from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx
from aiohttp import web

from nominal_code.bot_type import ChangedFile, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentReply,
    CommentType,
    ExistingComment,
    PlatformName,
    ReviewComment,
)
from nominal_code.platforms.registry import register_platform

GITHUB_API_BASE: str = "https://api.github.com"
FILES_PER_PAGE: int = 100

logger: logging.Logger = logging.getLogger(__name__)


class GitHubPlatform:
    """
    GitHub webhook handler and API client.

    Handles ``issue_comment``, ``pull_request_review_comment``, and
    ``pull_request_review`` events. Verifies webhooks via HMAC-SHA256.

    Attributes:
        token (str): GitHub personal access token or app token.
        webhook_secret (str): HMAC secret for signature verification.
        reviewer_token (str): Read-only token for reviewer clone URLs.
    """

    def __init__(
        self,
        token: str,
        webhook_secret: str = "",
        reviewer_token: str = "",
    ) -> None:
        """
        Initialize the GitHub platform client.

        Args:
            token (str): GitHub API token.
            webhook_secret (str): HMAC secret for webhook verification.
            reviewer_token (str): Read-only token for reviewer clone URLs.
        """

        self.token: str = token
        self.webhook_secret: str = webhook_secret
        self.reviewer_token: str = reviewer_token
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=30.0,
        )

    @property
    def name(self) -> str:
        """
        Unique platform identifier.

        Returns:
            str: Always ``"github"``.
        """

        return "github"

    def verify_webhook(self, request: web.Request, body: bytes) -> bool:
        """
        Verify the GitHub webhook HMAC-SHA256 signature.

        If no webhook secret is configured, verification is skipped.

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            bool: True if the signature is valid or no secret is configured.
        """

        if not self.webhook_secret:
            return True

        signature: str | None = request.headers.get("X-Hub-Signature-256")

        if not signature:
            return False

        expected: str = (
            "sha256="
            + hmac.new(
                self.webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(signature, expected)

    def parse_webhook(
        self,
        request: web.Request,
        body: bytes,
    ) -> ReviewComment | None:
        """
        Parse a GitHub webhook payload into a ReviewComment.

        Handles three event types:
        - ``issue_comment`` (created, on PRs only)
        - ``pull_request_review_comment`` (created)
        - ``pull_request_review`` (submitted)

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            ReviewComment | None: Parsed comment, or None if not relevant.
        """

        event_type: str = request.headers.get("X-GitHub-Event", "")
        payload: dict[str, Any] = json.loads(body)

        if event_type == "issue_comment":
            return self._parse_issue_comment(payload)

        if event_type == "pull_request_review_comment":
            return self._parse_review_comment(payload)

        if event_type == "pull_request_review":
            return self._parse_review(payload)

        return None

    def _parse_issue_comment(
        self,
        payload: dict[str, Any],
    ) -> ReviewComment | None:
        """
        Parse an ``issue_comment`` event payload.

        Only processes ``created`` actions on pull requests.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            ReviewComment | None: Parsed comment, or None if not relevant.
        """

        if payload.get("action") != "created":
            return None

        if "pull_request" not in payload.get("issue", {}):
            return None

        comment: dict[str, Any] = payload.get("comment", {})
        issue: dict[str, Any] = payload.get("issue", {})
        repo: dict[str, Any] = payload.get("repository", {})
        repo_full_name: str = repo.get("full_name", "")
        pr_number: int = issue.get("number", 0)

        return ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_branch="",
            comment_id=comment.get("id", 0),
            author_username=comment.get("user", {}).get("login", ""),
            body=comment.get("body", ""),
            diff_hunk="",
            file_path="",
            clone_url=self._build_clone_url(repo_full_name),
            comment_type=CommentType.ISSUE_COMMENT,
        )

    def _parse_review_comment(
        self,
        payload: dict[str, Any],
    ) -> ReviewComment | None:
        """
        Parse a ``pull_request_review_comment`` event payload.

        Only processes ``created`` actions.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            ReviewComment | None: Parsed comment, or None if not relevant.
        """

        if payload.get("action") != "created":
            return None

        comment: dict[str, Any] = payload.get("comment", {})
        pull_request: dict[str, Any] = payload.get("pull_request", {})
        repo: dict[str, Any] = payload.get("repository", {})
        repo_full_name: str = repo.get("full_name", "")

        return ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pull_request.get("number", 0),
            pr_branch=pull_request.get("head", {}).get("ref", ""),
            comment_id=comment.get("id", 0),
            author_username=comment.get("user", {}).get("login", ""),
            body=comment.get("body", ""),
            diff_hunk=comment.get("diff_hunk", ""),
            file_path=comment.get("path", ""),
            clone_url=self._build_clone_url(repo_full_name),
            comment_type=CommentType.REVIEW_COMMENT,
        )

    def _parse_review(
        self,
        payload: dict[str, Any],
    ) -> ReviewComment | None:
        """
        Parse a ``pull_request_review`` event payload.

        Only processes ``submitted`` actions with a non-empty body.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            ReviewComment | None: Parsed comment, or None if not relevant.
        """

        if payload.get("action") != "submitted":
            return None

        review: dict[str, Any] = payload.get("review", {})
        review_body: str = review.get("body", "") or ""

        if not review_body.strip():
            return None

        pull_request: dict[str, Any] = payload.get("pull_request", {})
        repo: dict[str, Any] = payload.get("repository", {})
        repo_full_name: str = repo.get("full_name", "")

        return ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pull_request.get("number", 0),
            pr_branch=pull_request.get("head", {}).get("ref", ""),
            comment_id=review.get("id", 0),
            author_username=review.get("user", {}).get("login", ""),
            body=review_body,
            diff_hunk="",
            file_path="",
            clone_url=self._build_clone_url(repo_full_name),
            comment_type=CommentType.REVIEW,
        )

    async def post_reply(
        self,
        comment: ReviewComment,
        reply: CommentReply,
    ) -> None:
        """
        Post a reply to a GitHub PR comment.

        Uses the issue comments endpoint to reply in the PR conversation.

        Args:
            comment (ReviewComment): The original comment to reply to.
            reply (CommentReply): The reply content.
        """

        body: str = reply.body

        if reply.commit_sha:
            body += f"\n\n_Pushed commit: {reply.commit_sha}_"

        if comment.comment_type == CommentType.REVIEW_COMMENT:
            url: str = (
                f"/repos/{comment.repo_full_name}"
                f"/pulls/{comment.pr_number}"
                f"/comments/{comment.comment_id}/replies"
            )
        else:
            url = f"/repos/{comment.repo_full_name}/issues/{comment.pr_number}/comments"

        try:
            response: httpx.Response = await self._client.post(
                url,
                json={"body": body},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to post reply to %s#%d",
                comment.repo_full_name,
                comment.pr_number,
            )

    async def post_reaction(
        self,
        comment: ReviewComment,
        reaction: str,
    ) -> None:
        """
        Add a reaction to a GitHub comment.

        Tries the issue comment reactions endpoint first, then falls
        back to pull request review comment reactions.

        Args:
            comment (ReviewComment): The comment to react to.
            reaction (str): The reaction content (e.g. ``eyes``, ``+1``).
        """

        endpoints: list[str] = [
            (
                f"/repos/{comment.repo_full_name}"
                f"/issues/comments/{comment.comment_id}/reactions"
            ),
            (
                f"/repos/{comment.repo_full_name}"
                f"/pulls/comments/{comment.comment_id}/reactions"
            ),
        ]

        for url in endpoints:
            try:
                response: httpx.Response = await self._client.post(
                    url,
                    json={"content": reaction},
                )

                if response.status_code < 400:
                    return

            except httpx.HTTPError:
                continue

        logger.warning(
            "Failed to add reaction to comment %d on %s",
            comment.comment_id,
            comment.repo_full_name,
        )

    async def is_pr_open(self, repo_full_name: str, pr_number: int) -> bool:
        """
        Check whether a GitHub pull request is still open.

        Returns True on HTTP errors as a safe default to avoid deleting
        workspaces when the API is unreachable.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request number.

        Returns:
            bool: True if the PR is open or on error, False if closed/merged.
        """

        url: str = f"/repos/{repo_full_name}/pulls/{pr_number}"

        try:
            response: httpx.Response = await self._client.get(url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            return str(data.get("state", "")) == "open"
        except httpx.HTTPError:
            logger.warning(
                "Failed to check PR state for %s#%d, assuming open",
                repo_full_name,
                pr_number,
            )

            return True

    async def fetch_pr_branch(self, comment: ReviewComment) -> str:
        """
        Fetch the head branch name for a PR when not available from the webhook.

        Args:
            comment (ReviewComment): The comment with repo and PR info.

        Returns:
            str: The head branch name, or empty string on failure.
        """

        url: str = f"/repos/{comment.repo_full_name}/pulls/{comment.pr_number}"

        try:
            response: httpx.Response = await self._client.get(url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            return str(data.get("head", {}).get("ref", ""))
        except httpx.HTTPError:
            logger.exception(
                "Failed to fetch PR branch for %s#%d",
                comment.repo_full_name,
                comment.pr_number,
            )

            return ""

    async def fetch_pr_comments(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ExistingComment]:
        """
        Fetch existing comments on a GitHub PR from both endpoints.

        Merges top-level issue comments and inline review comments, sorted
        by ``created_at`` ascending.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request number.

        Returns:
            list[ExistingComment]: Combined and sorted comments.
        """

        comments: list[ExistingComment] = []

        comments.extend(
            await self._fetch_issue_comments(repo_full_name, pr_number),
        )
        comments.extend(
            await self._fetch_review_comments(repo_full_name, pr_number),
        )

        comments.sort(key=lambda existing: existing.created_at)

        return comments

    async def _fetch_issue_comments(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ExistingComment]:
        """
        Fetch top-level issue comments on a PR.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Pull request number.

        Returns:
            list[ExistingComment]: Top-level conversation comments.
        """

        results: list[ExistingComment] = []
        page: int = 1

        while True:
            url: str = (
                f"/repos/{repo_full_name}/issues/{pr_number}/comments"
                f"?per_page={FILES_PER_PAGE}&page={page}"
            )

            try:
                response: httpx.Response = await self._client.get(url)
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to fetch issue comments for %s#%d (page %d)",
                    repo_full_name,
                    pr_number,
                    page,
                )

                break

            if not data:
                break

            for entry in data:
                results.append(
                    ExistingComment(
                        author=entry.get("user", {}).get("login", ""),
                        body=entry.get("body", ""),
                        created_at=entry.get("created_at", ""),
                    ),
                )

            if len(data) < FILES_PER_PAGE:
                break

            page += 1

        return results

    async def _fetch_review_comments(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ExistingComment]:
        """
        Fetch inline review comments on a PR.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Pull request number.

        Returns:
            list[ExistingComment]: Inline review comments with file and line info.
        """

        results: list[ExistingComment] = []
        page: int = 1

        while True:
            url: str = (
                f"/repos/{repo_full_name}/pulls/{pr_number}/comments"
                f"?per_page={FILES_PER_PAGE}&page={page}"
            )

            try:
                response: httpx.Response = await self._client.get(url)
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to fetch review comments for %s#%d (page %d)",
                    repo_full_name,
                    pr_number,
                    page,
                )

                break

            if not data:
                break

            for entry in data:
                results.append(
                    ExistingComment(
                        author=entry.get("user", {}).get("login", ""),
                        body=entry.get("body", ""),
                        file_path=entry.get("path", ""),
                        line=entry.get("line", 0) or 0,
                        created_at=entry.get("created_at", ""),
                    ),
                )

            if len(data) < FILES_PER_PAGE:
                break

            page += 1

        return results

    async def fetch_pr_diff(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ChangedFile]:
        """
        Fetch the list of changed files with patches for a GitHub PR.

        Paginates through all pages of the ``/pulls/{pr}/files`` endpoint.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request number.

        Returns:
            list[ChangedFile]: The changed files with unified diff patches.
        """

        files: list[ChangedFile] = []
        page: int = 1

        while True:
            url: str = (
                f"/repos/{repo_full_name}/pulls/{pr_number}/files"
                f"?per_page={FILES_PER_PAGE}&page={page}"
            )

            try:
                response: httpx.Response = await self._client.get(url)
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to fetch PR files for %s#%d (page %d)",
                    repo_full_name,
                    pr_number,
                    page,
                )

                break

            if not data:
                break

            for entry in data:
                files.append(
                    ChangedFile(
                        file_path=entry.get("filename", ""),
                        status=FileStatus(entry.get("status", "modified")),
                        patch=entry.get("patch", ""),
                    ),
                )

            if len(data) < FILES_PER_PAGE:
                break

            page += 1

        return files

    async def submit_review(
        self,
        repo_full_name: str,
        pr_number: int,
        findings: list[ReviewFinding],
        summary: str,
        comment: ReviewComment,
    ) -> None:
        """
        Submit a GitHub PR review with inline comments.

        Falls back to posting a plain comment if the review API call fails.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Pull request number.
            findings (list[ReviewFinding]): Inline review comments.
            summary (str): High-level review summary.
            comment (ReviewComment): The original comment that triggered the review.
        """

        review_comments: list[dict[str, str | int]] = [
            {
                "path": finding.file_path,
                "line": finding.line,
                "side": finding.side,
                "body": finding.body,
            }
            for finding in findings
        ]

        url: str = f"/repos/{repo_full_name}/pulls/{pr_number}/reviews"

        try:
            response: httpx.Response = await self._client.post(
                url,
                json={
                    "event": "COMMENT",
                    "body": summary,
                    "comments": review_comments,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to submit review for %s#%d, falling back to comment",
                repo_full_name,
                pr_number,
            )

            await self.post_reply(comment, CommentReply(body=summary))

    def build_reviewer_clone_url(self, repo_full_name: str) -> str:
        """
        Build a clone URL using the read-only reviewer token.

        Falls back to the main token if no reviewer token is configured.

        Args:
            repo_full_name (str): Full repository name.

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        effective_token: str = self.reviewer_token or self.token

        return (
            f"https://x-access-token:{effective_token}@github.com/{repo_full_name}.git"
        )

    def _build_clone_url(self, repo_full_name: str) -> str:
        """
        Build an authenticated clone URL for a GitHub repository.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        return f"https://x-access-token:{self.token}@github.com/{repo_full_name}.git"


def _create_github_platform() -> GitHubPlatform | None:
    """
    Factory that builds a GitHubPlatform from environment variables.

    Returns None if ``GITHUB_TOKEN`` is not set, indicating GitHub is not
    configured.

    Returns:
        GitHubPlatform | None: A configured client, or None.
    """

    token: str = os.environ.get("GITHUB_TOKEN", "")

    if not token:
        return None

    webhook_secret: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    reviewer_token: str = os.environ.get("GITHUB_REVIEWER_TOKEN", "")

    return GitHubPlatform(
        token=token,
        webhook_secret=webhook_secret,
        reviewer_token=reviewer_token,
    )


register_platform("github", _create_github_platform)
