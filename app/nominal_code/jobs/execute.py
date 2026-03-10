from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.handlers.review import ReviewResult, review
from nominal_code.handlers.worker import review_and_fix
from nominal_code.models import BotType
from nominal_code.platforms.base import CommentEvent, ReviewerPlatform
from nominal_code.workspace.setup import resolve_branch

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.jobs.payload import JobPayload
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


async def execute_job(
    job: JobPayload,
    config: Config,
    platform: Platform,
    conversation_store: ConversationStore | None = None,
) -> ReviewResult | None:
    """
    Execute a job by routing to the appropriate handler.

    Routes by ``bot_type``: reviewer jobs return a ``ReviewResult``,
    worker jobs return ``None``. Does NOT handle errors — callers
    wrap with their own strategy.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (Platform): The platform client.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.

    Returns:
        ReviewResult | None: The review result for reviewer jobs,
            ``None`` for worker jobs.

    Raises:
        RuntimeError: If the event type or platform is incompatible
            with the requested bot type, or if branch resolution fails.
    """

    bot_type: BotType = BotType(job.bot_type)

    if bot_type == BotType.WORKER:
        await _dispatch_worker_job(
            job=job,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )

        return None

    if not isinstance(platform, ReviewerPlatform):
        raise RuntimeError(
            f"Platform {job.event.platform} does not support reviewer operations",
        )

    return await _dispatch_reviewer_job(
        job=job,
        config=config,
        platform=platform,
        conversation_store=conversation_store,
    )


async def _dispatch_reviewer_job(
    job: JobPayload,
    config: Config,
    platform: ReviewerPlatform,
    conversation_store: ConversationStore | None = None,
) -> ReviewResult:
    """
    Prepare and execute a reviewer job.

    Resolves the clone URL, extracts the mention prompt, resolves the
    branch, and delegates to the review handler. Returns the result
    without posting it.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.

    Returns:
        ReviewResult: The review result with findings and summary.

    Raises:
        RuntimeError: If branch resolution fails.
    """

    clone_url: str = platform.build_reviewer_clone_url(
        repo_full_name=job.event.repo_full_name,
    )
    event = replace(job.event, clone_url=clone_url)

    resolved_event = await resolve_branch(event=event, platform=platform)

    if resolved_event is None:
        raise RuntimeError(
            f"Cannot resolve branch for "
            f"{job.event.repo_full_name}#{job.event.pr_number}"
        )

    mention_prompt: str = ""

    if isinstance(resolved_event, CommentEvent) and resolved_event.mention_prompt:
        mention_prompt = resolved_event.mention_prompt

    return await review(
        event=resolved_event,
        prompt=mention_prompt,
        config=config,
        platform=platform,
        conversation_store=conversation_store,
    )


async def _dispatch_worker_job(
    job: JobPayload,
    config: Config,
    platform: Platform,
    conversation_store: ConversationStore | None = None,
) -> None:
    """
    Prepare and execute a worker job.

    Validates the event type, resolves the clone URL and branch, and
    delegates to the worker handler which posts the reply internally.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (Platform): The platform client.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.

    Raises:
        RuntimeError: If the event is not a CommentEvent or branch
            resolution fails.
    """

    if not isinstance(job.event, CommentEvent):
        raise RuntimeError("Worker job requires a comment event")

    clone_url: str = platform.build_clone_url(
        repo_full_name=job.event.repo_full_name,
    )
    comment_event: CommentEvent = replace(job.event, clone_url=clone_url)

    resolved_event = await resolve_branch(event=comment_event, platform=platform)

    if resolved_event is None:
        raise RuntimeError(
            f"Cannot resolve branch for "
            f"{job.event.repo_full_name}#{job.event.pr_number}"
        )

    await review_and_fix(
        event=resolved_event,
        prompt=comment_event.mention_prompt or "",
        config=config,
        platform=platform,
        conversation_store=conversation_store,
    )
