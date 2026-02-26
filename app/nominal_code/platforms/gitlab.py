from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import quote

import httpx
from aiohttp import web

from nominal_code.bot_type import ChangedFile, ReviewFinding
from nominal_code.platforms.base import (
    CommentReply,
    ExistingComment,
    ReviewComment,
)
from nominal_code.platforms.registry import register_platform

logger: logging.Logger = logging.getLogger(__name__)


class GitLabPlatform:
    """
    GitLab webhook handler and API client.

    Handles ``Note Hook`` events where ``object_kind`` is ``note`` and
    ``noteable_type`` is ``MergeRequest``. Verifies webhooks via secret token.

    Attributes:
        token (str): GitLab personal access token.
        webhook_secret (str): Secret token for webhook verification.
        base_url (str): GitLab instance base URL.
        reviewer_token (str): Read-only token for reviewer clone URLs.
    """

    def __init__(
        self,
        token: str,
        webhook_secret: str = "",
        base_url: str = "https://gitlab.com",
        reviewer_token: str = "",
    ) -> None:
        """
        Initialize the GitLab platform client.

        Args:
            token (str): GitLab API token.
            webhook_secret (str): Secret token for webhook verification.
            base_url (str): GitLab instance base URL.
            reviewer_token (str): Read-only token for reviewer clone URLs.
        """

        self.token: str = token
        self.webhook_secret: str = webhook_secret
        self.base_url: str = base_url.rstrip("/")
        self.reviewer_token: str = reviewer_token
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=30.0,
        )

    @property
    def name(self) -> str:
        """
        Unique platform identifier.

        Returns:
            str: Always ``"gitlab"``.
        """

        return "gitlab"

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

        return token == self.webhook_secret

    def parse_webhook(
        self,
        request: web.Request,
        body: bytes,
    ) -> ReviewComment | None:
        """
        Parse a GitLab webhook payload into a ReviewComment.

        Only handles Note Hook events on merge requests.

        Args:
            request (web.Request): The incoming HTTP request.
            body (bytes): The raw request body.

        Returns:
            ReviewComment | None: Parsed comment, or None if not relevant.
        """

        payload: dict[str, Any] = json.loads(body)

        if payload.get("object_kind") != "note":
            return None

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
        host: str = self.base_url.replace("https://", "").replace("http://", "")

        diff_hunk: str = ""
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

        return ReviewComment(
            platform="gitlab",
            repo_full_name=repo_full_name,
            pr_number=merge_request.get("iid", 0),
            pr_branch=merge_request.get("source_branch", ""),
            comment_id=object_attributes.get("id", 0),
            author_username=user.get("username", ""),
            body=object_attributes.get("note", ""),
            diff_hunk=diff_hunk,
            file_path=file_path,
            clone_url=f"https://oauth2:{self.token}@{host}/{repo_full_name}.git",
            comment_type="note",
            discussion_id=discussion_id,
        )

    async def post_reply(
        self,
        comment: ReviewComment,
        reply: CommentReply,
    ) -> None:
        """
        Post a reply to a GitLab MR note.

        Args:
            comment (ReviewComment): The original comment to reply to.
            reply (CommentReply): The reply content.
        """

        body: str = reply.body

        if reply.commit_sha:
            body += f"\n\n_Pushed commit: {reply.commit_sha}_"

        project_path: str = quote(comment.repo_full_name, safe="")

        if comment.discussion_id:
            url: str = (
                f"/projects/{project_path}"
                f"/merge_requests/{comment.pr_number}"
                f"/discussions/{comment.discussion_id}/notes"
            )
        else:
            url = f"/projects/{project_path}/merge_requests/{comment.pr_number}/notes"

        try:
            response: httpx.Response = await self._client.post(
                url,
                json={"body": body},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception(
                "Failed to post reply to %s!%d",
                comment.repo_full_name,
                comment.pr_number,
            )

    async def post_reaction(
        self,
        comment: ReviewComment,
        reaction: str,
    ) -> None:
        """
        Add an award emoji to a GitLab MR note.

        Args:
            comment (ReviewComment): The comment to react to.
            reaction (str): The emoji name (e.g. ``eyes``, ``thumbsup``).
        """

        project_path: str = quote(comment.repo_full_name, safe="")
        url: str = (
            f"/projects/{project_path}"
            f"/merge_requests/{comment.pr_number}"
            f"/notes/{comment.comment_id}/award_emoji"
        )

        try:
            response: httpx.Response = await self._client.post(
                url,
                json={"name": reaction},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "Failed to add reaction to note %d on %s",
                comment.comment_id,
                comment.repo_full_name,
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
            response: httpx.Response = await self._client.get(url)
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

    async def fetch_pr_branch(self, comment: ReviewComment) -> str:
        """
        Resolve the head branch for a merge request.

        GitLab webhooks always include the source branch, so this is a no-op.

        Args:
            comment (ReviewComment): The comment with repo and MR info.

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
                f"/discussions?per_page=100&page={page}"
            )

            try:
                response: httpx.Response = await self._client.get(url)
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

            if len(data) < 100:
                break

            page += 1

        comments.sort(key=lambda comment: comment.created_at)

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
            response: httpx.Response = await self._client.get(url)
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
                status = "added"
            elif deleted_file:
                status = "removed"
            elif renamed:
                status = "renamed"
            else:
                status = "modified"

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
        comment: ReviewComment,
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
            comment (ReviewComment): The original comment that triggered the review.
        """

        await self.post_reply(comment, CommentReply(body=summary))

        project_path: str = quote(repo_full_name, safe="")

        if not findings:
            return

        versions_url: str = (
            f"/projects/{project_path}/merge_requests/{pr_number}/versions"
        )

        try:
            versions_response: httpx.Response = await self._client.get(
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
                "new_path": finding.file_path,
                "new_line": finding.line,
            }

            try:
                discussion_response: httpx.Response = await self._client.post(
                    discussions_url,
                    json={
                        "body": finding.body,
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
        host: str = self.base_url.replace("https://", "").replace("http://", "")

        return f"https://oauth2:{effective_token}@{host}/{repo_full_name}.git"

    def _build_clone_url(self, repo_full_name: str) -> str:
        """
        Build an authenticated clone URL for a GitLab repository.

        Args:
            repo_full_name (str): Full repository name.

        Returns:
            str: The authenticated HTTPS clone URL.
        """

        host: str = self.base_url.replace("https://", "").replace("http://", "")

        return f"https://oauth2:{self.token}@{host}/{repo_full_name}.git"


def _create_gitlab_platform() -> GitLabPlatform | None:
    """
    Factory that builds a GitLabPlatform from environment variables.

    Returns None if ``GITLAB_TOKEN`` is not set, indicating GitLab is not
    configured.

    Returns:
        GitLabPlatform | None: A configured client, or None.
    """

    token: str = os.environ.get("GITLAB_TOKEN", "")

    if not token:
        return None

    webhook_secret: str = os.environ.get("GITLAB_WEBHOOK_SECRET", "")
    base_url: str = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")
    reviewer_token: str = os.environ.get("GITLAB_REVIEWER_TOKEN", "")

    return GitLabPlatform(
        token=token,
        webhook_secret=webhook_secret,
        base_url=base_url,
        reviewer_token=reviewer_token,
    )


register_platform("gitlab", _create_gitlab_platform)
