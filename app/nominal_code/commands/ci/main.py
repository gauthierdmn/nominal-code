from __future__ import annotations

import logging
from pathlib import Path

from environs import Env

from nominal_code.config import Config, load_config
from nominal_code.llm.cost import format_cost_summary
from nominal_code.models import ProviderName
from nominal_code.platforms import build_platform
from nominal_code.platforms.base import PlatformName, PullRequestEvent
from nominal_code.review.handler import ReviewResult, run_and_post_review

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

    try:
        config: Config = _build_ci_config()
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    try:
        platform = build_platform(resolved_platform_name, config)
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    event: PullRequestEvent = _build_ci_event(resolved_platform_name)
    workspace_path: str = _resolve_ci_workspace(resolved_platform_name)

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


def _build_ci_event(platform_name: PlatformName) -> PullRequestEvent:
    """
    Build a PullRequestEvent from CI-specific environment variables.

    Dispatches to the platform-specific event builder.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        PullRequestEvent: The event for the current CI run.
    """

    if platform_name == PlatformName.GITHUB:
        from nominal_code.commands.ci.github import build_event
    else:
        from nominal_code.commands.ci.gitlab import build_event

    return build_event()


def _resolve_ci_workspace(platform_name: PlatformName) -> str:
    """
    Resolve the workspace path from CI-specific environment variables.

    Dispatches to the platform-specific workspace resolver.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        str: Absolute path to the repository checkout.
    """

    if platform_name == PlatformName.GITHUB:
        from nominal_code.commands.ci.github import resolve_workspace
    else:
        from nominal_code.commands.ci.gitlab import resolve_workspace

    return resolve_workspace()


def _build_ci_config() -> Config:
    """
    Build a CI Config from environment variables.

    Reads ``INPUT_MODEL``, ``AGENT_PROVIDER``, and
    ``INPUT_CODING_GUIDELINES`` from the environment.

    Returns:
        Config: The resolved CI configuration.

    Raises:
        ValueError: If ``AGENT_PROVIDER`` is not a recognised provider.
    """

    model_env: str = _env.str("INPUT_MODEL", "")
    model: str | None = model_env if model_env else None

    guidelines_env: str = _env.str("INPUT_CODING_GUIDELINES", "")
    guidelines_path: Path | None = Path(guidelines_env) if guidelines_env else None

    return load_config(
        default_provider=ProviderName.ANTHROPIC,
        model=model,
        guidelines_path=guidelines_path,
    )
