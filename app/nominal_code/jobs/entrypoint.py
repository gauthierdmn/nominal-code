from __future__ import annotations

import logging
import os
from types import ModuleType

from nominal_code.agent.cost import CostSummary
from nominal_code.agent.providers.registry import PROVIDERS
from nominal_code.config import Config, ProviderConfig
from nominal_code.jobs.payload import ReviewJob
from nominal_code.models import BotType, EventType, ProviderName
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    LifecycleEvent,
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)
from nominal_code.review.handler import ReviewResult, review
from nominal_code.workspace.setup import resolve_branch

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_AGENT_PROVIDER: str = "anthropic"


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
        job: ReviewJob = ReviewJob.deserialize(payload_raw)
    except (TypeError, ValueError) as exc:
        logger.error("Failed to deserialize job payload: %s", exc)

        return 1

    try:
        config: Config = _build_job_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    platform_name: PlatformName = PlatformName(job.platform)
    platform: ReviewerPlatform = _build_platform(platform_name)

    bot_type: BotType = BotType(job.bot_type)

    if bot_type == BotType.REVIEWER:
        return await _run_reviewer_job(job, config, platform)

    return await _run_worker_job(job, config, platform)


async def _run_reviewer_job(
    job: ReviewJob,
    config: Config,
    platform: ReviewerPlatform,
) -> int:
    """
    Execute a reviewer job and post results.

    Args:
        job (ReviewJob): The deserialized review job.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    await platform.ensure_auth()

    event: PullRequestEvent = _build_event(job, platform)

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
        )
    except Exception:
        logger.exception(
            "Review failed for %s#%d",
            job.repo_full_name,
            job.pr_number,
        )

        return 1

    if result.agent_review is None:
        await platform.post_reply(
            event,
            CommentReply(body=result.raw_output),
        )

        logger.info("Posted raw review output (JSON parse failed)")

        return 0

    if result.valid_findings:
        await platform.submit_review(
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            findings=result.valid_findings,
            summary=result.effective_summary,
            event=event,
        )
    else:
        await platform.post_reply(
            event=event,
            reply=CommentReply(body=result.effective_summary),
        )

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
    job: ReviewJob,
    config: Config,
    platform: ReviewerPlatform,
) -> int:
    """
    Execute a worker job.

    Args:
        job (ReviewJob): The deserialized review job.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    from nominal_code.worker.handler import review_and_fix

    await platform.ensure_auth()

    comment_event: CommentEvent = CommentEvent(
        platform=PlatformName(job.platform),
        repo_full_name=job.repo_full_name,
        pr_number=job.pr_number,
        pr_branch=job.pr_branch,
        pr_title=job.pr_title,
        event_type=EventType(job.event_type),
        comment_id=job.comment_id,
        author_username=job.author_username,
        body=job.comment_body,
        diff_hunk=job.diff_hunk,
        file_path=job.file_path,
        discussion_id=job.discussion_id,
        clone_url=platform.build_clone_url(job.repo_full_name),
    )

    resolved_event: CommentEvent | None = await resolve_branch(
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


def _build_event(
    job: ReviewJob,
    platform: ReviewerPlatform,
) -> PullRequestEvent:
    """
    Reconstruct a PullRequestEvent from a ReviewJob payload.

    Builds the appropriate event type (comment or lifecycle) and
    sets the clone URL using the platform's reviewer clone URL.

    Args:
        job (ReviewJob): The deserialized job payload.
        platform (ReviewerPlatform): Platform client for building clone URLs.

    Returns:
        PullRequestEvent: The reconstructed event.
    """

    clone_url: str = platform.build_reviewer_clone_url(job.repo_full_name)

    if job.is_comment_event:
        return CommentEvent(
            platform=PlatformName(job.platform),
            repo_full_name=job.repo_full_name,
            pr_number=job.pr_number,
            pr_branch=job.pr_branch,
            pr_title=job.pr_title,
            event_type=EventType(job.event_type),
            clone_url=clone_url,
            comment_id=job.comment_id,
            author_username=job.author_username,
            body=job.comment_body,
            diff_hunk=job.diff_hunk,
            file_path=job.file_path,
            discussion_id=job.discussion_id,
        )

    return LifecycleEvent(
        platform=PlatformName(job.platform),
        repo_full_name=job.repo_full_name,
        pr_number=job.pr_number,
        pr_branch=job.pr_branch,
        pr_title=job.pr_title,
        event_type=EventType(job.event_type),
        clone_url=clone_url,
        pr_author=job.pr_author,
    )


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
