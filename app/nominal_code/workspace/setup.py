from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.platforms.base import (
    CommentReply,
    Platform,
    PullRequestEvent,
)
from nominal_code.workspace.git import GitWorkspace

if TYPE_CHECKING:
    from nominal_code.config import Config

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

    When ``event.clone_url`` is empty, the workspace is marked as
    read-only.

    Args:
        event (PullRequestEvent): The event with repository and branch info.
        config (Config): Application configuration.

    Returns:
        GitWorkspace: The constructed (but not yet cloned) workspace.
    """

    read_only: bool = not event.clone_url

    return GitWorkspace(
        base_dir=config.workspace.base_dir,
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
        clone_url=event.clone_url,
        branch=event.pr_branch,
        read_only=read_only,
    )


async def prepare_job_event(
    event: PullRequestEvent,
    platform: Platform,
    pre_cloned: bool = False,
) -> PullRequestEvent:
    """
    Resolve clone URL and branch for a job event.

    Resolves the authenticated clone URL. When ``pre_cloned`` is True
    the repository was cloned by an external process and the clone URL
    is left empty so that ``create_workspace`` marks the resulting
    ``GitWorkspace`` as read-only.

    Args:
        event (PullRequestEvent): The raw event from the job payload.
        platform (Platform): The platform client.
        pre_cloned (bool): When True, skip clone URL resolution and
            preserve the empty clone URL for a read-only workspace.

    Returns:
        PullRequestEvent: The event with clone URL and branch resolved.

    Raises:
        RuntimeError: If branch resolution fails.
    """

    if pre_cloned:
        clone_url: str = ""
    else:
        clone_url = platform.build_clone_url(
            repo_full_name=event.repo_full_name,
        )

    effective_event: PullRequestEvent = replace(event, clone_url=clone_url)
    resolved_event: PullRequestEvent | None = await resolve_branch(
        event=effective_event,
        platform=platform,
    )

    if resolved_event is None:
        raise RuntimeError(
            f"Cannot resolve branch for {event.repo_full_name}#{event.pr_number}",
        )

    return resolved_event
