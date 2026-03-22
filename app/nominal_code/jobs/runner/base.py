from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from nominal_code.config.settings import DEFAULT_REDIS_KEY_TTL_SECONDS
from nominal_code.jobs.payload import JobPayload

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


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


def build_runner(config: Config, platforms: dict[str, Platform]) -> JobRunner:
    """
    Construct a JobRunner from the application configuration.

    Returns a ``KubernetesRunner`` when ``config.kubernetes`` is set, otherwise
    a ``ProcessRunner`` for in-process execution.

    Args:
        config (Config): Application configuration.
        platforms (dict[str, Platform]): Mapping of platform names to clients.

    Returns:
        JobRunner: The constructed runner instance.

    Raises:
        SystemExit: If ``REDIS_URL`` is missing when Kubernetes mode is enabled.
    """

    webhook = config.webhook
    kubernetes = webhook.kubernetes if webhook is not None else None
    redis = webhook.redis if webhook is not None else None

    if kubernetes is not None:
        redis_url: str = redis.url if redis is not None else ""

        if not redis_url:
            raise ValueError("REDIS_URL is required when kubernetes config is set")

        from nominal_code.jobs.queue.redis import RedisJobQueue
        from nominal_code.jobs.runner.kubernetes import KubernetesRunner

        redis_queue: RedisJobQueue = RedisJobQueue(redis_url)

        runner: JobRunner = KubernetesRunner(
            config=kubernetes,
            queue=redis_queue,
        )

        logger.info(
            "Using KubernetesRunner (image=%s, namespace=%s)",
            kubernetes.image,
            kubernetes.namespace,
        )

        return runner

    from nominal_code.conversation.base import build_conversation_store
    from nominal_code.jobs.queue.asyncio import AsyncioJobQueue
    from nominal_code.jobs.runner.process import ProcessRunner

    conversation_store = build_conversation_store(
        redis_url=redis.url if redis is not None else "",
        redis_key_ttl_seconds=(
            redis.key_ttl_seconds
            if redis is not None
            else DEFAULT_REDIS_KEY_TTL_SECONDS
        ),
    )
    job_queue: AsyncioJobQueue = AsyncioJobQueue()

    runner = ProcessRunner(
        config=config,
        platforms=platforms,
        conversation_store=conversation_store,
        queue=job_queue,
    )

    logger.info("Using ProcessRunner (in-process)")

    return runner
