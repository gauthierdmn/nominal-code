from __future__ import annotations

import logging
import os
import sys

from nominal_code.models import EventType
from nominal_code.platforms.base import PlatformName, PullRequestEvent, ReviewerPlatform
from nominal_code.platforms.gitlab import GitLabPlatform

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


def build_platform() -> ReviewerPlatform:
    """
    Construct a GitLab platform client for CI mode.

    Reads ``$GITLAB_TOKEN`` and optionally ``$CI_SERVER_URL`` from
    environment variables.

    Returns:
        ReviewerPlatform: The constructed GitLab platform client.

    Raises:
        SystemExit: If ``$GITLAB_TOKEN`` is not set.
    """

    gitlab_token: str = os.environ.get("GITLAB_TOKEN", "")

    if not gitlab_token:
        logger.error("GITLAB_TOKEN is required for GitLab CI reviews")
        sys.exit(1)

    gitlab_base_url: str = os.environ.get("CI_SERVER_URL", "")

    if gitlab_base_url:
        return GitLabPlatform(
            token=gitlab_token,
            base_url=gitlab_base_url,
        )

    return GitLabPlatform(token=gitlab_token)


def resolve_workspace() -> str:
    """
    Determine the workspace path from GitLab CI environment variables.

    Returns:
        str: The absolute path to the repository checkout.
    """

    return os.environ.get("CI_PROJECT_DIR", os.getcwd())
