from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nominal_code.jobs.handler import JobHandler
from nominal_code.jobs.payload import JobPayload
from nominal_code.models import BotType
from nominal_code.platforms.base import CommentEvent, ReviewerPlatform
from nominal_code.workspace.setup import prepare_job_event

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.handlers.review import ReviewResult
    from nominal_code.platforms.base import Platform


logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    """
    Result of a job execution.

    Attributes:
        bot_type (BotType): The bot type that handled the job.
        review_result (ReviewResult | None): Review result with findings
            and cost data. Only populated for reviewer jobs.
    """

    bot_type: BotType
    review_result: ReviewResult | None = None


def extract_prompt(event: CommentEvent | object, bot_type: BotType) -> str:
    """
    Extract the user prompt from a prepared event.

    For worker jobs the event must be a ``CommentEvent``; a
    ``RuntimeError`` is raised otherwise. For reviewer jobs the
    mention prompt is returned when available, defaulting to an
    empty string for lifecycle events.

    Args:
        event (CommentEvent | object): The prepared event.
        bot_type (BotType): The bot type handling the job.

    Returns:
        str: The extracted prompt.

    Raises:
        RuntimeError: If ``bot_type`` is ``WORKER`` and the event
            is not a ``CommentEvent``.
    """

    if bot_type == BotType.WORKER:
        if not isinstance(event, CommentEvent):
            raise RuntimeError("Worker job requires a comment event")

        return event.mention_prompt or ""

    if isinstance(event, CommentEvent) and event.mention_prompt:
        return event.mention_prompt

    return ""


async def execute_job(
    job: JobPayload,
    platform: Platform,
    handler: JobHandler,
    config: Config,
    conversation_store: ConversationStore | None = None,
    pre_cloned: bool = False,
) -> JobResult:
    """
    Unified job execution pipeline.

    Authenticates the platform, validates capabilities, prepares the
    event, extracts the prompt, and dispatches to the appropriate
    handler method.

    Args:
        job (JobPayload): The deserialized job payload.
        platform (Platform): The platform client.
        handler (JobHandler): The handler to delegate execution to.
        config (Config): Application configuration.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.
        pre_cloned (bool): When True, the repository was pre-cloned by
            an external process and clone URL resolution is skipped.

    Returns:
        JobResult: The execution result.

    Raises:
        RuntimeError: If a reviewer job targets a platform that does
            not implement ``ReviewerPlatform``.
    """

    await platform.authenticate()

    bot_type: BotType = BotType(job.bot_type)

    if bot_type == BotType.REVIEWER and not isinstance(platform, ReviewerPlatform):
        raise RuntimeError(
            f"Platform {job.event.platform} does not support reviewer operations",
        )

    prepared_event = await prepare_job_event(
        event=job.event,
        bot_type=bot_type,
        platform=platform,
        pre_cloned=pre_cloned,
    )

    prompt: str = extract_prompt(prepared_event, bot_type)

    if bot_type == BotType.WORKER:
        assert isinstance(prepared_event, CommentEvent)

        await handler.handle_worker(
            event=prepared_event,
            prompt=prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
            namespace=job.namespace,
        )

        return JobResult(bot_type=bot_type)

    assert isinstance(platform, ReviewerPlatform)

    review_result: ReviewResult = await handler.handle_review(
        event=prepared_event,
        prompt=prompt,
        config=config,
        platform=platform,
        conversation_store=conversation_store,
        namespace=job.namespace,
    )

    return JobResult(bot_type=bot_type, review_result=review_result)
