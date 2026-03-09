from __future__ import annotations

import logging

import redis

logger: logging.Logger = logging.getLogger(__name__)

JOB_CHANNEL_PREFIX: str = "nc:job"


def publish_job_completion(
    redis_url: str,
    job_name: str,
    status: str,
) -> None:
    """
    Publish a job completion signal to Redis pub/sub.

    Called from the K8s Job pod at the end of ``run_job_main()`` to
    notify the server that the job has finished.

    Args:
        redis_url (str): Redis connection URL.
        job_name (str): The Kubernetes Job name.
        status (str): Completion status (``"succeeded"`` or ``"failed"``).
    """

    channel: str = f"{JOB_CHANNEL_PREFIX}:{job_name}:done"

    try:
        client: redis.Redis = redis.Redis.from_url(redis_url)

        try:
            client.publish(channel, status)
            logger.info("Published completion for job %s: %s", job_name, status)
        finally:
            client.close()
    except redis.RedisError:
        logger.warning(
            "Failed to publish completion for job %s",
            job_name,
            exc_info=True,
        )
