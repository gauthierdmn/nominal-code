from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from aiohttp import web
from environs import Env

from nominal_code.http import request_with_retry
from nominal_code.models import ChangedFile, EventType, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    ExistingComment,
    LifecycleEvent,
    PlatformName,
    PullRequestEvent,
)
from nominal_code.platforms.github.auth import (
    GitHubAppAuth,
    GitHubAuth,
    GitHubPatAuth,
    load_private_key,
)
from nominal_code.platforms.registry import register_platform

GITHUB_API_BASE: str = "https://api.github.com"
FILES_PER_PAGE: int = 100

PR_ACTION_TO_EVENT_TYPE: dict[str, EventType] = {
    "opened": EventType.PR_OPENED,
    "synchronize": EventType.PR_PUSH,
    "reopened": EventType.PR_REOPENED,
    "ready_for_review": EventType.PR_READY_FOR_REVIEW,
}

logger: logging.Logger = logging.getLogger(__name__)


class GitHubPlatform:
    """
    GitHub webhook handler and API client.

    Handles comment events (``issue_comment``, ``pull_request_review_comment``,
    ``pull_request_review``) and lifecycle events (``pull_request`` with
    relevant actions). Verifies webhooks via HMAC-SHA256.

    Attributes:
        auth (GitHubAuth): Authentication provider for API tokens.
        webhook_secret (str): HMAC secret for signature verification.
    """

    def __init__(
        self,
        auth: GitHubAuth,
        webhook_secret: str = "",
    ) -> None:
        """
        Initialize the GitHub platform client.

        Args:
            auth (GitHubAuth): Authentication provider for API tokens.
            webhook_secret (str): HMAC secret for webhook verification.
        """

        self.auth: GitHubAuth = auth
        self.webhook_secret: str = webhook_secret

        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
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

    def _auth_headers(self) -> dict[str, str]:
        """
        Build authorization headers for GitHub API requests.

        Returns:
            dict[str, str]: Headers with Authorization and Accept fields.
        """

        return {
            "Authorization": f"token {self.auth.get_token()}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Send an authenticated HTTP request with transient retry.

        Injects authorization headers and delegates to
        :func:`~nominal_code.http.request_with_retry`.

        Args:
            method (str): HTTP method (GET, POST, PUT, PATCH, DELETE).
            url (str): Request URL or path.
            **kwargs (Any): Additional arguments forwarded to the request.

        Returns:
            httpx.Response: The HTTP response.
        """

        return await request_with_retry(
            self._client,
            method,
            url,
            headers=self._auth_headers(),
            **kwargs,
        )

    async def ensure_auth(self) -> None:
        """
        Ensure the auth provider has a valid token, refreshing if needed.
        """

        await self.auth.refresh_if_needed()

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

    def parse_event(
        self,
        request: web.Request,
        body: bytes,
    ) -> CommentEvent | LifecycleEvent | None:
        """
        Parse a GitHub webhook payload into a CommentEvent or LifecycleEvent.

        Handles comment events:
        - ``issue_comment`` (created, on PRs only)
        - ``pull_request_review_comment`` (created)
        - ``pull_request_review`` (submitted)

        And lifecycle events:
        - ``pull_request`` (opened, synchronize, reopened, ready_for_review)

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            CommentEvent | LifecycleEvent | None: Parsed event, or None if not relevant.
        """

        event_header: str = request.headers.get("X-GitHub-Event", "")

        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON in GitHub webhook payload")

            return None

        installation: dict[str, Any] = payload.get("installation", {})
        installation_id: int = installation.get("id", 0)

        if installation_id:
            self.auth.set_installation_id(installation_id)

        if event_header == "issue_comment":
            return self._parse_issue_comment(payload)

        if event_header == "pull_request_review_comment":
            return self._parse_review_comment(payload)

        if event_header == "pull_request_review":
            return self._parse_review(payload)

        if event_header == "pull_request":
            return self._parse_pull_request(payload)

        return None

    async def post_reply(
        self,
        event: PullRequestEvent,
        reply: CommentReply,
    ) -> None:
        """
        Post a reply to a GitHub PR comment.

        Uses the issue comments endpoint to reply in the PR conversation.

        Args:
            event (PullRequestEvent): The original event to reply to.
            reply (CommentReply): The reply content.
        """

        body: str = reply.body

        if reply.commit_sha:
            body += f"\n\n_Pushed commit: {reply.commit_sha}_"

        if (
            isinstance(event, CommentEvent)
            and event.event_type == EventType.REVIEW_COMMENT
        ):
            url: str = (
                f"/repos/{event.repo_full_name}"
                f"/pulls/{event.pr_number}"
                f"/comments/{event.comment_id}/replies"
            )
        else:
            url = f"/repos/{event.repo_full_name}/issues/{event.pr_number}/comments"

        try:
            response: httpx.Response = await self._request(
                "POST",
                url,
                json={"body": body},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to post reply to %s#%d",
                event.repo_full_name,
                event.pr_number,
            )

    async def post_reaction(
        self,
        event: CommentEvent,
        reaction: str,
    ) -> None:
        """
        Add a reaction to a GitHub comment.

        Tries the issue comment reactions endpoint first, then falls
        back to pull request review comment reactions.

        Args:
            event (CommentEvent): The comment event to react to.
            reaction (str): The reaction content (e.g. ``eyes``, ``+1``).
        """

        endpoints: list[str] = [
            (
                f"/repos/{event.repo_full_name}"
                f"/issues/comments/{event.comment_id}/reactions"
            ),
            (
                f"/repos/{event.repo_full_name}"
                f"/pulls/comments/{event.comment_id}/reactions"
            ),
        ]

        for url in endpoints:
            try:
                response: httpx.Response = await self._request(
                    "POST",
                    url,
                    json={"content": reaction},
                )

                if response.status_code < 400:
                    return

            except httpx.HTTPError:
                continue

        logger.warning(
            "Failed to add reaction to comment %d on %s",
            event.comment_id,
            event.repo_full_name,
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
            response: httpx.Response = await self._request("GET", url)
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

    async def fetch_pr_branch(self, repo_full_name: str, pr_number: int) -> str:
        """
        Fetch the head branch name for a PR when not available from the webhook.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request number.

        Returns:
            str: The head branch name, or empty string on failure.
        """

        url: str = f"/repos/{repo_full_name}/pulls/{pr_number}"

        try:
            response: httpx.Response = await self._request("GET", url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            return str(data.get("head", {}).get("ref", ""))
        except httpx.HTTPError:
            logger.exception(
                "Failed to fetch PR branch for %s#%d",
                repo_full_name,
                pr_number,
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
                response: httpx.Response = await self._request("GET", url)
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
                try:
                    status: FileStatus = FileStatus(entry.get("status", "modified"))
                except ValueError:
                    status = FileStatus.MODIFIED

                files.append(
                    ChangedFile(
                        file_path=entry.get("filename", ""),
                        status=status,
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
        event: PullRequestEvent,
    ) -> None:
        """
        Submit a GitHub PR review with inline comments.

        Falls back to posting a plain comment if the review API call fails.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Pull request number.
            findings (list[ReviewFinding]): Inline review comments.
            summary (str): High-level review summary.
            event (PullRequestEvent): The original event that triggered the review.
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
            response: httpx.Response = await self._request(
                "POST",
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

            await self.post_reply(
                event=event,
                reply=CommentReply(body=summary),
            )

    def build_reviewer_clone_url(self, repo_full_name: str) -> str:
        """
        Build a clone URL using the read-only reviewer token.

        Falls back to the main token if no reviewer token is configured.

        Args:
            repo_full_name (str): Full repository name.

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        effective_token: str = self.auth.get_reviewer_token()

        return (
            f"https://x-access-token:{effective_token}@github.com/{repo_full_name}.git"
        )

    def _parse_issue_comment(
        self,
        payload: dict[str, Any],
    ) -> CommentEvent | None:
        """
        Parse an ``issue_comment`` event payload.

        Only processes ``created`` actions on pull requests.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            CommentEvent | None: Parsed comment, or None if not relevant.
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

        return CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_branch="",
            event_type=EventType.ISSUE_COMMENT,
            pr_title=issue.get("title", ""),
            comment_id=comment.get("id", 0),
            author_username=comment.get("user", {}).get("login", ""),
            body=comment.get("body", ""),
        )

    def _parse_review_comment(
        self,
        payload: dict[str, Any],
    ) -> CommentEvent | None:
        """
        Parse a ``pull_request_review_comment`` event payload.

        Only processes ``created`` actions.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            CommentEvent | None: Parsed comment, or None if not relevant.
        """

        if payload.get("action") != "created":
            return None

        comment: dict[str, Any] = payload.get("comment", {})
        pull_request: dict[str, Any] = payload.get("pull_request", {})
        repo: dict[str, Any] = payload.get("repository", {})
        repo_full_name: str = repo.get("full_name", "")

        return CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pull_request.get("number", 0),
            pr_branch=pull_request.get("head", {}).get("ref", ""),
            event_type=EventType.REVIEW_COMMENT,
            pr_title=pull_request.get("title", ""),
            comment_id=comment.get("id", 0),
            author_username=comment.get("user", {}).get("login", ""),
            body=comment.get("body", ""),
            diff_hunk=comment.get("diff_hunk", ""),
            file_path=comment.get("path", ""),
        )

    def _parse_review(
        self,
        payload: dict[str, Any],
    ) -> CommentEvent | None:
        """
        Parse a ``pull_request_review`` event payload.

        Only processes ``submitted`` actions with a non-empty body.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            CommentEvent | None: Parsed comment, or None if not relevant.
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

        return CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pull_request.get("number", 0),
            pr_branch=pull_request.get("head", {}).get("ref", ""),
            event_type=EventType.REVIEW,
            pr_title=pull_request.get("title", ""),
            comment_id=review.get("id", 0),
            author_username=review.get("user", {}).get("login", ""),
            body=review_body,
        )

    def _parse_pull_request(
        self,
        payload: dict[str, Any],
    ) -> LifecycleEvent | None:
        """
        Parse a ``pull_request`` lifecycle event payload.

        Maps ``opened``, ``synchronize``, ``reopened``, and
        ``ready_for_review`` actions to the corresponding EventType.
        Draft PRs are skipped.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            LifecycleEvent | None: Parsed event, or None if not relevant.
        """

        action: str = payload.get("action", "")
        event_type: EventType | None = PR_ACTION_TO_EVENT_TYPE.get(action)

        if event_type is None:
            return None

        pull_request: dict[str, Any] = payload.get("pull_request", {})

        if pull_request.get("draft", False):
            return None

        repo: dict[str, Any] = payload.get("repository", {})
        repo_full_name: str = repo.get("full_name", "")

        return LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name=repo_full_name,
            pr_number=pull_request.get("number", 0),
            pr_branch=pull_request.get("head", {}).get("ref", ""),
            event_type=event_type,
            pr_title=pull_request.get("title", ""),
            pr_author=pull_request.get("user", {}).get("login", ""),
        )

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
                response: httpx.Response = await self._request("GET", url)
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
                response: httpx.Response = await self._request("GET", url)
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

    def build_clone_url(self, repo_full_name: str) -> str:
        """
        Build an authenticated clone URL for a GitHub repository.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        return f"https://x-access-token:{self.auth.get_token()}@github.com/{repo_full_name}.git"


