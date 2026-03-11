from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import web
from environs import Env

from nominal_code.commands.webhook.helpers import acknowledge_event, extract_mention
from nominal_code.config import Config
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.queue.asyncio import AsyncioJobQueue
from nominal_code.jobs.runner.process import ProcessRunner
from nominal_code.models import COMMENT_EVENT_TYPES, BotType
from nominal_code.platforms import build_platforms
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    Platform,
    PullRequestEvent,
    ReviewerPlatform,
)
from nominal_code.workspace.cleanup import WorkspaceCleaner

if TYPE_CHECKING:
    from nominal_code.jobs.runner.base import JobRunner

logger: logging.Logger = logging.getLogger(__name__)
env: Env = Env()


async def run_webhook_server() -> None:
    """
    Async core: load config, build platforms, create app, and start server.
    """

    logger.info("Loading configuration from environment")

    try:
        config: Config = Config.from_env()
    except (OSError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    platforms: dict[str, Platform] = build_platforms()

    if not platforms:
        logger.error(
            "No platforms configured. "
            "Set GITHUB_TOKEN or GITLAB_TOKEN to enable a platform."
        )
        sys.exit(1)

    runner: JobRunner

    if config.kubernetes is not None:
        redis_url: str = env.str("REDIS_URL", "")

        if not redis_url:
            logger.error("REDIS_URL is required when JOB_RUNNER=kubernetes")
            sys.exit(1)

        # import in function as this is an optional dependency
        from nominal_code.jobs.queue.redis import RedisJobQueue
        from nominal_code.jobs.runner.kubernetes import KubernetesRunner

        redis_queue: RedisJobQueue = RedisJobQueue(redis_url)

        runner = KubernetesRunner(
            config=config.kubernetes,
            queue=redis_queue,
        )
    else:
        conversation_store: MemoryConversationStore = MemoryConversationStore()
        job_queue: AsyncioJobQueue = AsyncioJobQueue()

        runner = ProcessRunner(
            config=config,
            platforms=platforms,
            conversation_store=conversation_store,
            queue=job_queue,
        )

    app: web.Application = create_app(
        config=config,
        platforms=platforms,
        runner=runner,
    )

    enabled: list[str] = list(platforms.keys())

    bots: list[str] = []

    if config.worker is not None:
        bots.append(f"worker=@{config.worker.bot_username}")

    if config.reviewer is not None:
        bots.append(f"reviewer=@{config.reviewer.bot_username}")

    runner_mode: str = "kubernetes" if config.kubernetes is not None else "in-process"

    logger.info(
        "Starting server on %s:%d | platforms=%s | %s | runner=%s | allowed_users=%s",
        config.webhook_host,
        config.webhook_port,
        enabled,
        " | ".join(bots),
        runner_mode,
        config.allowed_users,
    )

    cleaner: WorkspaceCleaner | None = None

    if config.cleanup_interval_hours > 0:
        cleaner = WorkspaceCleaner(
            base_dir=config.workspace_base_dir,
            platforms=platforms,
            cleanup_wait=timedelta(hours=config.cleanup_interval_hours),
        )

    web_runner: web.AppRunner = web.AppRunner(app)

    await web_runner.setup()

    site: web.TCPSite = web.TCPSite(
        runner=web_runner,
        host=config.webhook_host,
        port=config.webhook_port,
    )

    try:
        if cleaner is not None:
            cleaner.purge()
            await cleaner.start()

        await site.start()
        logger.info("Server is running, waiting for webhooks...")
        await asyncio.Event().wait()

    except asyncio.CancelledError:
        pass

    finally:
        logger.info("Shutting down...")

        if cleaner is not None:
            await cleaner.stop()

        await web_runner.cleanup()


def create_app(
    config: Config,
    platforms: dict[str, Platform],
    runner: JobRunner,
) -> web.Application:
    """
    Create the aiohttp web application with webhook routes.

    Args:
        config (Config): Application configuration.
        platforms (dict[str, Platform]): Mapping of platform names to clients.
        runner (JobRunner): Job runner for dispatching review jobs.

    Returns:
        web.Application: The configured aiohttp application.
    """

    app: web.Application = web.Application(client_max_size=5 * 1024 * 1024)

    app["config"] = config
    app["platforms"] = platforms
    app["runner"] = runner

    app.router.add_get(path="/health", handler=_handle_health)

    for platform_name in platforms:
        handler: Callable[
            [web.Request],
            Awaitable[web.Response],
        ] = _make_webhook_handler(platform_name)
        app.router.add_post(path=f"/webhooks/{platform_name}", handler=handler)

    return app


def _should_process_event(event: PullRequestEvent, config: Config) -> bool:
    """
    Check whether an event passes the PR title tag filters.

    When both tag lists are empty, all events are processed (backward
    compatible). Exclude tags take priority over include tags.

    Args:
        event (PullRequestEvent): The parsed webhook event.
        config (Config): Application configuration with tag filter lists.

    Returns:
        bool: True if the event should be processed.
    """

    if not config.pr_title_include_tags and not config.pr_title_exclude_tags:
        return True

    title_lower: str = event.pr_title.lower()

    for tag in config.pr_title_exclude_tags:
        if f"[{tag}]" in title_lower:
            return False

    if config.pr_title_include_tags:
        for tag in config.pr_title_include_tags:
            if f"[{tag}]" in title_lower:
                return True

        return False

    return True


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
        return await _handle_webhook(
            request=request,
            platform_name=platform_name,
        )

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

    try:
        config: Config = request.app["config"]
        platform: Platform = request.app["platforms"][platform_name]
        runner: JobRunner = request.app["runner"]

        body: bytes = await request.read()

        if not platform.verify_webhook(request=request, body=body):
            logger.warning("Invalid webhook signature for %s", platform_name)

            return web.Response(status=401, text="Invalid signature")

        event: CommentEvent | LifecycleEvent | None = platform.parse_event(
            request=request,
            body=body,
        )

        if event is None:
            return web.json_response({"status": "ignored"})

        if config.allowed_repos and event.repo_full_name not in config.allowed_repos:
            logger.debug(
                "Ignoring event from repo %s (not in ALLOWED_REPOS)",
                event.repo_full_name,
            )

            return web.json_response({"status": "filtered"})

        if not _should_process_event(event=event, config=config):
            return web.json_response({"status": "filtered"})

        if event.event_type in config.reviewer_triggers:
            if config.reviewer is None:
                return web.json_response({"status": "ignored"})

            if not isinstance(event, LifecycleEvent):
                return web.json_response({"status": "ignored"})

            if not isinstance(platform, ReviewerPlatform):
                return web.json_response({"status": "ignored"})

            await acknowledge_event(
                event=event,
                bot_type=BotType.REVIEWER,
                config=config,
                platform=platform,
            )

            job: JobPayload = JobPayload(
                event=event,
                bot_type=BotType.REVIEWER.value,
            )

            await runner.enqueue(job)

            return web.json_response({"status": "accepted"})

        if event.event_type not in COMMENT_EVENT_TYPES:
            return web.json_response({"status": "ignored"})

        if not isinstance(event, CommentEvent):
            return web.json_response({"status": "ignored"})

        comment_event: CommentEvent = event

        worker_prompt: str | None = None
        reviewer_prompt: str | None = None

        if config.worker is not None:
            worker_prompt = extract_mention(
                text=comment_event.body,
                bot_username=config.worker.bot_username,
            )

        if config.reviewer is not None:
            reviewer_prompt = extract_mention(
                text=comment_event.body,
                bot_username=config.reviewer.bot_username,
            )

        if worker_prompt is not None:
            bot_type: BotType = BotType.WORKER
            mention_prompt: str = worker_prompt

        elif reviewer_prompt is not None and isinstance(platform, ReviewerPlatform):
            bot_type = BotType.REVIEWER
            mention_prompt = reviewer_prompt

        else:
            return web.json_response({"status": "no_mention"})

        proceed = await acknowledge_event(
            event=comment_event,
            bot_type=bot_type,
            config=config,
            platform=platform,
        )

        if not proceed:
            return web.json_response({"status": "unauthorized"})

        mentioned_event: CommentEvent = replace(
            comment_event,
            mention_prompt=mention_prompt,
        )

        job = JobPayload(
            event=mentioned_event,
            bot_type=bot_type.value,
        )

        await runner.enqueue(job)

        return web.json_response({"status": "accepted"})

    except Exception:
        logger.exception("Unhandled error in webhook handler for %s", platform_name)

        return web.json_response(
            {"status": "error", "message": "Internal server error"},
            status=500,
        )
