from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.handlers.review import review_and_post
from nominal_code.handlers.worker import review_and_fix
from nominal_code.jobs.payload import JobPayload
from nominal_code.models import BotType
from nominal_code.platforms.base import (
    CommentEvent,
    ReviewerPlatform,
)

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.jobs.runner import JobQueue
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
            await self._run_worker_job(job=job, platform=platform)
        elif isinstance(platform, ReviewerPlatform):
            await self._run_reviewer_job(job=job, platform=platform)
        else:
            logger.warning(
                "Platform %s does not support reviewer operations",
                job.event.platform,
            )

    async def _run_worker_job(
        self,
        job: JobPayload,
        platform: Platform,
    ) -> None:
        """
        Execute a worker job.

        Args:
            job (JobPayload): The job payload.
            platform (Platform): The platform client.
        """

        if not isinstance(job.event, CommentEvent):
            logger.warning("Worker job requires a comment event")

            return

        clone_url: str = platform.build_clone_url(
            repo_full_name=job.event.repo_full_name
        )
        ready_event: CommentEvent = replace(job.event, clone_url=clone_url)

        await review_and_fix(
            event=ready_event,
            prompt=ready_event.mention_prompt or "",
            config=self._config,
            platform=platform,
            conversation_store=self._conversation_store,
        )

    async def _run_reviewer_job(
        self,
        job: JobPayload,
        platform: ReviewerPlatform,
    ) -> None:
        """
        Execute a reviewer job.

        Args:
            job (JobPayload): The job payload.
            platform (ReviewerPlatform): The platform client.
        """

        clone_url: str = platform.build_reviewer_clone_url(
            repo_full_name=job.event.repo_full_name
        )
        ready_event = replace(job.event, clone_url=clone_url)

        mention_prompt: str = ""

        if isinstance(ready_event, CommentEvent) and ready_event.mention_prompt:
            mention_prompt = ready_event.mention_prompt

        await review_and_post(
            event=ready_event,
            prompt=mention_prompt,
            config=self._config,
            platform=platform,
            conversation_store=self._conversation_store,
        )
