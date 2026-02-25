from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aiohttp import web

from nominal_code.bot_type import BotType
from nominal_code.handlers import handle_comment
from nominal_code.mention import extract_mention
from nominal_code.platforms.base import ReviewComment

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform
    from nominal_code.session import SessionQueue, SessionStore

logger: logging.Logger = logging.getLogger(__name__)


def create_app(
    config: Config,
    platforms: dict[str, Platform],
    session_store: SessionStore,
    session_queue: SessionQueue,
) -> web.Application:
    """
    Create the aiohttp web application with webhook routes.

    Args:
        config (Config): Application configuration.
        platforms (dict[str, Platform]): Mapping of platform names to clients.
        session_store (SessionStore): Claude session store.
        session_queue (SessionQueue): Per-PR job queue.

    Returns:
        web.Application: The configured aiohttp application.
    """

    app: web.Application = web.Application()

    app["config"] = config
    app["platforms"] = platforms
    app["session_store"] = session_store
    app["session_queue"] = session_queue

    app.router.add_get("/health", _handle_health)

    for platform_name in platforms:
        handler: Callable[
            [web.Request],
            Awaitable[web.Response],
        ] = _make_webhook_handler(platform_name)
        app.router.add_post(f"/webhooks/{platform_name}", handler)

    return app


async def _handle_health(request: web.Request) -> web.Response:
    """
    Health check endpoint.

    Args:
        request (web.Request): The incoming request.

    Returns:
        web.Response: A 200 OK response with status JSON.
    """

    return web.json_response({"status": "ok"})


def _make_webhook_handler(
    platform_name: str,
) -> Callable[[web.Request], Awaitable[web.Response]]:
    """
    Create a webhook handler bound to a specific platform name.

    Args:
        platform_name (str): The platform identifier used as a dict key.

    Returns:
        Callable[[web.Request], Awaitable[web.Response]]: The handler coroutine.
    """

    async def _handler(request: web.Request) -> web.Response:
        return await _handle_webhook(request, platform_name)

    return _handler


async def _handle_webhook(
    request: web.Request,
    platform_name: str,
) -> web.Response:
    """
    Common webhook handler for all platforms.

    Checks configured bot usernames and dispatches accordingly.

    Args:
        request (web.Request): The incoming webhook request.
        platform_name (str): The platform identifier.

    Returns:
        web.Response: The HTTP response.
    """

    config: Config = request.app["config"]
    platform: Platform = request.app["platforms"][platform_name]
    session_store: SessionStore = request.app["session_store"]
    session_queue: SessionQueue = request.app["session_queue"]

    body: bytes = await request.read()

    if not platform.verify_webhook(request, body):
        logger.warning("Invalid webhook signature for %s", platform_name)

        return web.Response(status=401, text="Invalid signature")

    comment: ReviewComment | None = platform.parse_webhook(request, body)

    if comment is None:
        return web.json_response({"status": "ignored"})

    worker_prompt: str | None = None
    reviewer_prompt: str | None = None

    if config.worker is not None:
        worker_prompt = extract_mention(comment.body, config.worker.bot_username)

    if config.reviewer is not None:
        reviewer_prompt = extract_mention(comment.body, config.reviewer.bot_username)

    if worker_prompt is not None:
        bot_type: BotType = BotType.WORKER
        prompt: str = worker_prompt
    elif reviewer_prompt is not None:
        bot_type = BotType.REVIEWER
        prompt = reviewer_prompt
    else:
        return web.json_response({"status": "no_mention"})

    await handle_comment(
        comment=comment,
        prompt=prompt,
        config=config,
        platform=platform,
        session_store=session_store,
        session_queue=session_queue,
        bot_type=bot_type,
    )

    return web.json_response({"status": "accepted"})
