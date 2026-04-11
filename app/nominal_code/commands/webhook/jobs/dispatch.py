from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nominal_code.commands.webhook.jobs.handler import JobHandler
from nominal_code.commands.webhook.jobs.payload import JobPayload
from nominal_code.platforms.base import CommentEvent
from nominal_code.workspace.setup import prepare_job_event

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.platforms.base import Platform
    from nominal_code.review.reviewer import ReviewResult


logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobResult:
    """
    Result of a job execution.

    Attributes:
        review_result (ReviewResult | None): Review result with findings
            and cost data.
    """

    review_result: ReviewResult


def extract_prompt(event: CommentEvent | object) -> str:
    """
    Extract the user prompt from a prepared event.

    Returns the mention prompt when available, defaulting to an
    empty string for lifecycle events.

    Args:
        event (CommentEvent | object): The prepared event.

    Returns:
        str: The extracted prompt.
    """

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
    context: str = "",
) -> JobResult:
    """
    Execute a review job.

    Authenticates the platform, prepares the event, extracts the
    prompt, and dispatches to the handler.

    Args:
        job (JobPayload): The deserialized job payload.
        platform (Platform): The platform client.
        handler (JobHandler): The handler to delegate execution to.
        config (Config): Application configuration.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.
        pre_cloned (bool): When True, the repository was pre-cloned by
            an external process and clone URL resolution is skipped.
        context (str): Pre-review context to include in the user message.

    Returns:
        JobResult: The execution result.
    """

    await platform.authenticate()

    prepared_event = await prepare_job_event(
        event=job.event,
        platform=platform,
        pre_cloned=pre_cloned,
    )

    prompt: str = extract_prompt(prepared_event)

    review_result: ReviewResult = await handler.handle_review(
        event=prepared_event,
        prompt=prompt,
        config=config,
        platform=platform,
        conversation_store=conversation_store,
        namespace=job.namespace,
        context=context,
    )

    return JobResult(review_result=review_result)
