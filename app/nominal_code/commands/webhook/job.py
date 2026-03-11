from __future__ import annotations

import logging
import os
from datetime import timedelta

from nominal_code.config import Config, resolve_provider_config
from nominal_code.conversation.base import ConversationStore
from nominal_code.handlers.review import run_and_post_review
from nominal_code.handlers.worker import review_and_fix
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.runner.kubernetes import publish_job_completion
from nominal_code.llm.cost import format_cost_summary
from nominal_code.models import BotType
from nominal_code.platforms import load_platform_ci
from nominal_code.platforms.base import CommentEvent, PlatformName, ReviewerPlatform
from nominal_code.workspace.setup import prepare_job_event

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

    payload: str = os.environ.get("REVIEW_JOB_PAYLOAD", "")

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

    conversation_store: ConversationStore | None = _build_conversation_store()

    platform_name: PlatformName = PlatformName(job.event.platform)
    platform: ReviewerPlatform = _build_platform(platform_name)

    bot_type: BotType = BotType(job.bot_type)

    if bot_type == BotType.REVIEWER:
        exit_code: int = await _run_reviewer_job(
            job=job,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )
    else:
        exit_code = await _run_worker_job(
            job=job,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )

    _publish_completion(exit_code)

    return exit_code


async def _run_reviewer_job(
    job: JobPayload,
    config: Config,
    platform: ReviewerPlatform,
    conversation_store: ConversationStore | None = None,
) -> int:
    """
    Execute a reviewer job and post results.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    await platform.ensure_auth()

    logger.info(
        "Running job review for %s#%d on %s",
        job.event.repo_full_name,
        job.event.pr_number,
        job.event.platform,
    )

    try:
        prepared_event = await prepare_job_event(
            event=job.event,
            bot_type=BotType.REVIEWER,
            platform=platform,
        )

        mention_prompt: str = ""

        if isinstance(prepared_event, CommentEvent) and prepared_event.mention_prompt:
            mention_prompt = prepared_event.mention_prompt

        result = await run_and_post_review(
            event=prepared_event,
            prompt=mention_prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )
    except Exception:
        logger.exception(
            "Review failed for %s#%d",
            job.event.repo_full_name,
            job.event.pr_number,
        )

        return 1

    cost_info: str = format_cost_summary(cost=result.cost)

    logger.info(
        "Job review posted for %s#%d (findings=%d)%s",
        job.event.repo_full_name,
        job.event.pr_number,
        len(result.valid_findings),
        cost_info,
    )

    return 0


async def _run_worker_job(
    job: JobPayload,
    config: Config,
    platform: ReviewerPlatform,
    conversation_store: ConversationStore | None = None,
) -> int:
    """
    Execute a worker job.

    Args:
        job (JobPayload): The deserialized job payload.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.
        conversation_store (ConversationStore | None): Conversation store
            for conversation continuity.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    await platform.ensure_auth()

    logger.info(
        "Running worker job for %s#%d on %s",
        job.event.repo_full_name,
        job.event.pr_number,
        job.event.platform,
    )

    try:
        prepared_event = await prepare_job_event(
            event=job.event,
            bot_type=BotType.WORKER,
            platform=platform,
        )

        if not isinstance(prepared_event, CommentEvent):
            raise RuntimeError("Worker job requires a comment event")

        await review_and_fix(
            event=prepared_event,
            prompt=prepared_event.mention_prompt or "",
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )
    except Exception:
        logger.exception(
            "Worker job failed for %s#%d",
            job.event.repo_full_name,
            job.event.pr_number,
        )

        return 1

    logger.info(
        "Worker job completed for %s#%d",
        job.event.repo_full_name,
        job.event.pr_number,
    )

    return 0


def _build_conversation_store() -> ConversationStore | None:
    """
    Build a Redis-backed conversation store when ``REDIS_URL`` is set.

    Returns ``None`` when the env var is absent or when the Redis client
    cannot be created.

    Returns:
        ConversationStore | None: The conversation store, or ``None``.
    """

    redis_url: str = os.environ.get("REDIS_URL", "")

    if not redis_url:
        return None

    try:
        import redis

        from nominal_code.conversation.redis import (
            DEFAULT_KEY_TTL,
            RedisConversationStore,
        )

        ttl_env: str = os.environ.get("REDIS_KEY_TTL_SECONDS", "")
        key_ttl: timedelta = (
            timedelta(seconds=int(ttl_env)) if ttl_env else DEFAULT_KEY_TTL
        )

        client: redis.Redis = redis.Redis.from_url(url=redis_url)
        store: RedisConversationStore = RedisConversationStore(
            client=client, key_ttl=key_ttl
        )

        logger.info("Using Redis conversation store at %s", redis_url)

        return store

    except Exception:
        logger.warning(
            "Failed to create Redis conversation store, continuing without it",
            exc_info=True,
        )

        return None


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
    from environment variables. Uses the same ``Config.for_ci()``
    pattern as CI mode.

    Returns:
        Config: The resolved job configuration.

    Raises:
        ValueError: If ``AGENT_PROVIDER`` is not a recognised provider.
    """

    provider_config = resolve_provider_config(default="google")
    model: str = os.environ.get("AGENT_MODEL", "")

    try:
        max_turns: int = int(os.environ.get("AGENT_MAX_TURNS", "0"))
    except ValueError:
        max_turns = 0

    return Config.for_ci(
        provider=provider_config,
        model=model,
        max_turns=max_turns,
    )


def _publish_completion(exit_code: int) -> None:
    """
    Publish a job completion signal to Redis if running as a K8s Job.

    Checks for ``K8S_JOB_NAME`` and ``REDIS_URL`` environment variables.
    When both are set, publishes a completion message so the server
    can move on to the next queued job.

    Args:
        exit_code (int): The job exit code (0 = succeeded).
    """

    job_name: str = os.environ.get("K8S_JOB_NAME", "")
    redis_url: str = os.environ.get("REDIS_URL", "")

    if not job_name or not redis_url:
        return

    status: str = "succeeded" if exit_code == 0 else "failed"
    publish_job_completion(redis_url=redis_url, job_name=job_name, status=status)
