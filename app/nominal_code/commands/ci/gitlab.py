from __future__ import annotations

import logging
import os
import sys

from environs import Env

from nominal_code.models import EventType
from nominal_code.platforms.base import PlatformName, PullRequestEvent

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)


def build_event() -> PullRequestEvent:
    """
    Build a PullRequestEvent from GitLab CI predefined variables.

    Reads ``$CI_PROJECT_PATH``, ``$CI_MERGE_REQUEST_IID``, and
    ``$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME``.

    Returns:
        PullRequestEvent: The event for the current GitLab CI run.

    Raises:
        SystemExit: If required environment variables are missing.
    """

    repo_full_name: str = _env.str("CI_PROJECT_PATH", "")
    mr_iid_env: str = _env.str("CI_MERGE_REQUEST_IID", "")
    pr_branch: str = _env.str("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "")

    if not repo_full_name or not mr_iid_env or not pr_branch:
        logger.error(
            "Missing GitLab CI variables: CI_PROJECT_PATH=%s, "
            "CI_MERGE_REQUEST_IID=%s, "
            "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME=%s",
            repo_full_name,
            mr_iid_env,
            pr_branch,
        )
        sys.exit(1)

    try:
        pr_number: int = int(mr_iid_env)
    except ValueError:
        logger.error("CI_MERGE_REQUEST_IID is not an integer: %s", mr_iid_env)
        sys.exit(1)

    pr_title: str = _env.str("CI_MERGE_REQUEST_TITLE", "")
    base_branch: str = _env.str("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "")

    return PullRequestEvent(
        platform=PlatformName.GITLAB,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_branch=pr_branch,
        clone_url="",
        event_type=EventType.PR_OPENED,
        pr_title=pr_title,
        base_branch=base_branch,
    )


def resolve_workspace() -> str:
    """
    Determine the workspace path from GitLab CI environment variables.

    Returns:
        str: The absolute path to the repository checkout.
    """

    return _env.str("CI_PROJECT_DIR", os.getcwd())
