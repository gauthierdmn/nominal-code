from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from nominal_code.jobs.payload import JobPayload
from nominal_code.models import PRKey

logger: logging.Logger = logging.getLogger(__name__)


class AsyncioJobQueue:
    """
    Per-PR in-memory job queue ensuring serial execution within a conversation.

    When a job is enqueued for a PR key that has no active consumer,
    a new consumer task is spawned. The consumer pulls jobs one at a time,
    guaranteeing that concurrent comments in the same PR never race on the
    same conversation. Idle queues are cleaned up automatically.
    """

    def __init__(self) -> None:
        """
        Initialize an empty queue manager.
        """

        self._queues: dict[PRKey, asyncio.Queue[JobPayload]] = {}
        self._consumers: dict[PRKey, asyncio.Task[None]] = {}
        self._on_job: Callable[[JobPayload], Awaitable[None]] | None = None

    def set_job_callback(
        self,
        callback: Callable[[JobPayload], Awaitable[None]],
    ) -> None:
        """
        Register the callback invoked for each dequeued job.

        Args:
            callback (Callable[[JobPayload], Awaitable[None]]): An async
                callable that receives a ``JobPayload`` to process.
        """

        self._on_job = callback

    async def enqueue(self, job: JobPayload) -> None:
        """
        Enqueue a job for serial execution within its PR key.

        If no consumer task exists for the given PR key, one is
        spawned automatically.

        Args:
            job (JobPayload): The job payload to enqueue.
        """

        key: PRKey = (
            job.event.platform.value,
            job.event.repo_full_name,
            job.event.pr_number,
            job.bot_type,
            job.namespace,
        )

        if key not in self._queues:
            self._queues[key] = asyncio.Queue()

        await self._queues[key].put(job)

        if key not in self._consumers or self._consumers[key].done():
            self._consumers[key] = asyncio.create_task(self._consume(key))

    async def _consume(self, key: PRKey) -> None:
        """
        Consume jobs from the queue for a specific key.

        Processes all queued jobs, then exits. A new consumer is spawned
        if more jobs arrive after this one finishes.

        Args:
            key (PRKey): The (platform, repo, pr_number, bot_type) key.
        """

        queue: asyncio.Queue[JobPayload] = self._queues[key]

        while not queue.empty():
            job: JobPayload = await queue.get()

            try:
                if self._on_job is not None:
                    await self._on_job(job)
            except Exception:
                logger.exception("Job failed for key %s", key)
            finally:
                queue.task_done()

        self._queues.pop(key, None)
        self._consumers.pop(key, None)
