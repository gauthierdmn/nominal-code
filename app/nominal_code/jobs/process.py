from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.jobs.payload import JobPayload
from nominal_code.models import BotType
from nominal_code.platforms.base import (
    CommentEvent,
    PlatformName,
    ReviewerPlatform,
)

if TYPE_CHECKING:
    from nominal_code.agent.cli.queue import JobQueue
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


class ProcessRunner:
    """
    Runs review jobs in the current process.

    This is the default runner used when no external job backend is
    configured. It sets clone URLs on the event and enqueues jobs
    via the per-PR ``JobQueue`` for serial execution.

    Attributes:
        _config (Config): Application configuration.
        _platforms (dict[str, Platform]): Platform clients keyed by name.
        _conversation_store (ConversationStore): Conversation store.
        _job_queue (JobQueue): Per-PR job queue.
    """

    def __init__(
        self,
        config: Config,
        platforms: dict[str, Platform],
        conversation_store: ConversationStore,
        job_queue: JobQueue,
    ) -> None:
        """
        Initialize the process runner.

        Args:
            config (Config): Application configuration.
            platforms (dict[str, Platform]): Platform clients keyed by name.
            conversation_store (ConversationStore): Conversation store.
            job_queue (JobQueue): Per-PR job queue.
        """

        self._config = config
        self._platforms = platforms
        self._conversation_store = conversation_store
        self._job_queue = job_queue

    async def run(self, job: JobPayload) -> None:
        """
        Execute a review job in the current process via the job queue.

        Looks up the platform, sets the clone URL on the event, builds
        the appropriate handler closure, and enqueues it for serial
        per-PR execution.

        Args:
            job (JobPayload): The review job to execute.
        """

        platform: Platform = self._platforms[job.platform]

        await platform.ensure_auth()

        bot_type: BotType = BotType(job.bot_type)

        if bot_type == BotType.WORKER:
            await self._run_worker_job(job, platform, bot_type)
        elif isinstance(platform, ReviewerPlatform):
            await self._run_reviewer_job(job, platform, bot_type)
        else:
            logger.warning(
                "Platform %s does not support reviewer operations",
                job.platform,
            )

    async def _run_worker_job(
        self,
        job: JobPayload,
        platform: Platform,
        bot_type: BotType,
    ) -> None:
        """
        Build and enqueue a worker job.

        Args:
            job (JobPayload): The job payload.
            platform (Platform): The platform client.
            bot_type (BotType): The bot type.
        """

        if not isinstance(job.event, CommentEvent):
            logger.warning("Worker job requires a comment event")

            return

        clone_url: str = platform.build_clone_url(job.repo_full_name)
        ready_event: CommentEvent = replace(job.event, clone_url=clone_url)

        async def _worker_closure() -> None:
            from nominal_code.handlers.worker import review_and_fix

            await review_and_fix(
                event=ready_event,
                prompt=job.prompt,
                config=self._config,
                platform=platform,
                conversation_store=self._conversation_store,
            )

        await self._job_queue.enqueue(
            platform=PlatformName(job.platform),
            repo=job.repo_full_name,
            pr_number=job.pr_number,
            bot_type=bot_type,
            job=_worker_closure,
        )

    async def _run_reviewer_job(
        self,
        job: JobPayload,
        platform: ReviewerPlatform,
        bot_type: BotType,
    ) -> None:
        """
        Build and enqueue a reviewer job.

        Args:
            job (JobPayload): The job payload.
            platform (ReviewerPlatform): The platform client.
            bot_type (BotType): The bot type.
        """

        clone_url: str = platform.build_reviewer_clone_url(job.repo_full_name)
        ready_event = replace(job.event, clone_url=clone_url)

        async def _reviewer_closure() -> None:
            from nominal_code.handlers.review import review_and_post

            await review_and_post(
                event=ready_event,
                prompt=job.prompt,
                config=self._config,
                platform=platform,
                conversation_store=self._conversation_store,
            )

        await self._job_queue.enqueue(
            platform=PlatformName(job.platform),
            repo=job.repo_full_name,
            pr_number=job.pr_number,
            bot_type=bot_type,
            job=_reviewer_closure,
        )
