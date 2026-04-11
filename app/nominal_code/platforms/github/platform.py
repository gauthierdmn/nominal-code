from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nominal_code.config.settings import GitHubConfig

import httpx

from nominal_code.config.settings import DEFAULT_GITHUB_API_BASE
from nominal_code.models import ChangedFile, EventType, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    ExistingComment,
    LifecycleEvent,
    PlatformAuth,
    PlatformName,
    PullRequestEvent,
)
from nominal_code.platforms.github.auth import (
    NO_INSTALLATION,
    GitHubAppAuth,
    GitHubPatAuth,
)
from nominal_code.platforms.http import request_with_retry

FILES_PER_PAGE: int = 100

PR_ACTION_TO_EVENT_TYPE: dict[str, EventType] = {
    "opened": EventType.PR_OPENED,
    "synchronize": EventType.PR_PUSH,
    "reopened": EventType.PR_REOPENED,
    "ready_for_review": EventType.PR_READY_FOR_REVIEW,
}

logger: logging.Logger = logging.getLogger(__name__)

_installation_ctx: ContextVar[int] = ContextVar(
    "github_installation_id",
    default=NO_INSTALLATION,
)


def _format_suggestion_body(finding: ReviewFinding) -> str:
    """
    Format a finding body with a GitHub suggestion fence when applicable.

    Args:
        finding (ReviewFinding): The review finding to format.

    Returns:
        str: The formatted body, with a suggestion fence appended if
            the finding has a suggestion.
    """

    if finding.suggestion is None:
        return finding.body

    return f"{finding.body}\n\n```suggestion\n{finding.suggestion}\n```"


