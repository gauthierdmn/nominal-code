from __future__ import annotations

import logging
import os
from pathlib import Path
from types import ModuleType

from nominal_code.config import DEFAULT_AGENT_MAX_TURNS, Config, ProviderConfig
from nominal_code.handlers.review import ReviewResult, review
from nominal_code.llm.cost import CostSummary
from nominal_code.llm.registry import PROVIDERS
from nominal_code.models import ProviderName
from nominal_code.platforms.base import (
    CommentReply,
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_AGENT_PROVIDER: str = "anthropic"


async def run_ci_review(platform_name: str) -> int:
    """
    Run a CI-triggered review for the given platform.

    Reads environment variables, builds the event and platform client,
    runs the review using an LLM API, and posts results.

    Args:
        platform_name (str): Platform identifier ("github" or "gitlab").

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    try:
        resolved_platform_name: PlatformName = PlatformName(platform_name)
    except ValueError:
        logger.error("Unknown platform: %s", platform_name)

        return 1

    platform_ci: ModuleType = _load_platform_ci(resolved_platform_name)
    event: PullRequestEvent = platform_ci.build_event()
    platform: ReviewerPlatform = platform_ci.build_platform()
    workspace_path: str = platform_ci.resolve_workspace()

    try:
        config: Config = _build_ci_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    custom_prompt: str = os.environ.get("INPUT_PROMPT", "")

    logger.info(
        "Running CI review for %s#%d on %s (workspace=%s)",
        event.repo_full_name,
        event.pr_number,
        platform_name,
        workspace_path,
    )

    try:
        result: ReviewResult = await review(
            event=event,
            prompt=custom_prompt,
            config=config,
            platform=platform,
            workspace_path=workspace_path,
        )
    except RuntimeError:
        logger.exception("Failed to run review")
        return 1

    except Exception:
        logger.exception("Unexpected error running review")
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
        "CI review posted for %s#%d (findings=%d)%s",
        event.repo_full_name,
        event.pr_number,
        len(result.valid_findings),
        cost_info,
    )

    return 0


def _build_ci_config() -> Config:
    """
    Build a CI Config from environment variables.

    Reads ``INPUT_MODEL``, ``INPUT_MAX_TURNS``, ``AGENT_PROVIDER``,
    and ``INPUT_CODING_GUIDELINES`` from the environment.

    Returns:
        Config: The resolved CI configuration.

    Raises:
        ValueError: If ``AGENT_PROVIDER`` is not a recognised provider.
    """

    model: str = os.environ.get("INPUT_MODEL", "")
    max_turns_raw: str = os.environ.get(
        "INPUT_MAX_TURNS",
        str(DEFAULT_AGENT_MAX_TURNS),
    )

    try:
        max_turns: int = int(max_turns_raw)
    except ValueError:
        max_turns = 0

    provider: str = os.environ.get("AGENT_PROVIDER", DEFAULT_AGENT_PROVIDER)

    try:
        provider_name: ProviderName = ProviderName(provider)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown AGENT_PROVIDER: {provider!r}. Available: {available}"
        ) from None

    provider_config: ProviderConfig = PROVIDERS[provider_name]
    guidelines: str = os.environ.get("INPUT_CODING_GUIDELINES", "")

    return Config.for_ci(
        provider=provider_config,
        model=model,
        max_turns=max_turns,
        guidelines_path=Path(guidelines) if guidelines else Path(),
    )


def _format_cost_summary(cost: CostSummary | None) -> str:
    """
    Format a cost summary for log output.

    Args:
        cost (CostSummary | None): The cost summary, or ``None`` if
            cost tracking was not available.

    Returns:
        str: Formatted multi-line string prefixed with a newline,
            or empty string if no cost data is available.
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
        ModuleType: The platform CI module exposing ``build_event``,
            ``build_platform``, and ``resolve_workspace``.
    """

    if platform_name == PlatformName.GITHUB:
        from nominal_code.platforms.github import ci as _ci
    else:
        from nominal_code.platforms.gitlab import ci as _ci  # type: ignore[no-redef]

    return _ci
