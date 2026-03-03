from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from nominal_code.config import Config
from nominal_code.models import EventType
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

    if platform_name == PlatformName.GITHUB:
        event: PullRequestEvent = _build_github_event()
    else:
        event = _build_gitlab_event()

    custom_prompt: str = os.environ.get("INPUT_PROMPT", "")
    model: str = os.environ.get("INPUT_MODEL", "")
    max_turns_raw: str = os.environ.get("INPUT_MAX_TURNS", "0")

    try:
        max_turns: int = int(max_turns_raw)
    except ValueError:
        max_turns = 0

    guidelines_raw: str = os.environ.get("INPUT_CODING_GUIDELINES", "")
    config: Config = Config.for_ci(
        model=model,
        max_turns=max_turns,
        guidelines_path=Path(guidelines_raw) if guidelines_raw else Path(),
    )

    platform: ReviewerPlatform = _build_platform(platform_name, config)
    workspace_path: str = _resolve_workspace_path(platform_name)

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


def _build_github_event() -> PullRequestEvent:
    """
    Build a PullRequestEvent from GitHub Actions environment variables.

    Reads ``$GITHUB_EVENT_PATH`` for the full event payload and extracts
    repository, PR number, and branch information.

    Returns:
        PullRequestEvent: The event for the current GitHub Actions run.

    Raises:
        SystemExit: If required environment variables are missing.
    """

    event_path: str = os.environ.get("GITHUB_EVENT_PATH", "")

    if not event_path or not os.path.isfile(event_path):
        logger.error("GITHUB_EVENT_PATH is not set or file does not exist")
        sys.exit(1)

    with open(event_path, encoding="utf-8") as f:
        payload: dict[str, Any] = json.load(f)

    pull_request: dict[str, Any] = payload.get("pull_request", {})

    if not pull_request:
        logger.error("Event payload does not contain a pull_request object")
        sys.exit(1)

    repo_full_name: str = payload.get("repository", {}).get("full_name", "")
    pr_number: int = pull_request.get("number", 0)
    pr_branch: str = pull_request.get("head", {}).get("ref", "")

    if not repo_full_name or not pr_number or not pr_branch:
        logger.error(
            "Could not extract repo=%s, pr=%d, branch=%s from event payload",
            repo_full_name,
            pr_number,
            pr_branch,
        )
        sys.exit(1)

    return PullRequestEvent(
        platform=PlatformName.GITHUB,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_branch=pr_branch,
        clone_url="",
        event_type=EventType.PR_OPENED,
    )


def _build_gitlab_event() -> PullRequestEvent:
    """
    Build a PullRequestEvent from GitLab CI predefined variables.

    Reads ``$CI_PROJECT_PATH``, ``$CI_MERGE_REQUEST_IID``, and
    ``$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME``.

    Returns:
        PullRequestEvent: The event for the current GitLab CI run.

    Raises:
        SystemExit: If required environment variables are missing.
    """

    repo_full_name: str = os.environ.get("CI_PROJECT_PATH", "")
    mr_iid_raw: str = os.environ.get("CI_MERGE_REQUEST_IID", "")
    pr_branch: str = os.environ.get(
        "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME",
        "",
    )

    if not repo_full_name or not mr_iid_raw or not pr_branch:
        logger.error(
            "Missing GitLab CI variables: CI_PROJECT_PATH=%s, "
            "CI_MERGE_REQUEST_IID=%s, "
            "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME=%s",
            repo_full_name,
            mr_iid_raw,
            pr_branch,
        )
        sys.exit(1)

    try:
        pr_number: int = int(mr_iid_raw)
    except ValueError:
        logger.error("CI_MERGE_REQUEST_IID is not an integer: %s", mr_iid_raw)
        sys.exit(1)

    return PullRequestEvent(
        platform=PlatformName.GITLAB,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_branch=pr_branch,
        clone_url="",
        event_type=EventType.PR_OPENED,
    )


def _build_platform(
    platform_name: PlatformName,
    config: Config,
) -> ReviewerPlatform:
    """
    Construct a platform client for CI mode.

    Args:
        platform_name (PlatformName): The target platform.
        config (Config): Application configuration.

    Returns:
        ReviewerPlatform: The constructed platform client.

    Raises:
        SystemExit: If the required platform is not configured.
    """

    if platform_name == PlatformName.GITHUB:
        if config.github is None:
            logger.error("GITHUB_TOKEN is required for GitHub CI reviews")
            sys.exit(1)

        from nominal_code.platforms.github import GitHubPlatform

        return GitHubPlatform(token=config.github.token)

    if platform_name == PlatformName.GITLAB:
        if config.gitlab is None:
            logger.error("GITLAB_TOKEN is required for GitLab CI reviews")
            sys.exit(1)

        from nominal_code.platforms.gitlab import GitLabPlatform

        gitlab_base_url: str = os.environ.get(
            "CI_SERVER_URL",
            config.gitlab.base_url,
        )

        return GitLabPlatform(
            token=config.gitlab.token,
            base_url=gitlab_base_url,
        )

    logger.error("Unsupported platform: %s", platform_name)
    sys.exit(1)


def _resolve_workspace_path(platform_name: PlatformName) -> str:
    """
    Determine the workspace path from CI environment variables.

    In CI, the repository is already checked out by the runner.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        str: The absolute path to the repository checkout.
    """

    if platform_name == PlatformName.GITHUB:
        return os.environ.get("GITHUB_WORKSPACE", os.getcwd())

    if platform_name == PlatformName.GITLAB:
        return os.environ.get("CI_PROJECT_DIR", os.getcwd())

    return os.getcwd()
