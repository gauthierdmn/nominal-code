from __future__ import annotations

import logging
import os
from pathlib import Path
from types import ModuleType

from nominal_code.agent.providers.registry import PROVIDERS
from nominal_code.config import Config
from nominal_code.models import ProviderName
from nominal_code.platforms.base import (
    CommentReply,
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)
from nominal_code.review.handler import ReviewResult, review

logger: logging.Logger = logging.getLogger(__name__)


async def run_ci_review(platform_name_str: str) -> int:
    """
    Run a CI-triggered review for the given platform.

    Reads environment variables, builds the event and platform client,
    runs the review using the Anthropic API, and posts results.

    Args:
        platform_name_str (str): Platform identifier ("github" or "gitlab").

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    try:
        platform_name: PlatformName = PlatformName(platform_name_str)
    except ValueError:
        logger.error("Unknown platform: %s", platform_name_str)

        return 1

    platform_ci: ModuleType = _load_platform_ci(platform_name)
    event: PullRequestEvent = platform_ci.build_event()
    platform: ReviewerPlatform = platform_ci.build_platform()
    workspace_path: str = platform_ci.resolve_workspace()

    custom_prompt: str = os.environ.get("INPUT_PROMPT", "")
    model: str = os.environ.get("INPUT_MODEL", "")
    max_turns_raw: str = os.environ.get("INPUT_MAX_TURNS", "0")

    try:
        max_turns: int = int(max_turns_raw)
    except ValueError:
        max_turns = 0

    provider = PROVIDERS[ProviderName(os.environ.get("AGENT_PROVIDER", "anthropic"))]
    guidelines_raw: str = os.environ.get("INPUT_CODING_GUIDELINES", "")
    config: Config = Config.for_ci(
        provider=provider,
        model=model,
        max_turns=max_turns,
        guidelines_path=Path(guidelines_raw) if guidelines_raw else Path(),
    )

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

    logger.info(
        "CI review posted for %s#%d (findings=%d)",
        event.repo_full_name,
        event.pr_number,
        len(result.valid_findings),
    )

    return 0


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
