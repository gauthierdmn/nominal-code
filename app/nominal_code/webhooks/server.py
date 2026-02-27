from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from aiohttp import web

from nominal_code.models import COMMENT_EVENT_TYPES, BotType
from nominal_code.platforms.base import CommentEvent, LifecycleEvent
from nominal_code.webhooks.dispatch import enqueue_job
from nominal_code.webhooks.mention import extract_mention

if TYPE_CHECKING:
    from nominal_code.agent.session import SessionQueue, SessionStore
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform, ReviewerPlatform

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

    Dispatches auto-trigger lifecycle events and comment-based mentions.

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

    event: CommentEvent | LifecycleEvent | None = platform.parse_event(request, body)

    if event is None:
        return web.json_response({"status": "ignored"})

    await platform.ensure_auth()

    clone_url: str = platform.build_clone_url(event.repo_full_name)
    event = replace(event, clone_url=clone_url)

    if event.event_type in config.reviewer_triggers:
        if config.reviewer is None:
            return web.json_response({"status": "ignored"})

        if not isinstance(event, LifecycleEvent):
            return web.json_response({"status": "ignored"})

        lifecycle_event: LifecycleEvent = event

        async def _auto_trigger_job() -> None:
            from nominal_code.review.handler import review_and_post

            await platform.ensure_auth()

            await review_and_post(
                lifecycle_event,
                "",
                config,
                cast("ReviewerPlatform", platform),
                session_store,
            )

        await enqueue_job(
            event=lifecycle_event,
            bot_type=BotType.REVIEWER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=_auto_trigger_job,
        )

        return web.json_response({"status": "accepted"})

    if event.event_type not in COMMENT_EVENT_TYPES:
        return web.json_response({"status": "ignored"})

    if not isinstance(event, CommentEvent):
        return web.json_response({"status": "ignored"})

    comment_event: CommentEvent = event

    worker_prompt: str | None = None
    reviewer_prompt: str | None = None

    if config.worker is not None:
        worker_prompt = extract_mention(comment_event.body, config.worker.bot_username)

    if config.reviewer is not None:
        reviewer_prompt = extract_mention(
            comment_event.body, config.reviewer.bot_username
        )

    if worker_prompt is not None:
        bot_type: BotType = BotType.WORKER
        prompt: str = worker_prompt

        async def _job() -> None:
            from nominal_code.worker.handler import review_and_fix

            await platform.ensure_auth()

            await review_and_fix(
                comment_event,
                prompt,
                config,
                platform,
                session_store,
            )

    elif reviewer_prompt is not None:
        bot_type = BotType.REVIEWER
        prompt = reviewer_prompt

        async def _job() -> None:
            from nominal_code.review.handler import review_and_post

            await platform.ensure_auth()

            await review_and_post(
                comment_event,
                prompt,
                config,
                cast("ReviewerPlatform", platform),
                session_store,
            )

    else:
        return web.json_response({"status": "no_mention"})

    await enqueue_job(
        event=comment_event,
        bot_type=bot_type,
        config=config,
        platform=platform,
        session_queue=session_queue,
        job=_job,
    )

    return web.json_response({"status": "accepted"})
