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


async def resolve_branch(
    event: PullRequestEvent,
    platform: Platform,
) -> PullRequestEvent | None:
    """
    Return event with resolved branch, or None on failure.

    If the event already has a branch, returns it unchanged. Otherwise
    fetches the branch from the platform. Returns None if the branch
    cannot be determined.

    Args:
        event (PullRequestEvent): The event to resolve.
        platform (Platform): The platform client for API calls.

    Returns:
        PullRequestEvent | None: Event with branch set, or None on failure.
    """

    if event.pr_branch:
        return event

    branch: str = await platform.fetch_pr_branch(
        event.repo_full_name,
        event.pr_number,
    )

    if branch:
        return replace(event, pr_branch=branch)

    logger.error(
        "Cannot determine branch for %s#%d",
        event.repo_full_name,
        event.pr_number,
    )

    await platform.post_reply(
        event,
        CommentReply(body="Unable to determine the PR branch."),
    )

    return None


def create_workspace(
    event: PullRequestEvent,
    config: Config,
) -> GitWorkspace:
    """
    Construct a GitWorkspace from the event and config without any I/O.

    Use this when you need to run ``ensure_ready()`` separately (e.g. inside
    an ``asyncio.gather``). For the full setup pipeline, use ``setup_workspace``.

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


async def setup_workspace(
    event: PullRequestEvent,
    config: Config,
) -> GitWorkspace:
    """
    Create a workspace, clone/reset it, and ensure the deps directory exists.

    Combines ``create_workspace``, ``ensure_ready``, and ``ensure_deps_dir``
    into a single call. Lets ``RuntimeError`` from ``ensure_ready`` propagate.

    Args:
        event (PullRequestEvent): The event with repository and branch info.
        config (Config): Application configuration.

    Returns:
        GitWorkspace: The fully ready workspace.

    Raises:
        RuntimeError: If the git workspace cannot be set up.
    """

    workspace: GitWorkspace = create_workspace(event, config)

    await workspace.ensure_ready()
    workspace.ensure_deps_dir()

    return workspace
