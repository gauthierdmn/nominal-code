from __future__ import annotations

import logging
from pathlib import Path

from environs import Env

from nominal_code.config import Config, load_config_for_ci, resolve_provider_config
from nominal_code.handlers.review import ReviewResult, run_and_post_review
from nominal_code.llm.cost import format_cost_summary
from nominal_code.platforms import load_platform_ci
from nominal_code.platforms.base import (
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)


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

    platform_ci = load_platform_ci(platform_name=resolved_platform_name)
    event: PullRequestEvent = platform_ci.build_event()
    platform: ReviewerPlatform = platform_ci.build_platform()
    workspace_path: str = platform_ci.resolve_workspace()

    try:
        config: Config = _build_ci_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    custom_prompt: str = _env.str("INPUT_PROMPT", "")

    logger.info(
        "Running CI review for %s#%d on %s (workspace=%s)",
        event.repo_full_name,
        event.pr_number,
        platform_name,
        workspace_path,
    )

    try:
        result: ReviewResult = await run_and_post_review(
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

    cost_info: str = format_cost_summary(cost=result.cost)

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

    model: str = _env.str("INPUT_MODEL", "")

    try:
        max_turns: int = _env.int("INPUT_MAX_TURNS", 0)
    except ValueError:
        max_turns = 0

    provider_config = resolve_provider_config(default="anthropic")
    guidelines: str = _env.str("INPUT_CODING_GUIDELINES", "")

    return load_config_for_ci(
        provider=provider_config,
        model=model,
        max_turns=max_turns,
        guidelines_path=Path(guidelines) if guidelines else Path(),
    )
