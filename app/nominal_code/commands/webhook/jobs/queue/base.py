from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from nominal_code.commands.webhook.jobs.payload import JobPayload


class JobQueue(Protocol):
    """
    Protocol for per-PR job queues ensuring serial execution.

    Implementations accept ``JobPayload`` items and guarantee that
    jobs for the same PR key run one at a time. A callback registered
    via ``set_job_callback`` is invoked for each dequeued job.
    """

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

        ...

    async def enqueue(self, job: JobPayload) -> None:
        """
        Enqueue a job for serial per-PR execution.

        Args:
            job (JobPayload): The job payload to enqueue.
        """

        ...
