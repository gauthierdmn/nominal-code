from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.jobs.payload import ReviewJob
from nominal_code.models import BotType, EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PlatformName,
    ReviewerPlatform,
)

if TYPE_CHECKING:
    from nominal_code.agent.cli.job import JobQueue
    from nominal_code.agent.memory import ConversationStore
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


class InProcessRunner:
    """
    Runs review jobs in the current process.

    This is the default runner used when no external job backend is
    configured. It reconstructs events from the job payload and enqueues
    them via the per-PR ``JobQueue`` for serial execution.

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
        Initialize the in-process runner.

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

    async def run(self, job: ReviewJob) -> None:
        """
        Execute a review job in the current process via the job queue.

        Looks up the platform, reconstructs the event, builds the
        appropriate handler closure, and enqueues it for serial
        per-PR execution.

        Args:
            job (ReviewJob): The review job to execute.
        """

        platform: Platform = self._platforms[job.platform]

        await platform.ensure_auth()

        bot_type: BotType = BotType(job.bot_type)

        if job.is_comment_event:
            await self._run_comment_job(job, platform, bot_type)
        else:
            await self._run_lifecycle_job(job, platform, bot_type)

    async def _run_comment_job(
        self,
        job: ReviewJob,
        platform: Platform,
        bot_type: BotType,
    ) -> None:
        """
        Build and enqueue a comment-triggered job.

        Args:
            job (ReviewJob): The review job payload.
            platform (Platform): The platform client.
            bot_type (BotType): Which bot personality to use.
        """

        comment_event: CommentEvent = CommentEvent(
            platform=PlatformName(job.platform),
            repo_full_name=job.repo_full_name,
            pr_number=job.pr_number,
            pr_branch=job.pr_branch,
            pr_title=job.pr_title,
            event_type=EventType(job.event_type),
            comment_id=job.comment_id,
            author_username=job.author_username,
            body=job.comment_body,
            diff_hunk=job.diff_hunk,
            file_path=job.file_path,
            discussion_id=job.discussion_id,
        )

        if bot_type == BotType.WORKER:
            clone_url: str = platform.build_clone_url(job.repo_full_name)

            ready_event: CommentEvent = replace(
                comment_event,
                clone_url=clone_url,
            )

            async def _worker_job() -> None:
                from nominal_code.worker.handler import review_and_fix

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
                job=_worker_job,
            )
        else:
            if not isinstance(platform, ReviewerPlatform):
                logger.warning(
                    "Platform %s does not support reviewer operations",
                    job.platform,
                )

                return

            reviewer_platform: ReviewerPlatform = platform
            clone_url = reviewer_platform.build_reviewer_clone_url(
                job.repo_full_name,
            )

            ready_event = replace(comment_event, clone_url=clone_url)

            async def _reviewer_job() -> None:
                from nominal_code.review.handler import review_and_post

                await review_and_post(
                    event=ready_event,
                    prompt=job.prompt,
                    config=self._config,
                    platform=reviewer_platform,
                    conversation_store=self._conversation_store,
                )

            await self._job_queue.enqueue(
                platform=PlatformName(job.platform),
                repo=job.repo_full_name,
                pr_number=job.pr_number,
                bot_type=bot_type,
                job=_reviewer_job,
            )

    async def _run_lifecycle_job(
        self,
        job: ReviewJob,
        platform: Platform,
        bot_type: BotType,
    ) -> None:
        """
        Build and enqueue a lifecycle-triggered job.

        Args:
            job (ReviewJob): The review job payload.
            platform (Platform): The platform client.
            bot_type (BotType): Which bot personality to use.
        """

        if not isinstance(platform, ReviewerPlatform):
            logger.warning(
                "Platform %s does not support reviewer operations",
                job.platform,
            )

            return

        reviewer_platform: ReviewerPlatform = platform

        lifecycle_event: LifecycleEvent = LifecycleEvent(
            platform=PlatformName(job.platform),
            repo_full_name=job.repo_full_name,
            pr_number=job.pr_number,
            pr_branch=job.pr_branch,
            pr_title=job.pr_title,
            event_type=EventType(job.event_type),
            pr_author=job.pr_author,
            clone_url=reviewer_platform.build_clone_url(job.repo_full_name),
        )

        async def _lifecycle_job() -> None:
            from nominal_code.review.handler import review_and_post

            await review_and_post(
                event=lifecycle_event,
                prompt=job.prompt,
                config=self._config,
                platform=reviewer_platform,
                conversation_store=self._conversation_store,
            )

        await self._job_queue.enqueue(
            platform=PlatformName(job.platform),
            repo=job.repo_full_name,
            pr_number=job.pr_number,
            bot_type=bot_type,
            job=_lifecycle_job,
        )