class GitHubPlatform:
    """
    GitHub webhook handler and API client.

    Handles comment events (``issue_comment``, ``pull_request_review_comment``,
    ``pull_request_review``) and lifecycle events (``pull_request`` with
    relevant actions). Verifies webhooks via HMAC-SHA256.

    Attributes:
        auth (PlatformAuth): Authentication provider for API tokens.
        webhook_secret (str): HMAC secret for signature verification.
    """

    def __init__(
        self,
        auth: PlatformAuth,
        webhook_secret: str | None = None,
        fixed_installation_id: int = NO_INSTALLATION,
        base_url: str = "",
    ) -> None:
        """
        Initialize the GitHub platform client.

        Args:
            auth (PlatformAuth): Authentication provider for API tokens.
            webhook_secret (str | None): HMAC secret for webhook verification.
                None to skip verification.
            fixed_installation_id (int): Installation ID for CLI/CI modes
                where no webhook payload provides one.
            base_url (str): Override for the GitHub API base URL.
                Defaults to ``DEFAULT_GITHUB_API_BASE``.
        """

        self.auth: PlatformAuth = auth
        self.webhook_secret: str | None = webhook_secret
        self._fixed_installation_id: int = fixed_installation_id

        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=base_url or DEFAULT_GITHUB_API_BASE,
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

    def _active_installation_id(self) -> int:
        """
        Resolve the installation ID for the current request context.

        Returns the ContextVar value if set (webhook mode), otherwise
        falls back to the platform's fixed installation ID (CLI/CI mode).

        Returns:
            int: The active installation ID, or ``NO_INSTALLATION``.
        """

        ctx_id: int = _installation_ctx.get()

        if ctx_id:
            return ctx_id

        return self._fixed_installation_id

    def _auth_headers(self) -> dict[str, str]:
        """
        Build authorization headers for GitHub API requests.

        Returns:
            dict[str, str]: Headers with Authorization and Accept fields.
        """

        token: str = self.auth.get_api_token(
            self._active_installation_id(),
        )

        return {
            "Authorization": f"token {token}",
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
        :func:`~nominal_code.platforms.http.request_with_retry`.

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

    async def authenticate(self, *, webhook_body: bytes | None = None) -> None:
        """
        Ensure the platform has valid authentication.

        In webhook mode, extracts ``installation.id`` from the payload,
        sets the request-scoped ContextVar, and refreshes the token. In
        CLI/CI mode, call with no arguments.

        Args:
            webhook_body (bytes | None): The raw webhook request body,
                or None for non-webhook modes.
        """

        if webhook_body is not None:
            account_id: int = self.extract_installation_id(webhook_body)

            if account_id:
                _installation_ctx.set(account_id)

        active_id: int = self._active_installation_id()

        await self.auth.ensure_auth(active_id)

    def extract_installation_id(self, body: bytes) -> int:
        """
        Extract the GitHub App installation ID from a webhook payload.

        Not part of the ``Platform`` protocol — this is a GitHub-specific
        method. Auth consumers should use ``authenticate()`` instead.

        Args:
            body (bytes): The raw webhook request body.

        Returns:
            int: The installation ID, or 0 if not present.
        """

        try:
            payload: dict[str, Any] = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return 0

        installation: dict[str, Any] = payload.get("installation", {})

        return int(installation.get("id", 0))

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """
        Verify the GitHub webhook HMAC-SHA256 signature.

        If no webhook secret is configured, verification is skipped.

        Args:
            headers (Mapping[str, str]): The HTTP request headers.
            body (bytes): The raw request body.

        Returns:
            bool: True if the signature is valid or no secret is configured.
        """

        if self.webhook_secret is None:
            return True

        signature: str | None = headers.get("X-Hub-Signature-256")

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
        headers: Mapping[str, str],
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

        Auth is no longer mutated here. Call ``ensure_auth(account_id)``
        before ``parse_event()`` in the webhook handler.

        Args:
            headers (Mapping[str, str]): The HTTP request headers.
            body (bytes): The raw request body.

        Returns:
            CommentEvent | LifecycleEvent | None: Parsed event, or None if not relevant.
        """

        event_header: str = headers.get("X-GitHub-Event", "")

        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON in GitHub webhook payload")

            return None

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

    async def post_pr_reaction(
        self,
        repo_full_name: str,
        pr_number: int,
        reaction: str,
    ) -> None:
        """
        Add a reaction to a GitHub pull request.

        GitHub treats PRs as issues, so the issues reactions endpoint is used.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request number.
            reaction (str): The reaction content (e.g. ``eyes``, ``+1``).
        """

        url: str = f"/repos/{repo_full_name}/issues/{pr_number}/reactions"

        try:
            response: httpx.Response = await self._request(
                "POST",
                url,
                json={"content": reaction},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "Failed to add reaction to PR %s#%d",
                repo_full_name,
                pr_number,
            )

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

        review_comments: list[dict[str, str | int]] = []

        for finding in findings:
            comment_dict: dict[str, str | int] = {
                "path": finding.file_path,
                "line": finding.line,
                "side": finding.side,
                "body": _format_suggestion_body(finding),
            }

            if finding.start_line is not None:
                comment_dict["start_line"] = finding.start_line
                comment_dict["start_side"] = finding.side

            review_comments.append(comment_dict)

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
            base_branch=pull_request.get("base", {}).get("ref", ""),
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
            base_branch=pull_request.get("base", {}).get("ref", ""),
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
            base_branch=pull_request.get("base", {}).get("ref", ""),
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

    def build_clone_url(
        self,
        repo_full_name: str,
    ) -> str:
        """
        Build an authenticated clone URL for a GitHub repository.

        Args:
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        active_id: int = self._active_installation_id()
        token: str = self.auth.get_api_token(active_id)

        return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"


def create_github_platform(config: GitHubConfig) -> GitHubPlatform | None:
    """
    Build a GitHubPlatform from configuration.

    Detects auth mode: if ``app_id`` and ``private_key`` are set,
    uses GitHub App authentication. Otherwise falls back to PAT via
    ``token``. Returns None if neither is configured.

    Args:
        config (GitHubConfig): The frozen GitHub configuration.

    Returns:
        GitHubPlatform | None: A configured client, or None.
    """

    if config.app_id and config.private_key:
        auth: PlatformAuth = GitHubAppAuth(
            app_id=config.app_id,
            private_key=config.private_key,
        )

        return GitHubPlatform(
            auth=auth,
            webhook_secret=config.webhook_secret,
            fixed_installation_id=config.installation_id,
            base_url=config.api_base,
        )

    if not config.token:
        return None

    auth = GitHubPatAuth(
        token=config.token,
    )

    return GitHubPlatform(
        auth=auth,
        webhook_secret=config.webhook_secret,
        base_url=config.api_base,
    )
