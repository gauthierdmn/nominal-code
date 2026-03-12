from __future__ import annotations

import hmac
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx
from aiohttp import web
from environs import Env

from nominal_code.models import (
    ChangedFile,
    DiffSide,
    EventType,
    FileStatus,
    ReviewFinding,
)
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    ExistingComment,
    LifecycleEvent,
    PlatformAuth,
    PlatformName,
    PullRequestEvent,
)
from nominal_code.platforms.gitlab.auth import GitLabPatAuth
from nominal_code.platforms.http import request_with_retry
from nominal_code.platforms.registry import register_platform

GITLAB_API_BASE: str = "https://gitlab.com"
DISCUSSIONS_PER_PAGE: int = 100

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)


def _format_suggestion_body(finding: ReviewFinding) -> str:
    """
    Format a finding body with a GitLab suggestion fence when applicable.

    Args:
        finding (ReviewFinding): The review finding to format.

    Returns:
        str: The formatted body, with a suggestion fence appended if
            the finding has a suggestion.
    """

    if finding.suggestion is None:
        return finding.body

    if finding.start_line is not None:
        lines_above: int = finding.line - finding.start_line
        fence: str = f"```suggestion:-{lines_above}+0"
    else:
        fence = "```suggestion:-0+0"

    return f"{finding.body}\n\n{fence}\n{finding.suggestion}\n```"


