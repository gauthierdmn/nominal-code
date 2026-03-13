from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from nominal_code.jobs.payload import JobPayload

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger: logging.Logger = logging.getLogger(__name__)

QUEUE_KEY_PREFIX: str = "nc:queue"
JOB_CHANNEL_PREFIX: str = "nc:job"
BRPOP_TIMEOUT_SECONDS: int = 5


class RedisJobQueue:
    """
    Redis-backed per-PR job queue for Kubernetes deployments.

    Uses Redis lists for per-PR serial job execution and Redis pub/sub
    for event-driven job completion notification. Each unique PR key
    gets its own consumer task that processes jobs sequentially.

    Attributes:
        _redis (aioredis.Redis): The async Redis client.
        _consumers (dict[str, asyncio.Task[None]]): Active consumer
            tasks keyed by Redis list key.
        _on_job (Callable[[JobPayload], Awaitable[None]] | None):
            Callback invoked for each dequeued job.
    """

    def __init__(self, redis_url: str) -> None:
        """
        Initialize the Redis job queue.

        Args:
            redis_url (str): Redis connection URL.
        """

        import redis.asyncio as _aioredis

        self._redis: aioredis.Redis = _aioredis.from_url(redis_url)
        self._consumers: dict[str, asyncio.Task[None]] = {}
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

        Pushes the serialized payload onto a Redis list keyed by the
        PR identifier. If no consumer task exists for this key, one
        is spawned automatically.

        Args:
            job (JobPayload): The job payload to enqueue.
        """

        key: str = _build_queue_key(job)
        payload: str = job.serialize()

        await self._redis.lpush(key, payload)  # type: ignore[misc]

        if key not in self._consumers or self._consumers[key].done():
            self._consumers[key] = asyncio.create_task(self._consume(key))

    async def _consume(self, key: str) -> None:
        """
        Consume jobs from the Redis list for a specific PR key.

        Loops with ``BRPOP`` until the list is empty (timeout fires).
        For each job, calls the registered callback. Cleans up the
        consumer task reference when done.

        Args:
            key (str): The Redis list key to consume from.
        """

        try:
            while True:
                result: Any = await self._redis.brpop(  # type: ignore[misc]
                    [key],
                    timeout=BRPOP_TIMEOUT_SECONDS,
                )

                if result is None:
                    break

                raw_payload: str = result[1].decode("utf-8")

                try:
                    job: JobPayload = JobPayload.deserialize(raw_payload)
                except (TypeError, ValueError, KeyError) as exc:
                    logger.error("Failed to deserialize queued job: %s", exc)

                    continue

                if self._on_job is not None:
                    try:
                        await self._on_job(job)
                    except Exception:
                        logger.exception("Job failed for key %s", key)
        finally:
            self._consumers.pop(key, None)

    async def await_job_completion(
        self,
        channel_key: str,
        timeout_seconds: float,
    ) -> str:
        """
        Wait for a K8s Job to publish its completion signal via pub/sub.

        Subscribes to the job-specific Redis channel and waits for a
        message containing the completion status.

        Args:
            channel_key (str): The Redis pub/sub channel key.
            timeout_seconds (float): Maximum seconds to wait.

        Returns:
            str: The completion status (``"succeeded"`` or ``"failed"``).

        Raises:
            TimeoutError: If no completion signal arrives within the timeout.
        """

        pubsub: aioredis.client.PubSub = self._redis.pubsub()

        try:
            await pubsub.subscribe(channel_key)

            deadline: float = asyncio.get_event_loop().time() + timeout_seconds

            while True:
                remaining: float = deadline - asyncio.get_event_loop().time()

                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for job on {channel_key} after "
                        f"{timeout_seconds}s",
                    )

                message: dict[str, object] | None = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=min(remaining, 1.0),
                )

                if message is not None and message.get("type") == "message":
                    data: object = message["data"]

                    if isinstance(data, bytes):
                        return data.decode("utf-8")

                    return str(data)
        finally:
            await pubsub.unsubscribe(channel_key)
            await pubsub.close()

    async def close(self) -> None:
        """
        Close the Redis connection and cancel active consumers.
        """

        for task in self._consumers.values():
            task.cancel()

        await self._redis.close()


def _build_queue_key(job: JobPayload) -> str:
    """
    Build the Redis list key for a job's PR.

    Uses ``job.namespace`` to scope the key when set.

    Args:
        job (JobPayload): The job payload.

    Returns:
        str: The Redis key in the format
            ``nc:queue:{platform}:{repo}:{pr_number}:{bot_type}`` or
            ``nc:{namespace}:queue:{platform}:{repo}:{pr_number}:{bot_type}``.
    """

    base: str = QUEUE_KEY_PREFIX

    if job.namespace:
        base = f"nc:{job.namespace}:queue"

    return (
        f"{base}:{job.event.platform}:"
        f"{job.event.repo_full_name}:{job.event.pr_number}:{job.bot_type}"
    )
