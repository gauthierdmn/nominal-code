from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from environs import Env

from nominal_code.models import EventType
from nominal_code.platforms.base import PlatformName, PullRequestEvent, ReviewerPlatform
from nominal_code.platforms.github import (
    GitHubAppAuth,
    GitHubPatAuth,
    GitHubPlatform,
    load_private_key,
)

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)


def build_event() -> PullRequestEvent:
    """
    Build a PullRequestEvent from GitHub Actions environment variables.

    Reads ``$GITHUB_EVENT_PATH`` for the full event payload and extracts
    repository, PR number, and branch information.

    Returns:
        PullRequestEvent: The event for the current GitHub Actions run.

    Raises:
        SystemExit: If required environment variables are missing.
    """

    event_path: Path = Path(_env.str("GITHUB_EVENT_PATH", ""))

    if not event_path.is_file():
        logger.error("GITHUB_EVENT_PATH is not set or file does not exist")
        sys.exit(1)

    with event_path.open(encoding="utf-8") as f:
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


def build_platform() -> ReviewerPlatform:
    """
    Construct a GitHub platform client for CI/job mode.

    Supports both auth modes: if ``GITHUB_APP_ID`` and a private key
    are available, uses GitHub App authentication. Otherwise falls back
    to PAT via ``GITHUB_TOKEN``.

    Returns:
        ReviewerPlatform: The constructed GitHub platform client.

    Raises:
        SystemExit: If neither auth mode is configured.
    """

    app_id: str = _env.str("GITHUB_APP_ID", "")
    private_key: str = load_private_key()

    if app_id and private_key:
        installation_id: int = _env.int("GITHUB_INSTALLATION_ID", 0)

        return GitHubPlatform(
            auth=GitHubAppAuth(
                app_id=app_id,
                private_key=private_key,
                installation_id=installation_id,
            ),
        )

    github_token: str = _env.str("GITHUB_TOKEN", "")

    if not github_token:
        logger.error(
            "GitHub auth is required: set GITHUB_TOKEN, "
            "or GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY",
        )
        sys.exit(1)

    return GitHubPlatform(auth=GitHubPatAuth(token=github_token))


def resolve_workspace() -> str:
    """
    Determine the workspace path from GitHub Actions environment variables.

    Returns:
        str: The absolute path to the repository checkout.
    """

    return _env.str("GITHUB_WORKSPACE", os.getcwd())
