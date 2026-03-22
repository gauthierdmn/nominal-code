from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from aiohttp import web

from nominal_code.commands.webhook.helpers import acknowledge_event, extract_mention
from nominal_code.config import Config, load_config
from nominal_code.config.policies import FilteringPolicy, RoutingPolicy
from nominal_code.config.settings import WebhookConfig
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.runner import build_runner
from nominal_code.models import COMMENT_EVENT_TYPES, BotType
from nominal_code.platforms import build_platforms
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    Platform,
    PullRequestEvent,
    ReviewerPlatform,
)

if TYPE_CHECKING:
    from nominal_code.jobs.runner.base import JobRunner

logger: logging.Logger = logging.getLogger(__name__)


async def run_webhook_server() -> None:
    """
    Async core: load config, build platforms, create app, and start server.
    """

    logger.info("Loading configuration from environment")

    try:
        config: Config = load_config()
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

    runner: JobRunner = build_runner(config=config, platforms=platforms)

    app: web.Application = create_app(
        config=config,
        platforms=platforms,
        runner=runner,
    )

    enabled: list[str] = list(platforms.keys())

    if config.webhook is None:
        raise ValueError("WebhookConfig is required but not configured")

    webhook: WebhookConfig = config.webhook

    bots: list[str] = []

    if webhook.routing.worker_bot_username:
        bots.append(f"worker=@{webhook.routing.worker_bot_username}")

    if webhook.routing.reviewer_bot_username:
        bots.append(f"reviewer=@{webhook.routing.reviewer_bot_username}")

    runner_mode: str = "kubernetes" if webhook.kubernetes is not None else "in-process"

    logger.info(
        "Starting server on %s:%d | platforms=%s | %s | runner=%s | allowed_users=%s",
        webhook.host,
        webhook.port,
        enabled,
        " | ".join(bots),
        runner_mode,
        webhook.filtering.allowed_users,
    )

    web_runner: web.AppRunner = web.AppRunner(app)

    await web_runner.setup()

    site: web.TCPSite = web.TCPSite(
        runner=web_runner,
        host=webhook.host,
        port=webhook.port,
    )

    try:
        await site.start()
        logger.info("Server is running, waiting for webhooks...")
        await asyncio.Event().wait()

    except asyncio.CancelledError:
        pass

    finally:
        logger.info("Shutting down...")
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


def should_process_event(
    event: PullRequestEvent,
    filtering: FilteringPolicy,
) -> bool:
    """
    Check whether an event passes the PR title tag filters.

    When both tag lists are empty, all events are processed (backward
    compatible). Exclude tags take priority over include tags.

    Args:
        event (PullRequestEvent): The parsed webhook event.
        filtering (FilteringPolicy): Filtering policy with title tag lists.

    Returns:
        bool: True if the event should be processed.
    """

    if not filtering.pr_title_include_tags and not filtering.pr_title_exclude_tags:
        return True

    title_lower: str = event.pr_title.lower()

    for tag in filtering.pr_title_exclude_tags:
        if f"[{tag}]" in title_lower:
            return False

    if filtering.pr_title_include_tags:
        for tag in filtering.pr_title_include_tags:
            if f"[{tag}]" in title_lower:
                return True

        return False

    return True


def filter_event(event: PullRequestEvent, filtering: FilteringPolicy) -> str | None:
    """
    Apply standard event filters and return a reason string if filtered.

    Checks ``allowed_repos`` and PR title tag filters. Returns ``None``
    when the event should proceed.

    Args:
        event (PullRequestEvent): The parsed webhook event.
        filtering (FilteringPolicy): Filtering policy.

    Returns:
        str | None: A filter reason (``"filtered"``) or ``None`` if the
            event passes all filters.
    """

    if filtering.allowed_repos and event.repo_full_name not in filtering.allowed_repos:
        logger.debug(
            "Ignoring event from repo %s (not in allowed repos)",
            event.repo_full_name,
        )

        return "filtered"

    if not should_process_event(event=event, filtering=filtering):
        return "filtered"

    return None


