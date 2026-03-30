from __future__ import annotations

import logging

from environs import Env

from nominal_code.config import Config, load_config_for_ci, resolve_provider_config
from nominal_code.config.settings import DEFAULT_REDIS_KEY_TTL_SECONDS, RedisConfig
from nominal_code.conversation.base import ConversationStore, build_conversation_store
from nominal_code.jobs.dispatch import JobResult, execute_job
from nominal_code.jobs.handler import DefaultJobHandler, JobHandler
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.runner.kubernetes import (
    build_job_channel_key,
    publish_job_completion,
)
from nominal_code.llm.cost import format_cost_summary
from nominal_code.models import BotType
from nominal_code.platforms import load_platform_ci
from nominal_code.platforms.base import PlatformName, ReviewerPlatform

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)


async def run_job_main() -> int:
    """
    Entry point for the ``run-job`` CLI subcommand.

    Reads the ``REVIEW_JOB_PAYLOAD`` environment variable, deserializes
    the job, constructs the platform client, runs the review using the
    LLM provider API, and posts results.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    payload: str = _env.str("REVIEW_JOB_PAYLOAD", "")

    if not payload:
        logger.error("REVIEW_JOB_PAYLOAD environment variable is not set")

        return 1

    try:
        job: JobPayload = JobPayload.deserialize(payload)
    except (TypeError, ValueError, KeyError) as exc:
        logger.error("Failed to deserialize job payload: %s", exc)

        return 1

    try:
        config: Config = _build_job_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    redis: RedisConfig = _build_redis_config()

    conversation_store: ConversationStore = build_conversation_store(
        redis_url=redis.url,
        redis_key_ttl_seconds=redis.key_ttl_seconds,
    )

    platform_name: PlatformName = PlatformName(job.event.platform)
    platform: ReviewerPlatform = _build_platform(platform_name)
    handler: JobHandler = DefaultJobHandler()
    pre_cloned: bool = not job.event.clone_url

    exit_code: int = await _run_job(
        job=job,
        config=config,
        platform=platform,
        handler=handler,
        conversation_store=conversation_store,
        pre_cloned=pre_cloned,
    )

    _publish_completion(exit_code=exit_code, job=job, redis=redis)

    return exit_code


async def _run_job(
    job: JobPayload,
    config: Config,
    platform: ReviewerPlatform,
    handler: JobHandler,
    conversation_store: ConversationStore | None = None,
    pre_cloned: bool = False,
) -> int:
    """
    Execute a job via the unified dispatch pipeline.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.
        handler (JobHandler): The job handler to delegate execution to.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.
        pre_cloned (bool): When True, the repository was pre-cloned
            externally and clone URL resolution is skipped.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    bot_type: BotType = BotType(job.bot_type)

    logger.info(
        "Running %s job for %s#%d on %s",
        bot_type.value,
        job.event.repo_full_name,
        job.event.pr_number,
        job.event.platform,
    )

    try:
        result: JobResult = await execute_job(
            job=job,
            platform=platform,
            handler=handler,
            config=config,
            conversation_store=conversation_store,
            pre_cloned=pre_cloned,
        )
    except Exception:
        logger.exception(
            "%s job failed for %s#%d",
            bot_type.value.capitalize(),
            job.event.repo_full_name,
            job.event.pr_number,
        )

        return 1

    if result.review_result is not None:
        cost_info: str = format_cost_summary(cost=result.review_result.cost)

        logger.info(
            "Job review posted for %s#%d (findings=%d)%s",
            job.event.repo_full_name,
            job.event.pr_number,
            len(result.review_result.valid_findings),
            cost_info,
        )
    else:
        logger.info(
            "Worker job completed for %s#%d",
            job.event.repo_full_name,
            job.event.pr_number,
        )

    return 0


def _build_platform(platform_name: PlatformName) -> ReviewerPlatform:
    """
    Construct a platform client from environment variables.

    Reuses the platform CI modules' ``build_platform()`` functions.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        ReviewerPlatform: The constructed platform client.
    """

    platform_ci = load_platform_ci(platform_name=platform_name)
    result: ReviewerPlatform = platform_ci.build_platform()

    return result


def _build_job_config() -> Config:
    """
    Build a Config suitable for job execution.

    Reads ``AGENT_PROVIDER``, ``AGENT_MODEL``, and ``AGENT_MAX_TURNS``
    from environment variables. Uses the same ``load_config_for_ci()``
    pattern as CI mode.

    Returns:
        Config: The resolved job configuration.

    Raises:
        ValueError: If ``AGENT_PROVIDER`` is not a recognised provider.
    """

    provider_config = resolve_provider_config(default="google")

    return load_config_for_ci(provider=provider_config)


def _build_redis_config() -> RedisConfig:
    """
    Build a RedisConfig from environment variables.

    The K8s job pod receives ``REDIS_URL`` and ``REDIS_KEY_TTL_SECONDS``
    as env vars forwarded by ``KubernetesRunner``.

    Returns:
        RedisConfig: The resolved Redis configuration.
    """

    return RedisConfig(
        url=_env.str("REDIS_URL", ""),
        key_ttl_seconds=int(
            _env.str("REDIS_KEY_TTL_SECONDS", str(DEFAULT_REDIS_KEY_TTL_SECONDS)),
        ),
    )


def _publish_completion(exit_code: int, job: JobPayload, redis: RedisConfig) -> None:
    """
    Publish a job completion signal to Redis if running as a K8s Job.

    Args:
        exit_code (int): The job exit code (0 = succeeded).
        job (JobPayload): The job payload (used to build the channel key).
        redis (RedisConfig): Redis configuration.
    """

    if not redis.url:
        return

    channel_key: str = build_job_channel_key(job)
    status: str = "succeeded" if exit_code == 0 else "failed"
    publish_job_completion(redis_url=redis.url, channel_key=channel_key, status=status)
