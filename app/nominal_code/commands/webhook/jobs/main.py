from __future__ import annotations

import logging

from environs import Env

from nominal_code.commands.webhook.jobs.dispatch import JobResult, execute_job
from nominal_code.commands.webhook.jobs.handler import DefaultJobHandler, JobHandler
from nominal_code.commands.webhook.jobs.payload import JobPayload
from nominal_code.commands.webhook.jobs.runner.kubernetes import (
    build_job_channel_key,
    publish_job_completion,
)
from nominal_code.config import Config, load_config
from nominal_code.config.settings import DEFAULT_REDIS_KEY_TTL_SECONDS, RedisConfig
from nominal_code.conversation.base import ConversationStore, build_conversation_store
from nominal_code.llm.cost import format_cost_summary
from nominal_code.models import ProviderName
from nominal_code.platforms import build_platform
from nominal_code.platforms.base import Platform, PlatformName
from nominal_code.review.reviewer import ReviewScope

_env: Env = Env()

logger: logging.Logger = logging.getLogger(__name__)


async def run_job_main(
    pre_cloned: bool = False,
    context: str = "",
    scope: ReviewScope = ReviewScope.PR,
    workspace_path: str | None = None,
) -> JobResult | None:
    """
    Entry point for the ``run-job`` CLI subcommand.

    Reads the ``REVIEW_JOB_PAYLOAD`` environment variable, deserializes
    the job, constructs the platform client, runs the review using the
    LLM provider API, and posts results.

    Args:
        pre_cloned (bool): When True, the repository was pre-cloned by
            an external process and clone URL resolution is skipped.
        context (str): Pre-review context to include in the user message.
        scope (ReviewScope): Whether this is a PR diff review or a
            whole-repository codebase review.
        workspace_path (str): Pre-existing workspace path. Required when
            ``scope`` is ``ReviewScope.CODEBASE``.

    Returns:
        JobResult | None: The job result on success, or ``None`` on
            failure.
    """

    payload: str = _env.str("REVIEW_JOB_PAYLOAD", "")

    if not payload:
        logger.error("REVIEW_JOB_PAYLOAD environment variable is not set")

        return None

    try:
        job: JobPayload = JobPayload.deserialize(payload)
    except (TypeError, ValueError, KeyError) as exc:
        logger.error("Failed to deserialize job payload: %s", exc)

        return None

    try:
        config: Config = _build_job_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return None

    redis: RedisConfig = _build_redis_config()

    conversation_store: ConversationStore = build_conversation_store(
        redis_url=redis.url,
        redis_key_ttl_seconds=redis.key_ttl_seconds,
    )

    platform_name: PlatformName = PlatformName(job.event.platform)
    platform = build_platform(platform_name, config)
    handler: JobHandler = DefaultJobHandler()

    result: JobResult | None = await _run_job(
        job=job,
        config=config,
        platform=platform,
        handler=handler,
        conversation_store=conversation_store,
        pre_cloned=pre_cloned,
        context=context,
        scope=scope,
        workspace_path=workspace_path,
    )

    succeeded: bool = result is not None
    _publish_completion(succeeded=succeeded, job=job, redis=redis)

    return result


async def _run_job(
    job: JobPayload,
    config: Config,
    platform: Platform,
    handler: JobHandler,
    conversation_store: ConversationStore | None = None,
    pre_cloned: bool = False,
    context: str = "",
    scope: ReviewScope = ReviewScope.PR,
    workspace_path: str | None = None,
) -> JobResult | None:
    """
    Execute a job via the unified dispatch pipeline.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (Platform): The platform client.
        handler (JobHandler): The job handler to delegate execution to.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.
        pre_cloned (bool): When True, skip clone URL resolution.
        context (str): Pre-review context to include in the user message.
        scope (ReviewScope): Whether this is a PR diff review or a
            whole-repository codebase review.
        workspace_path (str): Pre-existing workspace path. Required when
            ``scope`` is ``ReviewScope.CODEBASE``.

    Returns:
        JobResult | None: The job result on success, or ``None`` on
            failure.
    """

    logger.info(
        "Running job for %s#%d on %s",
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
            context=context,
            scope=scope,
            workspace_path=workspace_path,
        )
    except Exception:
        logger.exception(
            "Job failed for %s#%d",
            job.event.repo_full_name,
            job.event.pr_number,
        )

        return None

    cost_info: str = format_cost_summary(cost=result.review_result.cost)

    logger.info(
        "Job review posted for %s#%d (findings=%d)%s",
        job.event.repo_full_name,
        job.event.pr_number,
        len(result.review_result.valid_findings),
        cost_info,
    )

    return result


def _build_job_config() -> Config:
    """
    Build a Config suitable for job execution.

    Reads ``AGENT_PROVIDER`` and ``AGENT_MODEL`` from environment
    variables. Uses the same ``load_config_for_ci()`` pattern as CI mode.

    Returns:
        Config: The resolved job configuration.

    Raises:
        ValueError: If ``AGENT_PROVIDER`` is not a recognised provider.
    """

    return load_config(default_provider=ProviderName.GOOGLE)


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


def _publish_completion(
    succeeded: bool,
    job: JobPayload,
    redis: RedisConfig,
) -> None:
    """
    Publish a job completion signal to Redis if running as a K8s Job.

    Args:
        succeeded (bool): Whether the job completed successfully.
        job (JobPayload): The job payload (used to build the channel key).
        redis (RedisConfig): Redis configuration.
    """

    if not redis.url:
        return

    channel_key: str = build_job_channel_key(job)
    status: str = "succeeded" if succeeded else "failed"
    publish_job_completion(redis_url=redis.url, channel_key=channel_key, status=status)
