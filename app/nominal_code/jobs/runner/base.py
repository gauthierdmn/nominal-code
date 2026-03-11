from __future__ import annotations

from typing import Protocol

from nominal_code.jobs.payload import JobPayload


class JobRunner(Protocol):
    """
    Protocol for dispatching review jobs to a backend.

    Implementations may run jobs in-process, create Kubernetes Jobs,
    publish to a message queue, or use any other mechanism.
    """

    async def enqueue(self, job: JobPayload) -> None:
        """
        Enqueue a review job for execution.

        The job is placed onto a per-PR queue and processed
        asynchronously. This method returns immediately.

        Args:
            job (JobPayload): The review job to dispatch.
        """

        ...