def _create_github_platform() -> GitHubPlatform | None:
    """
    Factory that builds a GitHubPlatform from environment variables.

    Detects auth mode: if ``GITHUB_APP_ID`` and a private key are set,
    uses GitHub App authentication. Otherwise falls back to PAT via
    ``GITHUB_TOKEN``. Returns None if neither is configured.

    Returns:
        GitHubPlatform | None: A configured client, or None.
    """

    env: Env = Env()
    webhook_secret: str = env.str("GITHUB_WEBHOOK_SECRET", "")

    app_id: str = env.str("GITHUB_APP_ID", "")
    private_key: str = load_private_key()

    if app_id and private_key:
        installation_id: int = env.int("GITHUB_INSTALLATION_ID", 0)
        auth: GitHubAuth = GitHubAppAuth(
            app_id=app_id,
            private_key=private_key,
            installation_id=installation_id,
        )

        return GitHubPlatform(auth=auth, webhook_secret=webhook_secret)

    token: str = env.str("GITHUB_TOKEN", "")

    if not token:
        return None

    reviewer_token: str = env.str("GITHUB_REVIEWER_TOKEN", "")
    auth = GitHubPatAuth(token=token, reviewer_token=reviewer_token)

    return GitHubPlatform(auth=auth, webhook_secret=webhook_secret)


register_platform("github", _create_github_platform)
