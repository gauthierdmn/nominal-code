# type: ignore
import hashlib
import hmac
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.bot_type import EventType, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentReply,
    PlatformName,
    PullRequestEvent,
)
from nominal_code.platforms.github import (
    GitHubPlatform,
    _create_github_platform,
)


@pytest.fixture
def platform():
    return GitHubPlatform(token="ghp_test123", webhook_secret="test-secret")


@pytest.fixture
def platform_no_secret():
    return GitHubPlatform(token="ghp_test123")


@pytest.fixture
def platform_with_reviewer_token():
    return GitHubPlatform(
        token="ghp_test123",
        webhook_secret="test-secret",
        reviewer_token="ghp_readonly456",
    )


def _make_request(headers=None, body=b""):
    request = MagicMock()
    request.headers = headers or {}

    return request


def _sign(secret, body):
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    return f"sha256={sig}"


def _make_comment(event_type=EventType.ISSUE_COMMENT):
    return PullRequestEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="main",
        comment_id=100,
        author_username="alice",
        body="test",
        diff_hunk="",
        file_path="",
        clone_url="",
        event_type=event_type,
    )


class TestNameProperty:
    def test_name_returns_github(self, platform):
        assert platform.name == "github"


class TestVerifyWebhook:
    def test_verify_webhook_valid_signature(self, platform):
        body = b'{"test": true}'
        signature = _sign("test-secret", body)
        request = _make_request({"X-Hub-Signature-256": signature})

        assert platform.verify_webhook(request, body) is True

    def test_verify_webhook_invalid_signature(self, platform):
        body = b'{"test": true}'
        request = _make_request({"X-Hub-Signature-256": "sha256=invalid"})

        assert platform.verify_webhook(request, body) is False

    def test_verify_webhook_missing_signature(self, platform):
        body = b'{"test": true}'
        request = _make_request({})

        assert platform.verify_webhook(request, body) is False

    def test_verify_webhook_no_secret_configured(self, platform_no_secret):
        body = b'{"test": true}'
        request = _make_request({})

        assert platform_no_secret.verify_webhook(request, body) is True


