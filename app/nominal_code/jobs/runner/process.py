from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nominal_code.agent.errors import handle_agent_errors
from nominal_code.handlers.review import run_and_post_review
from nominal_code.handlers.worker import review_and_fix
from nominal_code.jobs.payload import JobPayload
from nominal_code.models import BotType
from nominal_code.platforms.base import CommentEvent, ReviewerPlatform
from nominal_code.workspace.setup import prepare_job_event

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.jobs.queue.base import JobQueue
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


class ProcessRunner:
    """
    Runs review jobs in the current process.

    This is the default runner used when no external job backend is
    configured. It prepares the job event, dispatches to the appropriate
    handler, and posts results. Jobs are enqueued via the per-PR
    ``JobQueue`` for serial execution.

    Attributes:
        _config (Config): Application configuration.
        _platforms (dict[str, Platform]): Platform clients keyed by name.
        _conversation_store (ConversationStore): Conversation store.
        _queue (JobQueue): Per-PR job queue.
    """

    def __init__(
        self,
        config: Config,
        platforms: dict[str, Platform],
        conversation_store: ConversationStore,
        queue: JobQueue,
    ) -> None:
        """
        Initialize the process runner.

        Registers itself as the job callback on the queue so that
        dequeued jobs are processed via ``_execute``.

        Args:
            config (Config): Application configuration.
            platforms (dict[str, Platform]): Platform clients keyed by name.
            conversation_store (ConversationStore): Conversation store.
            queue (JobQueue): Per-PR job queue.
        """

        self._config = config
        self._platforms = platforms
        self._conversation_store = conversation_store
        self._queue = queue
        self._queue.set_job_callback(self._execute)

    async def enqueue(self, job: JobPayload) -> None:
        """
        Enqueue a review job for serial per-PR execution.

        The queue's consumer will call ``_execute`` for each
        dequeued job.

        Args:
            job (JobPayload): The review job to dispatch.
        """

        await self._queue.enqueue(job)

    async def _execute(self, job: JobPayload) -> None:
        """
        Execute a single dequeued job in the current process.

        Looks up the platform, prepares the job event, and dispatches
        to the appropriate handler.

        Args:
            job (JobPayload): The review job to execute.
        """

        platform: Platform = self._platforms[job.event.platform]

        await platform.authenticate()

        bot_type: BotType = BotType(job.bot_type)

        if bot_type == BotType.WORKER:
            agent_label: str = "worker"
        elif isinstance(platform, ReviewerPlatform):
            agent_label = "reviewer"
        else:
            logger.warning(
                "Platform %s does not support reviewer operations",
                job.event.platform,
            )

            return

        async with handle_agent_errors(
            event=job.event,
            platform=platform,
            agent_label=agent_label,
        ):
            prepared_event = await prepare_job_event(
                event=job.event,
                bot_type=bot_type,
                platform=platform,
            )

            if bot_type == BotType.WORKER:
                if not isinstance(prepared_event, CommentEvent):
                    raise RuntimeError("Worker job requires a comment event")

                await review_and_fix(
                    event=prepared_event,
                    prompt=prepared_event.mention_prompt or "",
                    config=self._config,
                    platform=platform,
                    conversation_store=self._conversation_store,
                    namespace=job.namespace,
                )
            elif isinstance(platform, ReviewerPlatform):
                mention_prompt: str = ""

                if (
                    isinstance(prepared_event, CommentEvent)
                    and prepared_event.mention_prompt
                ):
                    mention_prompt = prepared_event.mention_prompt

                await run_and_post_review(
                    event=prepared_event,
                    prompt=mention_prompt,
                    config=self._config,
                    platform=platform,
                    conversation_store=self._conversation_store,
                    namespace=job.namespace,
                )