class GitLabPlatform:
    """
    GitLab webhook handler and API client.

    Handles ``Note Hook`` events where ``object_kind`` is ``note`` and
    ``noteable_type`` is ``MergeRequest``, and ``Merge Request Hook``
    events for lifecycle actions (open, update with oldrev, reopen).
    Verifies webhooks via secret token.

    Attributes:
        _auth (PlatformAuth): Authentication strategy for token access.
        webhook_secret (str): Secret token for webhook verification.
        base_url (str): GitLab instance base URL.
    """

    def __init__(
        self,
        auth: PlatformAuth,
        webhook_secret: str = "",
        base_url: str = GITLAB_API_BASE,
    ) -> None:
        """
        Initialize the GitLab platform client.

        Args:
            auth (PlatformAuth): Authentication strategy for token access.
            webhook_secret (str): Secret token for webhook verification.
            base_url (str): GitLab instance base URL.
        """

        self.webhook_secret: str = webhook_secret
        self.base_url: str = base_url.rstrip("/")

        self._auth: PlatformAuth = auth
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": self._auth.get_api_token()},
            timeout=30.0,
        )

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Send an HTTP request with transient retry.

        Auth headers are set on the client, so no per-request injection
        is needed.

        Args:
            method (str): HTTP method (GET, POST, PUT, PATCH, DELETE).
            url (str): Request URL or path.
            **kwargs (Any): Additional arguments forwarded to the request.

        Returns:
            httpx.Response: The HTTP response.
        """

        return await request_with_retry(self._client, method, url, **kwargs)

    def _refresh_client_headers(self) -> None:
        """
        Update the HTTP client's auth header with the current token.

        Called after ``ensure_auth()`` to reflect any token changes.
        """

        self._client.headers["PRIVATE-TOKEN"] = self._auth.get_api_token()

    @property
    def name(self) -> str:
        """
        Unique platform identifier.

        Returns:
            str: Always ``"gitlab"``.
        """

        return "gitlab"

    @property
    def host(self) -> str:
        """
        Extract the hostname from the base URL.

        Returns:
            str: The hostname without protocol scheme.
        """

        return self.base_url.replace("https://", "").replace("http://", "")

    def verify_webhook(self, request: web.Request, body: bytes) -> bool:
        """
        Verify the GitLab webhook secret token.

        If no webhook secret is configured, verification is skipped.

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body (unused for token verification).

        Returns:
            bool: True if the token matches or no secret is configured.
        """

        if not self.webhook_secret:
            return True

        token: str | None = request.headers.get("X-Gitlab-Token")

        if token is None:
            return False

        return hmac.compare_digest(token, self.webhook_secret)

    def parse_event(
        self,
        request: web.Request,
        body: bytes,
    ) -> CommentEvent | LifecycleEvent | None:
        """
        Parse a GitLab webhook payload into a CommentEvent or LifecycleEvent.

        Handles Note Hook events on merge requests and Merge Request Hook
        events for lifecycle actions (open, update with oldrev, reopen).

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            CommentEvent | LifecycleEvent | None: Parsed event, or None if not relevant.
        """

        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON in GitLab webhook payload")

            return None

        object_kind: str = payload.get("object_kind", "")

        if object_kind == "note":
            return self._parse_note(payload)

        if object_kind == "merge_request":
            return self._parse_merge_request(payload)

        return None

    async def post_reply(
        self,
        event: PullRequestEvent,
        reply: CommentReply,
    ) -> None:
        """
        Post a reply to a GitLab MR note.

        Args:
            event (PullRequestEvent): The original event to reply to.
            reply (CommentReply): The reply content.
        """

        body: str = reply.body

        if reply.commit_sha:
            body += f"\n\n_Pushed commit: {reply.commit_sha}_"

        project_path: str = quote(event.repo_full_name, safe="")

        if isinstance(event, CommentEvent) and event.discussion_id:
            url: str = (
                f"/projects/{project_path}"
                f"/merge_requests/{event.pr_number}"
                f"/discussions/{event.discussion_id}/notes"
            )
        else:
            url = f"/projects/{project_path}/merge_requests/{event.pr_number}/notes"

        try:
            response: httpx.Response = await self._request(
                "POST",
                url,
                json={"body": body},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to post reply to %s!%d",
                event.repo_full_name,
                event.pr_number,
            )

    async def post_reaction(
        self,
        event: CommentEvent,
        reaction: str,
    ) -> None:
        """
        Add an award emoji to a GitLab MR note.

        Args:
            event (CommentEvent): The comment event to react to.
            reaction (str): The emoji name (e.g. ``eyes``, ``thumbsup``).
        """

        project_path: str = quote(event.repo_full_name, safe="")
        url: str = (
            f"/projects/{project_path}"
            f"/merge_requests/{event.pr_number}"
            f"/notes/{event.comment_id}/award_emoji"
        )

        try:
            response: httpx.Response = await self._request(
                "POST",
                url,
                json={"name": reaction},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "Failed to add reaction to note %d on %s",
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
        Add an award emoji to a GitLab merge request.

        Args:
            repo_full_name (str): Project path (e.g. ``group/project``).
            pr_number (int): Merge request IID.
            reaction (str): The emoji name (e.g. ``eyes``, ``thumbsup``).
        """

        project_path: str = quote(repo_full_name, safe="")
        url: str = f"/projects/{project_path}/merge_requests/{pr_number}/award_emoji"

        try:
            response: httpx.Response = await self._request(
                "POST",
                url,
                json={"name": reaction},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "Failed to add reaction to MR %s!%d",
                repo_full_name,
                pr_number,
            )

    async def is_pr_open(self, repo_full_name: str, pr_number: int) -> bool:
        """
        Check whether a GitLab merge request is still open.

        Returns True on HTTP errors as a safe default to avoid deleting
        workspaces when the API is unreachable.

        Args:
            repo_full_name (str): Full repository name (e.g. ``group/repo``).
            pr_number (int): Merge request IID.

        Returns:
            bool: True if the MR is open or on error, False if closed/merged.
        """

        project_path: str = quote(repo_full_name, safe="")
        url: str = f"/projects/{project_path}/merge_requests/{pr_number}"

        try:
            response: httpx.Response = await self._request("GET", url)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            return str(data.get("state", "")) == "opened"
        except httpx.HTTPError:
            logger.warning(
                "Failed to check MR state for %s!%d, assuming open",
                repo_full_name,
                pr_number,
            )

            return True

    async def fetch_pr_branch(self, repo_full_name: str, pr_number: int) -> str:
        """
        Resolve the head branch for a merge request.

        GitLab webhooks always include the source branch, so this is a no-op.

        Args:
            repo_full_name (str): Full repository name (e.g. ``group/repo``).
            pr_number (int): Merge request IID.

        Returns:
            str: Always returns an empty string.
        """

        return ""

    async def fetch_pr_comments(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ExistingComment]:
        """
        Fetch existing comments on a GitLab MR from its discussions.

        Flattens all notes from all discussions into individual comments.
        Extracts file path and line from the first note's position field
        for inline discussions.

        Args:
            repo_full_name (str): Full repository name (e.g. ``group/repo``).
            pr_number (int): Merge request IID.

        Returns:
            list[ExistingComment]: Flattened and sorted comments.
        """

        project_path: str = quote(repo_full_name, safe="")
        comments: list[ExistingComment] = []
        page: int = 1

        while True:
            url: str = (
                f"/projects/{project_path}/merge_requests/{pr_number}"
                f"/discussions?per_page={DISCUSSIONS_PER_PAGE}&page={page}"
            )

            try:
                response: httpx.Response = await self._request("GET", url)
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to fetch MR discussions for %s!%d (page %d)",
                    repo_full_name,
                    pr_number,
                    page,
                )

                break

            if not data:
                break

            for discussion in data:
                notes: list[dict[str, Any]] = discussion.get("notes", [])
                is_resolved: bool = discussion.get("resolved", False) or False

                file_path: str = ""
                line: int = 0
                first_note: dict[str, Any] = notes[0] if notes else {}
                position: dict[str, Any] = first_note.get("position", {}) or {}

                if position:
                    file_path = position.get("new_path", "") or position.get(
                        "old_path",
                        "",
                    )
                    line = position.get("new_line", 0) or 0

                for note in notes:
                    if note.get("system", False):
                        continue

                    comments.append(
                        ExistingComment(
                            author=note.get("author", {}).get("username", ""),
                            body=note.get("body", ""),
                            file_path=file_path,
                            line=line,
                            is_resolved=is_resolved,
                            created_at=note.get("created_at", ""),
                        ),
                    )

            if len(data) < DISCUSSIONS_PER_PAGE:
                break

            page += 1

        comments.sort(key=lambda existing: existing.created_at)

        return comments

    async def fetch_pr_diff(
        self,
        repo_full_name: str,
        pr_number: int,
    ) -> list[ChangedFile]:
        """
        Fetch the list of changed files with patches for a GitLab MR.

        Uses the ``/merge_requests/{iid}/diffs`` endpoint.

        Args:
            repo_full_name (str): Full repository name (e.g. ``group/repo``).
            pr_number (int): Merge request IID.

        Returns:
            list[ChangedFile]: The changed files with unified diff patches.
        """

        project_path: str = quote(repo_full_name, safe="")
        url: str = f"/projects/{project_path}/merge_requests/{pr_number}/diffs"

        try:
            response: httpx.Response = await self._request("GET", url)
            response.raise_for_status()
            data: list[dict[str, Any]] = response.json()
        except httpx.HTTPError:
            logger.exception(
                "Failed to fetch MR diffs for %s!%d",
                repo_full_name,
                pr_number,
            )

            return []

        files: list[ChangedFile] = []

        for entry in data:
            new_path: str = entry.get("new_path", "")
            old_path: str = entry.get("old_path", "")
            renamed: bool = entry.get("renamed_file", False)
            new_file: bool = entry.get("new_file", False)
            deleted_file: bool = entry.get("deleted_file", False)

            if new_file:
                status: FileStatus = FileStatus.ADDED
            elif deleted_file:
                status = FileStatus.REMOVED
            elif renamed:
                status = FileStatus.RENAMED
            else:
                status = FileStatus.MODIFIED

            files.append(
                ChangedFile(
                    file_path=new_path or old_path,
                    status=status,
                    patch=entry.get("diff", ""),
                ),
            )

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
        Submit a review on a GitLab MR via summary note and diff discussions.

        Posts the summary as a top-level note, then creates a diff discussion
        for each finding.

        Args:
            repo_full_name (str): Full repository name.
            pr_number (int): Merge request IID.
            findings (list[ReviewFinding]): Inline review comments.
            summary (str): High-level review summary.
            event (PullRequestEvent): The original event that triggered the review.
        """

        await self.post_reply(
            event=event,
            reply=CommentReply(body=summary),
        )

        project_path: str = quote(repo_full_name, safe="")

        if not findings:
            return

        versions_url: str = (
            f"/projects/{project_path}/merge_requests/{pr_number}/versions"
        )

        try:
            versions_response: httpx.Response = await self._request(
                "GET",
                versions_url,
            )
            versions_response.raise_for_status()
            versions: list[dict[str, Any]] = versions_response.json()
        except httpx.HTTPError:
            logger.exception(
                "Failed to fetch MR versions for %s!%d, skipping inline comments",
                repo_full_name,
                pr_number,
            )

            return

        if not versions:
            logger.warning(
                "No versions found for %s!%d, skipping inline comments",
                repo_full_name,
                pr_number,
            )

            return

        latest_version: dict[str, Any] = versions[0]
        base_sha: str = latest_version.get("base_commit_sha", "")
        head_sha: str = latest_version.get("head_commit_sha", "")
        start_sha: str = latest_version.get("start_commit_sha", "")

        discussions_url: str = (
            f"/projects/{project_path}/merge_requests/{pr_number}/discussions"
        )

        for finding in findings:
            position_payload: dict[str, str | int] = {
                "base_sha": base_sha,
                "head_sha": head_sha,
                "start_sha": start_sha,
                "position_type": "text",
            }

            if finding.side == DiffSide.LEFT:
                position_payload["old_path"] = finding.file_path
                position_payload["old_line"] = finding.line
            else:
                position_payload["new_path"] = finding.file_path
                position_payload["new_line"] = finding.line

            try:
                discussion_response: httpx.Response = await self._request(
                    "POST",
                    discussions_url,
                    json={
                        "body": _format_suggestion_body(finding),
                        "position": position_payload,
                    },
                )
                discussion_response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "Failed to post inline comment on %s:%d in %s!%d",
                    finding.file_path,
                    finding.line,
                    repo_full_name,
                    pr_number,
                )

    async def authenticate(self, *, webhook_body: bytes | None = None) -> None:
        """
        Ensure the platform has valid authentication.

        GitLab does not extract account context from the payload.
        Refreshes the HTTP client headers with the current token from
        the auth strategy.

        Args:
            webhook_body (bytes | None): The raw webhook request body
                (unused for GitLab PAT auth).
        """

        await self._auth.ensure_auth()
        self._refresh_client_headers()

    def _parse_note(
        self,
        payload: dict[str, Any],
    ) -> CommentEvent | None:
        """
        Parse a Note Hook event on a merge request.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            CommentEvent | None: Parsed comment, or None if not relevant.
        """

        object_attributes: dict[str, Any] = payload.get(
            "object_attributes",
            {},
        )

        if object_attributes.get("noteable_type") != "MergeRequest":
            return None

        merge_request: dict[str, Any] = payload.get("merge_request", {})
        project: dict[str, Any] = payload.get("project", {})
        user: dict[str, Any] = payload.get("user", {})

        repo_full_name: str = project.get("path_with_namespace", "")

        file_path: str = ""
        position: dict[str, Any] = object_attributes.get("position", {})

        if position:
            file_path = position.get("new_path", "") or position.get(
                "old_path",
                "",
            )

        discussion_id: str = str(
            object_attributes.get("discussion_id", ""),
        )

        return CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name=repo_full_name,
            pr_number=merge_request.get("iid", 0),
            pr_branch=merge_request.get("source_branch", ""),
            event_type=EventType.NOTE,
            pr_title=merge_request.get("title", ""),
            comment_id=object_attributes.get("id", 0),
            author_username=user.get("username", ""),
            body=object_attributes.get("note", ""),
            file_path=file_path,
            discussion_id=discussion_id,
        )

    def _parse_merge_request(
        self,
        payload: dict[str, Any],
    ) -> LifecycleEvent | None:
        """
        Parse a Merge Request Hook lifecycle event.

        Maps ``open``, ``reopen``, and ``update`` (with ``oldrev``) actions
        to the corresponding EventType. WIP merge requests are skipped.

        Args:
            payload (dict[str, Any]): The webhook payload.

        Returns:
            LifecycleEvent | None: Parsed event, or None if not relevant.
        """

        object_attributes: dict[str, Any] = payload.get(
            "object_attributes",
            {},
        )
        action: str = object_attributes.get("action", "")

        if action == "open":
            event_type: EventType = EventType.PR_OPENED
        elif action == "reopen":
            event_type = EventType.PR_REOPENED
        elif action == "update" and "oldrev" in object_attributes:
            event_type = EventType.PR_PUSH
        else:
            return None

        if object_attributes.get("work_in_progress", False):
            return None

        project: dict[str, Any] = payload.get("project", {})
        repo_full_name: str = project.get("path_with_namespace", "")

        return LifecycleEvent(
            platform=PlatformName.GITLAB,
            repo_full_name=repo_full_name,
            pr_number=object_attributes.get("iid", 0),
            pr_branch=object_attributes.get("source_branch", ""),
            event_type=event_type,
            pr_title=object_attributes.get("title", ""),
            pr_author=payload.get("user", {}).get("username", ""),
        )

    def build_clone_url(
        self,
        repo_full_name: str,
        *,
        read_only: bool = False,
    ) -> str:
        """
        Build an authenticated clone URL for a GitLab repository.

        Args:
            repo_full_name (str): Full repository name.
            read_only (bool): If True, use the read-only clone token.

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        if read_only:
            token: str = self._auth.get_clone_token()
        else:
            token = self._auth.get_api_token()

        return f"https://oauth2:{token}@{self.host}/{repo_full_name}.git"


def _create_gitlab_platform() -> GitLabPlatform | None:
    """
    Factory that builds a GitLabPlatform from environment variables.

    Returns None if ``GITLAB_TOKEN`` is not set, indicating GitLab is not
    configured.

    Returns:
        GitLabPlatform | None: A configured client, or None.
    """

    token: str = _env.str("GITLAB_TOKEN", "")

    if not token:
        return None

    webhook_secret: str = _env.str("GITLAB_WEBHOOK_SECRET", "")
    base_url: str = _env.str("GITLAB_API_BASE", GITLAB_API_BASE)
    reviewer_token: str = _env.str("GITLAB_REVIEWER_TOKEN", "")

    auth: GitLabPatAuth = GitLabPatAuth(
        token=token,
        reviewer_token=reviewer_token,
    )

    return GitLabPlatform(
        auth=auth,
        webhook_secret=webhook_secret,
        base_url=base_url,
    )


register_platform("gitlab", _create_gitlab_platform)