class TestParseWebhook:
    def test_parse_issue_comment_on_pr(self, platform):
        payload = {
            "action": "created",
            "issue": {
                "number": 42,
                "pull_request": {"url": "https://api.github.com/..."},
            },
            "comment": {
                "id": 100,
                "body": "@claude-bot fix this",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "issue_comment"})
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.platform == "github"
        assert result.repo_full_name == "owner/repo"
        assert result.pr_number == 42
        assert result.comment_id == 100
        assert result.author_username == "alice"
        assert result.body == "@claude-bot fix this"
        assert result.event_type == EventType.ISSUE_COMMENT

    def test_parse_issue_comment_not_on_pr(self, platform):
        payload = {
            "action": "created",
            "issue": {"number": 42},
            "comment": {"id": 100, "body": "test", "user": {"login": "alice"}},
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "issue_comment"})

        assert platform.parse_event(request, body) is None

    def test_parse_issue_comment_not_created(self, platform):
        payload = {
            "action": "edited",
            "issue": {
                "number": 42,
                "pull_request": {"url": "https://api.github.com/..."},
            },
            "comment": {"id": 100, "body": "test", "user": {"login": "alice"}},
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "issue_comment"})

        assert platform.parse_event(request, body) is None

    def test_parse_review_comment(self, platform):
        payload = {
            "action": "created",
            "comment": {
                "id": 200,
                "body": "@claude-bot refactor this",
                "user": {"login": "bob"},
                "diff_hunk": "@@ -1,3 +1,5 @@",
                "path": "src/main.py",
            },
            "pull_request": {
                "number": 10,
                "head": {"ref": "feature-branch"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request(
            {"X-GitHub-Event": "pull_request_review_comment"},
        )
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.pr_number == 10
        assert result.pr_branch == "feature-branch"
        assert result.diff_hunk == "@@ -1,3 +1,5 @@"
        assert result.file_path == "src/main.py"
        assert result.event_type == EventType.REVIEW_COMMENT

    def test_parse_review_submitted(self, platform):
        payload = {
            "action": "submitted",
            "review": {
                "id": 300,
                "body": "@claude-bot looks good but fix the typo",
                "user": {"login": "charlie"},
            },
            "pull_request": {
                "number": 5,
                "head": {"ref": "fix-branch"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request_review"})
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.pr_number == 5
        assert result.body == "@claude-bot looks good but fix the typo"
        assert result.event_type == EventType.REVIEW

    def test_parse_review_empty_body(self, platform):
        payload = {
            "action": "submitted",
            "review": {
                "id": 300,
                "body": "",
                "user": {"login": "charlie"},
            },
            "pull_request": {"number": 5, "head": {"ref": "fix"}},
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request_review"})

        assert platform.parse_event(request, body) is None

    def test_parse_unknown_event(self, platform):
        body = b'{"action": "opened"}'
        request = _make_request({"X-GitHub-Event": "push"})

        assert platform.parse_event(request, body) is None


class TestParsePullRequest:
    def test_parse_pr_opened(self, platform):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 99,
                "title": "Add new feature",
                "draft": False,
                "head": {"ref": "feature-branch"},
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.event_type == EventType.PR_OPENED
        assert result.pr_number == 99
        assert result.pr_branch == "feature-branch"
        assert result.pr_title == "Add new feature"
        assert result.pr_author == "alice"
        assert result.comment_id == 0
        assert result.body == ""

    def test_parse_pr_synchronize(self, platform):
        payload = {
            "action": "synchronize",
            "pull_request": {
                "number": 99,
                "title": "Update feature",
                "draft": False,
                "head": {"ref": "feature-branch"},
                "user": {"login": "bob"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.event_type == EventType.PR_PUSH

    def test_parse_pr_reopened(self, platform):
        payload = {
            "action": "reopened",
            "pull_request": {
                "number": 99,
                "title": "Reopened PR",
                "draft": False,
                "head": {"ref": "fix-branch"},
                "user": {"login": "charlie"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.event_type == EventType.PR_REOPENED

    def test_parse_pr_ready_for_review(self, platform):
        payload = {
            "action": "ready_for_review",
            "pull_request": {
                "number": 99,
                "title": "Ready PR",
                "draft": False,
                "head": {"ref": "ready-branch"},
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.event_type == EventType.PR_READY_FOR_REVIEW

    def test_parse_pr_draft_skipped(self, platform):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 99,
                "title": "Draft PR",
                "draft": True,
                "head": {"ref": "draft-branch"},
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request"})

        assert platform.parse_event(request, body) is None

    def test_parse_pr_closed_ignored(self, platform):
        payload = {
            "action": "closed",
            "pull_request": {
                "number": 99,
                "title": "Closed PR",
                "draft": False,
                "head": {"ref": "some-branch"},
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        request = _make_request({"X-GitHub-Event": "pull_request"})

        assert platform.parse_event(request, body) is None


class TestBuildCloneUrl:
    def test_build_clone_url(self, platform):
        url = platform._build_clone_url("owner/repo")

        assert url == "https://x-access-token:ghp_test123@github.com/owner/repo.git"


class TestBuildReviewerCloneUrl:
    def test_build_reviewer_clone_url_with_reviewer_token(
        self,
        platform_with_reviewer_token,
    ):
        url = platform_with_reviewer_token.build_reviewer_clone_url("owner/repo")

        assert url == (
            "https://x-access-token:ghp_readonly456@github.com/owner/repo.git"
        )

    def test_build_reviewer_clone_url_falls_back_to_main_token(self, platform):
        url = platform.build_reviewer_clone_url("owner/repo")

        assert url == ("https://x-access-token:ghp_test123@github.com/owner/repo.git")


class TestPostReply:
    @pytest.mark.asyncio
    async def test_post_reply_issue_comment_uses_issues_endpoint(self, platform):
        comment = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="test",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.ISSUE_COMMENT,
        )
        reply = CommentReply(body="Fixed it!")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_post.assert_called_once_with(
                "/repos/owner/repo/issues/42/comments",
                json={"body": "Fixed it!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_review_comment_uses_threaded_endpoint(self, platform):
        comment = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            comment_id=200,
            author_username="bob",
            body="test",
            diff_hunk="@@ -1,3 +1,5 @@",
            file_path="src/main.py",
            clone_url="",
            event_type=EventType.REVIEW_COMMENT,
        )
        reply = CommentReply(body="Refactored!")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_post.assert_called_once_with(
                "/repos/owner/repo/pulls/42/comments/200/replies",
                json={"body": "Refactored!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_review_uses_issues_endpoint(self, platform):
        comment = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            comment_id=300,
            author_username="charlie",
            body="test",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.REVIEW,
        )
        reply = CommentReply(body="Done!")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_post.assert_called_once_with(
                "/repos/owner/repo/issues/42/comments",
                json={"body": "Done!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_with_commit_sha(self, platform):
        comment = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="test",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.ISSUE_COMMENT,
        )
        reply = CommentReply(body="Done", commit_sha="abc123")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reply(comment, reply)

            call_args = mock_post.call_args
            posted_body = call_args[1]["json"]["body"]

            assert "abc123" in posted_body


class TestPostReaction:
    @pytest.mark.asyncio
    async def test_post_reaction_success_first_endpoint(self, platform):
        comment = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="test",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.ISSUE_COMMENT,
        )
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reaction(comment, "eyes")

            mock_post.assert_called_once()


class TestIsPrOpen:
    @pytest.mark.asyncio
    async def test_is_pr_open_returns_true_when_open(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"state": "open"}

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            result = await platform.is_pr_open("owner/repo", 42)

        assert result is True
        mock_get.assert_called_once_with("/repos/owner/repo/pulls/42")

    @pytest.mark.asyncio
    async def test_is_pr_open_returns_false_when_closed(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"state": "closed"}

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            result = await platform.is_pr_open("owner/repo", 42)

        assert result is False

    @pytest.mark.asyncio
    async def test_is_pr_open_returns_true_on_http_error(self, platform):
        import httpx

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = httpx.HTTPError("connection failed")
            result = await platform.is_pr_open("owner/repo", 42)

        assert result is True


class TestFetchPrDiff:
    @pytest.mark.asyncio
    async def test_fetch_pr_diff_returns_changed_files(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "filename": "src/main.py",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n-old\n+new",
            },
            {
                "filename": "src/new.py",
                "status": "added",
                "patch": "@@ -0,0 +1 @@\n+line",
            },
        ]

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            files = await platform.fetch_pr_diff("owner/repo", 42)

        assert len(files) == 2
        assert files[0].file_path == "src/main.py"
        assert files[0].status == FileStatus.MODIFIED
        assert files[1].file_path == "src/new.py"

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_paginates(self, platform):
        page1_data = [
            {"filename": f"file{idx}.py", "status": "modified", "patch": ""}
            for idx in range(100)
        ]
        page2_data = [
            {"filename": "last.py", "status": "added", "patch": ""},
        ]

        mock_response_1 = MagicMock()
        mock_response_1.raise_for_status = MagicMock()
        mock_response_1.json.return_value = page1_data

        mock_response_2 = MagicMock()
        mock_response_2.raise_for_status = MagicMock()
        mock_response_2.json.return_value = page2_data

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [mock_response_1, mock_response_2]
            files = await platform.fetch_pr_diff("owner/repo", 42)

        assert len(files) == 101
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = httpx.HTTPError("connection failed")
            files = await platform.fetch_pr_diff("owner/repo", 42)

        assert files == []


class TestSubmitReview:
    @pytest.mark.asyncio
    async def test_submit_review_success(self, platform):
        comment = _make_comment()
        findings = [
            ReviewFinding(file_path="src/main.py", line=10, body="Bug here"),
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.submit_review(
                "owner/repo",
                42,
                findings,
                "Found issues",
                comment,
            )

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]

            assert call_kwargs["json"]["event"] == "COMMENT"
            assert call_kwargs["json"]["body"] == "Found issues"
            assert len(call_kwargs["json"]["comments"]) == 1

    @pytest.mark.asyncio
    async def test_submit_review_fallback_on_failure(self, platform):
        import httpx

        comment = _make_comment()
        findings = [
            ReviewFinding(file_path="src/main.py", line=10, body="Bug here"),
        ]

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.side_effect = [
                httpx.HTTPError("review API failed"),
                MagicMock(raise_for_status=MagicMock()),
            ]

            await platform.submit_review(
                "owner/repo",
                42,
                findings,
                "Found issues",
                comment,
            )

            assert mock_post.call_count == 2


class TestFetchPrComments:
    @pytest.mark.asyncio
    async def test_fetch_pr_comments_merges_both_endpoints(self, platform):
        issue_comments = [
            {
                "user": {"login": "alice"},
                "body": "Looks good",
                "created_at": "2026-01-01T10:00:00Z",
            },
        ]
        review_comments = [
            {
                "user": {"login": "bob"},
                "body": "Bug on this line",
                "path": "src/main.py",
                "line": 42,
                "created_at": "2026-01-01T09:00:00Z",
            },
        ]

        mock_issue_resp = MagicMock()
        mock_issue_resp.raise_for_status = MagicMock()
        mock_issue_resp.json.return_value = issue_comments

        mock_review_resp = MagicMock()
        mock_review_resp.raise_for_status = MagicMock()
        mock_review_resp.json.return_value = review_comments

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [mock_issue_resp, mock_review_resp]
            result = await platform.fetch_pr_comments("owner/repo", 42)

        assert len(result) == 2
        assert result[0].author == "bob"
        assert result[0].file_path == "src/main.py"
        assert result[0].line == 42
        assert result[1].author == "alice"
        assert result[1].file_path == ""
        assert result[1].line == 0

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_sorted_by_created_at(self, platform):
        issue_comments = [
            {
                "user": {"login": "alice"},
                "body": "First",
                "created_at": "2026-01-01T08:00:00Z",
            },
        ]
        review_comments = [
            {
                "user": {"login": "bob"},
                "body": "Second",
                "path": "a.py",
                "line": 1,
                "created_at": "2026-01-01T12:00:00Z",
            },
        ]

        mock_issue_resp = MagicMock()
        mock_issue_resp.raise_for_status = MagicMock()
        mock_issue_resp.json.return_value = issue_comments

        mock_review_resp = MagicMock()
        mock_review_resp.raise_for_status = MagicMock()
        mock_review_resp.json.return_value = review_comments

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [mock_issue_resp, mock_review_resp]
            result = await platform.fetch_pr_comments("owner/repo", 42)

        assert result[0].created_at < result[1].created_at

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_paginates_issue_comments(self, platform):
        page1 = [
            {
                "user": {"login": f"user{idx}"},
                "body": f"comment {idx}",
                "created_at": f"2026-01-01T{idx:02d}:00:00Z",
            }
            for idx in range(100)
        ]
        page2 = [
            {
                "user": {"login": "last"},
                "body": "last comment",
                "created_at": "2026-01-02T00:00:00Z",
            },
        ]

        mock_resp1 = MagicMock()
        mock_resp1.raise_for_status = MagicMock()
        mock_resp1.json.return_value = page1

        mock_resp2 = MagicMock()
        mock_resp2.raise_for_status = MagicMock()
        mock_resp2.json.return_value = page2

        mock_review_resp = MagicMock()
        mock_review_resp.raise_for_status = MagicMock()
        mock_review_resp.json.return_value = []

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [mock_resp1, mock_resp2, mock_review_resp]
            result = await platform.fetch_pr_comments("owner/repo", 42)

        assert len(result) == 101

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_http_error_returns_partial(self, platform):
        import httpx

        mock_review_resp = MagicMock()
        mock_review_resp.raise_for_status = MagicMock()
        mock_review_resp.json.return_value = [
            {
                "user": {"login": "bob"},
                "body": "inline",
                "path": "a.py",
                "line": 1,
                "created_at": "2026-01-01T10:00:00Z",
            },
        ]

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [
                httpx.HTTPError("issue comments failed"),
                mock_review_resp,
            ]
            result = await platform.fetch_pr_comments("owner/repo", 42)

        assert len(result) == 1
        assert result[0].author == "bob"

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_null_line_defaults_to_zero(self, platform):
        mock_issue_resp = MagicMock()
        mock_issue_resp.raise_for_status = MagicMock()
        mock_issue_resp.json.return_value = []

        mock_review_resp = MagicMock()
        mock_review_resp.raise_for_status = MagicMock()
        mock_review_resp.json.return_value = [
            {
                "user": {"login": "bob"},
                "body": "outdated",
                "path": "a.py",
                "line": None,
                "created_at": "2026-01-01T10:00:00Z",
            },
        ]

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [mock_issue_resp, mock_review_resp]
            result = await platform.fetch_pr_comments("owner/repo", 42)

        assert result[0].line == 0


class TestFactory:
    def test_factory_returns_platform_when_token_set(self):
        env = {
            "GITHUB_TOKEN": "ghp_test123",
            "GITHUB_WEBHOOK_SECRET": "secret",
        }

        with patch.dict(os.environ, env, clear=True):
            result = _create_github_platform()

        assert result is not None
        assert isinstance(result, GitHubPlatform)
        assert result.token == "ghp_test123"
        assert result.webhook_secret == "secret"

    def test_factory_returns_none_when_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _create_github_platform()

        assert result is None

    def test_factory_reads_reviewer_token(self):
        env = {
            "GITHUB_TOKEN": "ghp_test123",
            "GITHUB_REVIEWER_TOKEN": "ghp_readonly",
        }

        with patch.dict(os.environ, env, clear=True):
            result = _create_github_platform()

        assert result is not None
        assert result.reviewer_token == "ghp_readonly"
