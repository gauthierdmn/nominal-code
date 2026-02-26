from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from aiohttp import web

if TYPE_CHECKING:
    from nominal_code.bot_type import ChangedFile, ReviewFinding


@dataclass(frozen=True)
class ReviewComment:
    """
    Normalized review comment from either GitHub or GitLab.

    Attributes:
        platform (str): Source platform identifier (``github`` or ``gitlab``).
        repo_full_name (str): Full repository name (e.g. ``owner/repo``).
        pr_number (int): Pull request or merge request number.
        pr_branch (str): The head branch name of the PR/MR.
        comment_id (int): Unique comment identifier on the platform.
        author_username (str): Username of the comment author.
        body (str): The raw comment body text.
        diff_hunk (str): The diff hunk context around the comment.
        file_path (str): File path the comment is attached to.
        clone_url (str): Authenticated clone URL for the repository.
        comment_type (str): Event type that produced this comment
            (e.g. ``review_comment``, ``issue_comment``, ``review``, ``note``).
        discussion_id (str): GitLab discussion ID for threaded replies.
    """

    platform: str
    repo_full_name: str
    pr_number: int
    pr_branch: str
    comment_id: int
    author_username: str
    body: str
    diff_hunk: str
    file_path: str
    clone_url: str
    comment_type: str = ""
    discussion_id: str = ""


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

    def parse_webhook(self, request: web.Request, body: bytes) -> ReviewComment | None:
        """
        Parse a webhook payload into a ReviewComment.

        Returns None if the event type is not a relevant comment event.

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            ReviewComment | None: The parsed comment, or None if irrelevant.
        """

        ...

    async def post_reply(
        self,
        comment: ReviewComment,
        reply: CommentReply,
    ) -> None:
        """
        Post a reply to a review comment on the platform.

        Args:
            comment (ReviewComment): The original comment to reply to.
            reply (CommentReply): The reply content.
        """

        ...

    async def post_reaction(
        self,
        comment: ReviewComment,
        reaction: str,
    ) -> None:
        """
        Add a reaction/emoji to a comment on the platform.

        Args:
            comment (ReviewComment): The comment to react to.
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

    async def fetch_pr_branch(self, comment: ReviewComment) -> str:
        """
        Resolve the head branch name when the webhook payload lacks it.

        Platforms where the branch is always present in the webhook should
        return an empty string.

        Args:
            comment (ReviewComment): The comment with repo and PR info.

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
        comment: ReviewComment,
    ) -> None:
        """
        Submit a native code review with inline comments.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Pull request or merge request number.
            findings (list[ReviewFinding]): Inline review comments.
            summary (str): High-level review summary.
            comment (ReviewComment): The original comment that triggered the review.
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
