from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

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
        event_type (EventType): The event type that produced this event.
        clone_url (str): Authenticated clone URL for the repository.
            Defaults to empty; populated after ``authenticate()`` by the
            webhook handler or CLI.
        pr_title (str): Pull request or merge request title. Defaults to
            empty; populated by webhook parsers.
    """

    platform: PlatformName
    repo_full_name: str
    pr_number: int
    pr_branch: str
    event_type: EventType
    clone_url: str = ""
    pr_title: str = ""


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
        mention_prompt (str | None): The user instruction extracted from
            the ``@bot`` mention. ``None`` until mention parsing runs.
    """

    comment_id: int = 0
    author_username: str = ""
    body: str = ""
    diff_hunk: str = ""
    file_path: str = ""
    discussion_id: str = ""
    mention_prompt: str | None = None


@dataclass(frozen=True)
class LifecycleEvent(PullRequestEvent):
    """
    A lifecycle event triggered by PR state changes (opened, push, reopened).

    Attributes:
        pr_author (str): Pull request author username.
    """

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


class PlatformAuth(ABC):
    """
    Abstract base for platform authentication strategies.

    Both GitHub and GitLab auth providers subclass this ABC,
    providing a unified interface for token access across platforms.
    """

    @abstractmethod
    async def ensure_auth(self, account_id: int = 0) -> None:
        """
        Ensure valid authentication for the given account.

        No-op for static token strategies. Dynamic implementations
        refresh or load tokens as needed.

        Args:
            account_id (int): Platform-specific account identifier.
        """

    @abstractmethod
    def get_api_token(self, account_id: int = 0) -> str:
        """
        Return the current API token for the given account.

        PAT implementations ignore the account ID and return a static token.
        App implementations look up the per-account cache.

        Args:
            account_id (int): Platform-specific account identifier.

        Returns:
            str: A valid API token.
        """

    @abstractmethod
    def get_clone_token(self, account_id: int = 0) -> str:
        """
        Return a token suitable for cloning repositories.

        Returns a read-only reviewer token when configured, falling back
        to the main API token otherwise.

        Args:
            account_id (int): Platform-specific account identifier.

        Returns:
            str: A valid clone token.
        """


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

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """
        Verify the webhook signature or token.

        Args:
            headers (Mapping[str, str]): The HTTP request headers.
            body (bytes): The raw request body.

        Returns:
            bool: True if verification succeeds.
        """

        ...

    def parse_event(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> CommentEvent | LifecycleEvent | None:
        """
        Parse a webhook payload into a CommentEvent or LifecycleEvent.

        Returns None if the event type is not relevant.

        Args:
            headers (Mapping[str, str]): The HTTP request headers.
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

    async def post_pr_reaction(
        self,
        repo_full_name: str,
        pr_number: int,
        reaction: str,
    ) -> None:
        """
        Add a reaction/emoji to a PR or MR itself.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request or merge request number.
            reaction (str): The reaction name (e.g. ``eyes``, ``+1``).
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

    async def authenticate(self, *, webhook_body: bytes | None = None) -> None:
        """
        Ensure the platform has valid authentication.

        In webhook mode, pass the raw body so the platform can extract
        account context (e.g. GitHub installation ID). In CLI/CI/job
        mode, call with no arguments.

        Args:
            webhook_body (bytes | None): The raw webhook request body,
                or None for non-webhook modes.
        """

        ...

    def build_clone_url(
        self,
        repo_full_name: str,
        *,
        read_only: bool = False,
    ) -> str:
        """
        Build an authenticated clone URL for a repository.

        Must be called after ``authenticate()`` so that a valid token is
        available for App-based auth modes.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            read_only (bool): If True, use a read-only reviewer token when
                available.

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        ...


@runtime_checkable
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