async def dispatch_lifecycle_event(
    event: PullRequestEvent,
    filtering: FilteringPolicy,
    routing: RoutingPolicy,
    platform: Platform,
    runner: JobRunner,
    namespace: str = "",
    extra_env: dict[str, str] | None = None,
) -> web.Response:
    """
    Dispatch a lifecycle event to the reviewer bot.

    Validates that a reviewer bot username is configured, the event is a
    lifecycle event, and the platform supports reviews. Acknowledges the
    event and enqueues a reviewer job.

    Args:
        event (PullRequestEvent): The parsed event.
        filtering (FilteringPolicy): Filtering policy (for user authorization).
        routing (RoutingPolicy): Routing policy.
        platform (Platform): The platform client.
        runner (JobRunner): Job runner for dispatching review jobs.
        namespace (str): Logical namespace for job isolation.
        extra_env (dict[str, str] | None): Additional env vars for the job container.

    Returns:
        web.Response: The HTTP response.
    """

    if not routing.reviewer_bot_username:
        return web.json_response({"status": "ignored"})

    if not isinstance(event, LifecycleEvent):
        return web.json_response({"status": "ignored"})

    if not isinstance(platform, ReviewerPlatform):
        return web.json_response({"status": "ignored"})

    await acknowledge_event(
        event=event,
        bot_type=BotType.REVIEWER,
        filtering=filtering,
        platform=platform,
    )

    job: JobPayload = JobPayload(
        event=event,
        bot_type=BotType.REVIEWER.value,
        namespace=namespace,
        extra_env=extra_env or {},
    )

    await runner.enqueue(job)

    return web.json_response({"status": "accepted"})


async def dispatch_comment_event(
    event: PullRequestEvent,
    filtering: FilteringPolicy,
    routing: RoutingPolicy,
    platform: Platform,
    runner: JobRunner,
    namespace: str = "",
    extra_env: dict[str, str] | None = None,
) -> web.Response:
    """
    Dispatch a comment event to the appropriate bot.

    Checks for @mentions of the worker and reviewer bots using the
    usernames from the routing policy. Authorizes the comment author
    against the filtering policy and enqueues a job. Worker mentions
    take precedence over reviewer mentions.

    Args:
        event (PullRequestEvent): The parsed event.
        filtering (FilteringPolicy): Filtering policy (for user authorization).
        routing (RoutingPolicy): Routing policy (for bot usernames).
        platform (Platform): The platform client.
        runner (JobRunner): Job runner for dispatching review jobs.
        namespace (str): Logical namespace for job isolation.
        extra_env (dict[str, str] | None): Additional env vars for the job container.

    Returns:
        web.Response: The HTTP response.
    """

    if event.event_type not in COMMENT_EVENT_TYPES:
        return web.json_response({"status": "ignored"})

    if not isinstance(event, CommentEvent):
        return web.json_response({"status": "ignored"})

    comment_event: CommentEvent = event

    worker_prompt: str | None = None
    reviewer_prompt: str | None = None

    if routing.worker_bot_username:
        worker_prompt = extract_mention(
            text=comment_event.body,
            bot_username=routing.worker_bot_username,
        )

    if routing.reviewer_bot_username:
        reviewer_prompt = extract_mention(
            text=comment_event.body,
            bot_username=routing.reviewer_bot_username,
        )

    if worker_prompt is not None:
        bot_type: BotType = BotType.WORKER
        mention_prompt: str = worker_prompt

    elif reviewer_prompt is not None and isinstance(platform, ReviewerPlatform):
        bot_type = BotType.REVIEWER
        mention_prompt = reviewer_prompt

    else:
        return web.json_response({"status": "no_mention"})

    proceed: bool = await acknowledge_event(
        event=comment_event,
        bot_type=bot_type,
        filtering=filtering,
        platform=platform,
    )

    if not proceed:
        return web.json_response({"status": "unauthorized"})

    mentioned_event: CommentEvent = replace(
        comment_event,
        mention_prompt=mention_prompt,
    )

    job: JobPayload = JobPayload(
        event=mentioned_event,
        bot_type=bot_type.value,
        namespace=namespace,
        extra_env=extra_env or {},
    )

    await runner.enqueue(job)

    return web.json_response({"status": "accepted"})


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

        if config.webhook is None:
            raise ValueError("WebhookConfig is required but not configured")

        webhook: WebhookConfig = config.webhook
        filtering: FilteringPolicy = webhook.filtering
        routing: RoutingPolicy = webhook.routing

        body: bytes = await request.read()

        if not platform.verify_webhook(request=request, body=body):
            logger.warning("Invalid webhook signature for %s", platform_name)

            return web.Response(status=401, text="Invalid signature")

        await platform.authenticate(webhook_body=body)

        event: CommentEvent | LifecycleEvent | None = platform.parse_event(
            request=request,
            body=body,
        )

        if event is None:
            return web.json_response({"status": "ignored"})

        filter_reason: str | None = filter_event(event=event, filtering=filtering)

        if filter_reason is not None:
            return web.json_response({"status": filter_reason})

        if event.event_type in routing.reviewer_triggers:
            return await dispatch_lifecycle_event(
                event=event,
                filtering=filtering,
                routing=routing,
                platform=platform,
                runner=runner,
            )

        return await dispatch_comment_event(
            event=event,
            filtering=filtering,
            routing=routing,
            platform=platform,
            runner=runner,
        )

    except Exception:
        logger.exception("Unhandled error in webhook handler for %s", platform_name)

        return web.json_response(
            {"status": "error", "message": "Internal server error"},
            status=500,
        )
