from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aiohttp import web

from nominal_code.models import COMMENT_EVENT_TYPES, BotType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PullRequestEvent,
    ReviewerPlatform,
)
from nominal_code.webhooks.dispatch import run_pre_flight
from nominal_code.webhooks.mention import extract_mention

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.jobs.payload import ReviewJob
    from nominal_code.jobs.runner import JobRunner
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


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
        return await _handle_webhook(
            request=request,
            platform_name=platform_name,
        )

    return _handler


def _build_review_job(
    event: CommentEvent | LifecycleEvent,
    prompt: str,
    bot_type: BotType,
) -> ReviewJob:
    """
    Build a ReviewJob payload from a parsed webhook event.

    Args:
        event (CommentEvent | LifecycleEvent): The parsed event.
        prompt (str): The extracted prompt text.
        bot_type (BotType): Which bot personality to use.

    Returns:
        ReviewJob: The serializable job payload.
    """

    from nominal_code.jobs.payload import ReviewJob as ReviewJobClass

    if isinstance(event, CommentEvent):
        return ReviewJobClass(
            platform=event.platform,
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            pr_branch=event.pr_branch,
            pr_title=event.pr_title,
            event_type=event.event_type,
            is_comment_event=True,
            author_username=event.author_username,
            comment_body=event.body,
            comment_id=event.comment_id,
            diff_hunk=event.diff_hunk,
            file_path=event.file_path,
            discussion_id=event.discussion_id,
            prompt=prompt,
            pr_author="",
            bot_type=bot_type.value,
        )

    return ReviewJobClass(
        platform=event.platform,
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
        pr_branch=event.pr_branch,
        pr_title=event.pr_title,
        event_type=event.event_type,
        is_comment_event=False,
        author_username="",
        comment_body="",
        comment_id=0,
        diff_hunk="",
        file_path="",
        discussion_id="",
        prompt=prompt,
        pr_author=event.pr_author,
        bot_type=bot_type.value,
    )


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

        if not platform.verify_webhook(request, body):
            logger.warning("Invalid webhook signature for %s", platform_name)

            return web.Response(status=401, text="Invalid signature")

        event: CommentEvent | LifecycleEvent | None = platform.parse_event(
            request,
            body,
        )

        if event is None:
            return web.json_response({"status": "ignored"})

        if config.allowed_repos and event.repo_full_name not in config.allowed_repos:
            logger.debug(
                "Ignoring event from repo %s (not in ALLOWED_REPOS)",
                event.repo_full_name,
            )

            return web.json_response({"status": "filtered"})

        if not _should_process_event(event, config):
            return web.json_response({"status": "filtered"})

        if event.event_type in config.reviewer_triggers:
            if config.reviewer is None:
                return web.json_response({"status": "ignored"})

            if not isinstance(event, LifecycleEvent):
                return web.json_response({"status": "ignored"})

            if not isinstance(platform, ReviewerPlatform):
                return web.json_response({"status": "ignored"})

            proceed: bool = await run_pre_flight(
                event=event,
                bot_type=BotType.REVIEWER,
                config=config,
                platform=platform,
            )

            if not proceed:
                return web.json_response({"status": "unauthorized"})

            job: ReviewJob = _build_review_job(
                event=event,
                prompt="",
                bot_type=BotType.REVIEWER,
            )

            await runner.run(job)

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
                comment_event.body,
                config.worker.bot_username,
            )

        if config.reviewer is not None:
            reviewer_prompt = extract_mention(
                comment_event.body,
                config.reviewer.bot_username,
            )

        if worker_prompt is not None:
            bot_type: BotType = BotType.WORKER
            prompt: str = worker_prompt

        elif reviewer_prompt is not None and isinstance(platform, ReviewerPlatform):
            bot_type = BotType.REVIEWER
            prompt = reviewer_prompt

        else:
            return web.json_response({"status": "no_mention"})

        proceed = await run_pre_flight(
            event=comment_event,
            bot_type=bot_type,
            config=config,
            platform=platform,
        )

        if not proceed:
            return web.json_response({"status": "unauthorized"})

        job = _build_review_job(
            event=comment_event,
            prompt=prompt,
            bot_type=bot_type,
        )

        await runner.run(job)

        return web.json_response({"status": "accepted"})

    except Exception:
        logger.exception("Unhandled error in webhook handler for %s", platform_name)

        return web.json_response(
            {"status": "error", "message": "Internal server error"},
            status=500,
        )
