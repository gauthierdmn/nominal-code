from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.platforms.base import CommentReply, PullRequestEvent
from nominal_code.workspace.git import GitWorkspace

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


async def resolve_branch[E: PullRequestEvent](
    event: E,
    platform: Platform,
) -> E | None:
    """
    Return event with resolved branch, or None on failure.

    If the event already has a branch, returns it unchanged. Otherwise
    fetches the branch from the platform. Returns None if the branch
    cannot be determined.

    Args:
        event (_E): The event to resolve.
        platform (Platform): The platform client for API calls.

    Returns:
        _E | None: Event with branch set, or None on failure.
    """

    if event.pr_branch:
        return event

    branch: str = await platform.fetch_pr_branch(
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
    )

    if branch:
        return replace(event, pr_branch=branch)

    logger.error(
        "Cannot determine branch for %s#%d",
        event.repo_full_name,
        event.pr_number,
    )

    await platform.post_reply(
        event=event,
        reply=CommentReply(body="Unable to determine the PR branch."),
    )

    return None


def create_workspace(
    event: PullRequestEvent,
    config: Config,
) -> GitWorkspace:
    """
    Construct a GitWorkspace from the event and config.

    Args:
        event (PullRequestEvent): The event with repository and branch info.
        config (Config): Application configuration.

    Returns:
        GitWorkspace: The constructed (but not yet cloned) workspace.
    """

    return GitWorkspace(
        base_dir=config.workspace_base_dir,
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
        clone_url=event.clone_url,
        branch=event.pr_branch,
    )
