from __future__ import annotations

import logging
import os
from dataclasses import replace
from types import ModuleType

from nominal_code.config import Config, ProviderConfig
from nominal_code.conversation.base import ConversationStore
from nominal_code.handlers.review import ReviewResult, post_review_result, review
from nominal_code.jobs.payload import JobPayload
from nominal_code.llm.cost import CostSummary
from nominal_code.llm.registry import PROVIDERS
from nominal_code.models import BotType, ProviderName
from nominal_code.platforms.base import (
    CommentEvent,
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)
from nominal_code.workspace.setup import resolve_branch

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_AGENT_PROVIDER: str = "google"


async def run_job_main() -> int:
    """
    Entry point for the ``run-job`` CLI subcommand.

    Reads the ``REVIEW_JOB_PAYLOAD`` environment variable, deserializes
    the job, constructs the platform client, runs the review using the
    LLM provider API, and posts results.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    payload_raw: str = os.environ.get("REVIEW_JOB_PAYLOAD", "")

    if not payload_raw:
        logger.error("REVIEW_JOB_PAYLOAD environment variable is not set")

        return 1

    try:
        job: JobPayload = JobPayload.deserialize(payload_raw)
    except (TypeError, ValueError, KeyError) as exc:
        logger.error("Failed to deserialize job payload: %s", exc)

        return 1

    try:
        config: Config = _build_job_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    conversation_store: ConversationStore | None = _build_conversation_store()

    platform_name: PlatformName = PlatformName(job.platform)
    platform: ReviewerPlatform = _build_platform(platform_name)

    bot_type: BotType = BotType(job.bot_type)

    if bot_type == BotType.REVIEWER:
        return await _run_reviewer_job(job, config, platform, conversation_store)

    return await _run_worker_job(job, config, platform, conversation_store)


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

    clone_url: str = platform.build_reviewer_clone_url(job.repo_full_name)
    event: PullRequestEvent = replace(job.event, clone_url=clone_url)

    resolved_event: PullRequestEvent | None = await resolve_branch(
        event=event,
        platform=platform,
    )

    if resolved_event is None:
        logger.error(
            "Cannot resolve branch for %s#%d",
            job.repo_full_name,
            job.pr_number,
        )

        return 1

    logger.info(
        "Running job review for %s#%d on %s",
        job.repo_full_name,
        job.pr_number,
        job.platform,
    )

    try:
        result: ReviewResult = await review(
            event=resolved_event,
            prompt=job.prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )
    except Exception:
        logger.exception(
            "Review failed for %s#%d",
            job.repo_full_name,
            job.pr_number,
        )

        return 1

    await post_review_result(event, result, platform)

    cost_info: str = _format_cost_summary(result.cost)

    logger.info(
        "Job review posted for %s#%d (findings=%d)%s",
        job.repo_full_name,
        job.pr_number,
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

    from nominal_code.handlers.worker import review_and_fix

    if not isinstance(job.event, CommentEvent):
        logger.error("Worker job requires a comment event")

        return 1

    await platform.ensure_auth()

    clone_url: str = platform.build_clone_url(job.repo_full_name)
    comment_event: CommentEvent = replace(job.event, clone_url=clone_url)

    resolved_event = await resolve_branch(
        event=comment_event,
        platform=platform,
    )

    if resolved_event is None:
        logger.error(
            "Cannot resolve branch for %s#%d",
            job.repo_full_name,
            job.pr_number,
        )

        return 1

    logger.info(
        "Running worker job for %s#%d on %s",
        job.repo_full_name,
        job.pr_number,
        job.platform,
    )

    try:
        await review_and_fix(
            event=resolved_event,
            prompt=job.prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
        )
    except Exception:
        logger.exception(
            "Worker job failed for %s#%d",
            job.repo_full_name,
            job.pr_number,
        )

        return 1

    logger.info(
        "Worker job completed for %s#%d",
        job.repo_full_name,
        job.pr_number,
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
        from datetime import timedelta

        import redis

        from nominal_code.conversation.redis import (
            DEFAULT_KEY_TTL,
            RedisConversationStore,
        )

        ttl_raw: str = os.environ.get("REDIS_KEY_TTL_SECONDS", "")
        key_ttl: timedelta = (
            timedelta(seconds=int(ttl_raw)) if ttl_raw else DEFAULT_KEY_TTL
        )

        client: redis.Redis = redis.Redis.from_url(redis_url)
        store: RedisConversationStore = RedisConversationStore(client, key_ttl)

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

    platform_ci: ModuleType = _load_platform_ci(platform_name)
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

    provider_name_raw: str = os.environ.get(
        "AGENT_PROVIDER",
        DEFAULT_AGENT_PROVIDER,
    )

    try:
        provider_name: ProviderName = ProviderName(provider_name_raw)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown AGENT_PROVIDER: {provider_name_raw!r}. Available: {available}",
        ) from None

    provider_config: ProviderConfig = PROVIDERS[provider_name]
    model: str = os.environ.get("AGENT_MODEL", "")
    max_turns_raw: str = os.environ.get("AGENT_MAX_TURNS", "0")

    try:
        max_turns: int = int(max_turns_raw)
    except ValueError:
        max_turns = 0

    return Config.for_ci(
        provider=provider_config,
        model=model,
        max_turns=max_turns,
    )


def _format_cost_summary(cost: CostSummary | None) -> str:
    """
    Format a cost summary for log output.

    Args:
        cost (CostSummary | None): The cost summary.

    Returns:
        str: Formatted cost string, or empty if no data.
    """

    if cost is None:
        return ""

    parts: list[str] = []

    if cost.model:
        parts.append(f"  Model: {cost.model} ({cost.provider})")

    tokens_in: int = cost.total_input_tokens
    tokens_out: int = cost.total_output_tokens
    tokens_line: str = f"  Tokens: {tokens_in:,} in / {tokens_out:,} out"

    if cost.total_cache_read_tokens > 0:
        tokens_line += f" (cache read: {cost.total_cache_read_tokens:,})"

    parts.append(tokens_line)

    if cost.total_cost_usd is not None:
        parts.append(f"  Cost: ${cost.total_cost_usd:.4f}")

    if cost.num_api_calls > 0:
        parts.append(f"  API calls: {cost.num_api_calls}")

    return "\n" + "\n".join(parts)


def _load_platform_ci(platform_name: PlatformName) -> ModuleType:
    """
    Import and return the platform-specific CI module.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        ModuleType: The platform CI module.
    """

    if platform_name == PlatformName.GITHUB:
        from nominal_code.platforms.github import ci as _ci
    else:
        from nominal_code.platforms.gitlab import ci as _ci  # type: ignore[no-redef]

    return _ci
