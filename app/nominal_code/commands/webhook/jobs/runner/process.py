from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nominal_code.agent.errors import handle_agent_errors
from nominal_code.commands.webhook.jobs.dispatch import execute_job
from nominal_code.commands.webhook.jobs.handler import DefaultJobHandler
from nominal_code.commands.webhook.jobs.payload import JobPayload

if TYPE_CHECKING:
    from nominal_code.commands.webhook.jobs.queue.base import JobQueue
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
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

        Looks up the platform, validates capabilities, and delegates
        to ``execute_job`` for unified dispatch.

        Args:
            job (JobPayload): The review job to execute.
        """

        platform: Platform = self._platforms[job.event.platform]

        handler: DefaultJobHandler = DefaultJobHandler()

        async with handle_agent_errors(
            event=job.event,
            platform=platform,
            agent_label="reviewer",
        ):
            await execute_job(
                job=job,
                platform=platform,
                handler=handler,
                config=self._config,
                conversation_store=self._conversation_store,
            )
