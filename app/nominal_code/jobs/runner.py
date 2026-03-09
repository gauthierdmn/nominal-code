from __future__ import annotations

from typing import Protocol

from nominal_code.jobs.payload import JobPayload


class JobRunner(Protocol):
    """
    Protocol for dispatching review jobs to a backend.

    Implementations may run jobs in-process, create Kubernetes Jobs,
    publish to a message queue, or use any other mechanism.
    """

    async def run(self, job: JobPayload) -> None:
        """
        Execute a review job.

        Args:
            job (JobPayload): The serialized review job to execute.
        """

        ...
