from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.models import BotType
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    PullRequestEvent,
    ReviewerPlatform,
)
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


async def prepare_job_event(
    event: PullRequestEvent,
    bot_type: BotType,
    platform: Platform,
) -> PullRequestEvent:
    """
    Resolve clone URL and branch for a job event.

    For reviewer jobs, uses the read-only reviewer clone URL. For worker
    jobs, validates that the event is a ``CommentEvent`` and uses the
    read-write clone URL.

    Args:
        event (PullRequestEvent): The raw event from the job payload.
        bot_type (BotType): Which bot personality will handle the job.
        platform (Platform): The platform client.

    Returns:
        PullRequestEvent: The event with clone URL and branch resolved.

    Raises:
        RuntimeError: If the event type is incompatible with the bot type,
            the platform doesn't support reviewer operations, or branch
            resolution fails.
    """

    if bot_type == BotType.WORKER:
        if not isinstance(event, CommentEvent):
            raise RuntimeError("Worker job requires a comment event")

        clone_url: str = platform.build_clone_url(
            repo_full_name=event.repo_full_name,
        )

    else:
        if not isinstance(platform, ReviewerPlatform):
            raise RuntimeError(
                f"Platform {event.platform} does not support reviewer operations",
            )

        clone_url = platform.build_reviewer_clone_url(
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
