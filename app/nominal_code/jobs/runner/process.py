from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nominal_code.agent.errors import handle_agent_errors
from nominal_code.handlers.review import post_review_result
from nominal_code.jobs.execute import execute_job
from nominal_code.jobs.payload import JobPayload
from nominal_code.models import BotType
from nominal_code.platforms.base import ReviewerPlatform

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
    configured. It sets clone URLs on the event, builds the appropriate
    handler closure, and executes it. Jobs are enqueued via the per-PR
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

        Looks up the platform, sets the clone URL on the event, and
        dispatches to the appropriate handler.

        Args:
            job (JobPayload): The review job to execute.
        """

        platform: Platform = self._platforms[job.event.platform]

        await platform.ensure_auth()

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

        reviewer_platform: ReviewerPlatform | None = (
            platform if isinstance(platform, ReviewerPlatform) else None
        )

        async with handle_agent_errors(
            event=job.event,
            platform=platform,
            agent_label=agent_label,
        ):
            result = await execute_job(
                job=job,
                config=self._config,
                platform=platform,
                conversation_store=self._conversation_store,
            )

            if result is not None and reviewer_platform is not None:
                await post_review_result(
                    event=job.event,
                    result=result,
                    platform=reviewer_platform,
                )
