from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from aiohttp import web

if TYPE_CHECKING:
    from nominal_code.models import ChangedFile, EventType, ReviewFinding


class PlatformName(StrEnum):
    """
    Supported source-control platform identifiers.
    """

    GITHUB = "github"
    GITLAB = "gitlab"


@dataclass(frozen=True)
class PullRequestEvent:
    """
    Base normalized event from either GitHub or GitLab.

    Contains the shared identity fields common to both comment and
    lifecycle events. Use ``CommentEvent`` or ``LifecycleEvent`` for
    the full event shape.

    Attributes:
        platform (PlatformName): Source platform identifier.
        repo_full_name (str): Full repository name (e.g. ``owner/repo``).
        pr_number (int): Pull request or merge request number.
        pr_branch (str): The head branch name of the PR/MR.
        clone_url (str): Authenticated clone URL for the repository.
        event_type (EventType): The event type that produced this event.
    """

    platform: PlatformName
    repo_full_name: str
    pr_number: int
    pr_branch: str
    clone_url: str
    event_type: EventType


@dataclass(frozen=True)
class CommentEvent(PullRequestEvent):
    """
    A comment event triggered by an @mention on a PR/MR.

    Attributes:
        comment_id (int): Unique comment identifier on the platform.
        author_username (str): Username of the comment author.
        body (str): The raw comment body text.
        diff_hunk (str): The diff hunk context around the comment.
        file_path (str): File path the comment is attached to.
        discussion_id (str): GitLab discussion ID for threaded replies.
    """

    comment_id: int = 0
    author_username: str = ""
    body: str = ""
    diff_hunk: str = ""
    file_path: str = ""
    discussion_id: str = ""


@dataclass(frozen=True)
class LifecycleEvent(PullRequestEvent):
    """
    A lifecycle event triggered by PR state changes (opened, push, reopened).

    Attributes:
        pr_title (str): Pull request title.
        pr_author (str): Pull request author username.
    """

    pr_title: str = ""
    pr_author: str = ""


@dataclass(frozen=True)
class CommentReply:
    """
    A reply to post back to the platform.

    Attributes:
        body (str): The reply text in markdown.
        commit_sha (str): Optional commit SHA if code was pushed.
    """

    body: str
    commit_sha: str = ""


@dataclass(frozen=True)
class ExistingComment:
    """
    An existing comment on a PR/MR, used as context for the reviewer bot.

    Attributes:
        author (str): Username of the comment author.
        body (str): The comment body text.
        file_path (str): File path the comment is attached to, or empty for
            top-level comments.
        line (int): Line number the comment is attached to, or 0 for top-level
            comments.
        is_resolved (bool): Whether the discussion thread is resolved.
        created_at (str): ISO 8601 timestamp of comment creation.
    """

    author: str
    body: str
    file_path: str = ""
    line: int = 0
    is_resolved: bool = False
    created_at: str = ""


class Platform(Protocol):
    """
    Protocol for platform-specific webhook handling and API calls.
    """

    @property
    def name(self) -> str:
        """
        Unique platform identifier.

        Returns:
            str: The platform name (e.g. ``"github"``, ``"gitlab"``).
        """

        ...

    def verify_webhook(self, request: web.Request, body: bytes) -> bool:
        """
        Verify the webhook signature or token.

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            bool: True if verification succeeds.
        """

        ...

    def parse_event(
        self,
        request: web.Request,
        body: bytes,
    ) -> CommentEvent | LifecycleEvent | None:
        """
        Parse a webhook payload into a CommentEvent or LifecycleEvent.

        Returns None if the event type is not relevant.

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            CommentEvent | LifecycleEvent | None: The parsed event,
                or None if irrelevant.
        """

        ...

    async def post_reply(
        self,
        event: PullRequestEvent,
        reply: CommentReply,
    ) -> None:
        """
        Post a reply to a review comment on the platform.

        Args:
            event (PullRequestEvent): The original event to reply to.
            reply (CommentReply): The reply content.
        """

        ...

    async def post_reaction(
        self,
        event: CommentEvent,
        reaction: str,
    ) -> None:
        """
        Add a reaction/emoji to a comment on the platform.

        Args:
            event (CommentEvent): The comment event to react to.
            reaction (str): The reaction name (e.g. ``eyes``, ``+1``).
        """

        ...

    async def is_pr_open(self, repo_full_name: str, pr_number: int) -> bool:
        """
        Check whether a pull request or merge request is still open.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request or merge request number.

        Returns:
            bool: True if the PR/MR is open, False otherwise.
        """

        ...

    async def fetch_pr_branch(self, repo_full_name: str, pr_number: int) -> str:
        """
        Resolve the head branch name when the webhook payload lacks it.

        Platforms where the branch is always present in the webhook should
        return an empty string.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request or merge request number.

        Returns:
            str: The head branch name, or empty string if unavailable.
        """

        ...


class ReviewerPlatform(Platform, Protocol):
    """
    Protocol extending Platform with reviewer-specific API calls.
    """

    async def fetch_pr_comments(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ExistingComment]:
        """
        Fetch existing comments on a PR/MR for reviewer context.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request or merge request number.

        Returns:
            list[ExistingComment]: Comments sorted by ``created_at`` ascending.
        """

        ...

    async def fetch_pr_diff(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ChangedFile]:
        """
        Fetch the list of changed files with their patches for a PR/MR.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request or merge request number.

        Returns:
            list[ChangedFile]: The changed files with unified diff patches.
        """

        ...

    async def submit_review(
        self,
        repo_full_name: str,
        pr_number: int,
        findings: list[ReviewFinding],
        summary: str,
        event: PullRequestEvent,
    ) -> None:
        """
        Submit a native code review with inline comments.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Pull request or merge request number.
            findings (list[ReviewFinding]): Inline review comments.
            summary (str): High-level review summary.
            event (PullRequestEvent): The original event that triggered the review.
        """

        ...

    def build_reviewer_clone_url(self, repo_full_name: str) -> str:
        """
        Build a clone URL using the read-only reviewer token.

        Falls back to the main token if no reviewer token is configured.

        Args:
            repo_full_name (str): Full repository name.

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        ...
