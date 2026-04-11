# type: ignore
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.config.settings import GitHubConfig
from nominal_code.models import EventType, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    LifecycleEvent,
    PlatformName,
)
from nominal_code.platforms.github import (
    GitHubPatAuth,
    GitHubPlatform,
)
from nominal_code.platforms.github.platform import (
    _format_suggestion_body,
    create_github_platform,
)

EXPECTED_AUTH_HEADERS = {
    "Authorization": "token ghp_test123",
    "Accept": "application/vnd.github.v3+json",
}


@pytest.fixture
def platform():
    auth = GitHubPatAuth(token="ghp_test123")

    return GitHubPlatform(auth=auth, webhook_secret="test-secret")


@pytest.fixture
def platform_no_secret():
    auth = GitHubPatAuth(token="ghp_test123")

    return GitHubPlatform(auth=auth)


def _make_headers(headers=None):
    return headers or {}


def _sign(secret, body):
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    return f"sha256={sig}"


def _make_comment(event_type=EventType.ISSUE_COMMENT):
    return CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="main",
        event_type=event_type,
        comment_id=100,
        author_username="alice",
        body="test",
    )


class TestNameProperty:
    def test_name_returns_github(self, platform):
        assert platform.name == "github"


class TestVerifyWebhook:
    def test_verify_webhook_valid_signature(self, platform):
        body = b'{"test": true}'
        signature = _sign("test-secret", body)
        headers = _make_headers({"X-Hub-Signature-256": signature})

        assert platform.verify_webhook(headers, body) is True

    def test_verify_webhook_invalid_signature(self, platform):
        body = b'{"test": true}'
        headers = _make_headers({"X-Hub-Signature-256": "sha256=invalid"})

        assert platform.verify_webhook(headers, body) is False

    def test_verify_webhook_missing_signature(self, platform):
        body = b'{"test": true}'
        headers = _make_headers({})

        assert platform.verify_webhook(headers, body) is False

    def test_verify_webhook_no_secret_configured(self, platform_no_secret):
        body = b'{"test": true}'
        headers = _make_headers({})

        assert platform_no_secret.verify_webhook(headers, body) is True


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
        headers = _make_headers({"X-GitHub-Event": "issue_comment"})
        result = platform.parse_event(headers, body)

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
        headers = _make_headers({"X-GitHub-Event": "issue_comment"})

        assert platform.parse_event(headers, body) is None

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
        headers = _make_headers({"X-GitHub-Event": "issue_comment"})

        assert platform.parse_event(headers, body) is None

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
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        headers = _make_headers(
            {"X-GitHub-Event": "pull_request_review_comment"},
        )
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.pr_number == 10
        assert result.pr_branch == "feature-branch"
        assert result.base_branch == "main"
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
                "base": {"ref": "develop"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        headers = _make_headers({"X-GitHub-Event": "pull_request_review"})
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.pr_number == 5
        assert result.base_branch == "develop"
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
        headers = _make_headers({"X-GitHub-Event": "pull_request_review"})

        assert platform.parse_event(headers, body) is None

    def test_parse_unknown_event(self, platform):
        body = b'{"action": "opened"}'
        headers = _make_headers({"X-GitHub-Event": "push"})

        assert platform.parse_event(headers, body) is None

    def test_parse_event_does_not_mutate_auth(self, platform):
        payload = {
            "action": "created",
            "installation": {"id": 98765},
            "issue": {
                "number": 42,
                "pull_request": {"url": "https://api.github.com/..."},
            },
            "comment": {
                "id": 100,
                "body": "@bot test",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        headers = _make_headers({"X-GitHub-Event": "issue_comment"})

        platform.parse_event(headers, body)


class TestParsePullRequest:
    def test_parse_pr_opened(self, platform):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 99,
                "title": "Add new feature",
                "draft": False,
                "head": {"ref": "feature-branch"},
                "base": {"ref": "main"},
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "owner/repo"},
        }
        body = json.dumps(payload).encode()
        headers = _make_headers({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.event_type == EventType.PR_OPENED
        assert result.pr_number == 99
        assert result.pr_branch == "feature-branch"
        assert result.base_branch == "main"
        assert result.pr_title == "Add new feature"
        assert result.pr_author == "alice"
        assert isinstance(result, LifecycleEvent)

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
        headers = _make_headers({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(headers, body)

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
        headers = _make_headers({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(headers, body)

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
        headers = _make_headers({"X-GitHub-Event": "pull_request"})
        result = platform.parse_event(headers, body)

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
        headers = _make_headers({"X-GitHub-Event": "pull_request"})

        assert platform.parse_event(headers, body) is None

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
        headers = _make_headers({"X-GitHub-Event": "pull_request"})

        assert platform.parse_event(headers, body) is None


class TestBuildCloneUrl:
    def test_build_clone_url(self, platform):
        url = platform.build_clone_url("owner/repo")

        assert url == "https://x-access-token:ghp_test123@github.com/owner/repo.git"


class TestPostReply:
    @pytest.mark.asyncio
    async def test_post_reply_issue_comment_uses_issues_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="test",
        )
        reply = CommentReply(body="Fixed it!")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_request.assert_called_once_with(
                "POST",
                "/repos/owner/repo/issues/42/comments",
                json={"body": "Fixed it!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_review_comment_uses_threaded_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            event_type=EventType.REVIEW_COMMENT,
            comment_id=200,
            author_username="bob",
            body="test",
            diff_hunk="@@ -1,3 +1,5 @@",
            file_path="src/main.py",
        )
        reply = CommentReply(body="Refactored!")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_request.assert_called_once_with(
                "POST",
                "/repos/owner/repo/pulls/42/comments/200/replies",
                json={"body": "Refactored!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_review_uses_issues_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            event_type=EventType.REVIEW,
            comment_id=300,
            author_username="charlie",
            body="test",
        )
        reply = CommentReply(body="Done!")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_request.assert_called_once_with(
                "POST",
                "/repos/owner/repo/issues/42/comments",
                json={"body": "Done!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_with_commit_sha(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="test",
        )
        reply = CommentReply(body="Done", commit_sha="abc123")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.post_reply(comment, reply)

            call_args = mock_request.call_args
            posted_body = call_args[1]["json"]["body"]

            assert "abc123" in posted_body


class TestPostReaction:
    @pytest.mark.asyncio
    async def test_post_reaction_success_first_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="test",
        )
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.post_reaction(comment, "eyes")

            mock_request.assert_called_once()


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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [mock_response_1, mock_response_2]
            files = await platform.fetch_pr_diff("owner/repo", 42)

        assert len(files) == 101
        assert mock_request.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = httpx.HTTPError("connection failed")
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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.submit_review(
                "owner/repo",
                42,
                findings,
                "Found issues",
                comment,
            )

            mock_request.assert_called_once()
            call_kwargs = mock_request.call_args[1]

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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [
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

            assert mock_request.call_count == 2


class TestFormatSuggestionBody:
    def test_format_suggestion_body_plain_comment(self):
        finding = ReviewFinding(file_path="src/main.py", line=10, body="Bug here")

        assert _format_suggestion_body(finding) == "Bug here"

    def test_format_suggestion_body_with_suggestion(self):
        finding = ReviewFinding(
            file_path="src/main.py",
            line=10,
            body="Use snake_case",
            suggestion="user_count = len(users)",
        )
        result = _format_suggestion_body(finding)

        assert "```suggestion" in result
        assert "user_count = len(users)" in result
        assert result.endswith("```")


class TestSubmitReviewSuggestion:
    @pytest.mark.asyncio
    async def test_submit_review_suggestion_includes_start_line(self, platform):
        comment = _make_comment()
        findings = [
            ReviewFinding(
                file_path="src/main.py",
                line=20,
                body="Simplify",
                suggestion="simplified()",
                start_line=18,
            ),
        ]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.submit_review(
                "owner/repo",
                42,
                findings,
                "Found issues",
                comment,
            )

            call_kwargs = mock_request.call_args[1]
            review_comment = call_kwargs["json"]["comments"][0]

            assert review_comment["start_line"] == 18
            assert review_comment["start_side"] == "RIGHT"
            assert "```suggestion" in review_comment["body"]


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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [mock_issue_resp, mock_review_resp]
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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [mock_issue_resp, mock_review_resp]
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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [mock_resp1, mock_resp2, mock_review_resp]
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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [
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
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [mock_issue_resp, mock_review_resp]
            result = await platform.fetch_pr_comments("owner/repo", 42)

        assert result[0].line == 0


class TestFactory:
    def test_factory_returns_platform_when_token_set(self):
        config = GitHubConfig(token="ghp_test123", webhook_secret="secret")
        result = create_github_platform(config)

        assert result is not None
        assert isinstance(result, GitHubPlatform)
        assert isinstance(result.auth, GitHubPatAuth)
        assert result.auth.get_api_token() == "ghp_test123"
        assert result.webhook_secret == "secret"

    def test_factory_returns_none_when_no_token(self):
        config = GitHubConfig()
        result = create_github_platform(config)

        assert result is None

    def test_factory_returns_app_auth_when_app_id_and_key_set(self):
        from nominal_code.platforms.github import GitHubAppAuth

        config = GitHubConfig(
            app_id="12345",
            private_key="fake-pem-key",
            installation_id=67890,
            webhook_secret="secret",
        )
        result = create_github_platform(config)

        assert result is not None
        assert isinstance(result.auth, GitHubAppAuth)
        assert result.auth.app_id == "12345"
        assert result._fixed_installation_id == 67890
        assert result.webhook_secret == "secret"

    def test_factory_prefers_app_auth_over_pat(self):
        from nominal_code.platforms.github import GitHubAppAuth

        config = GitHubConfig(
            app_id="12345",
            private_key="fake-pem-key",
            token="ghp_test123",
        )
        result = create_github_platform(config)

        assert result is not None
        assert isinstance(result.auth, GitHubAppAuth)


class TestGitHubPlatformInit:
    def test_init_stores_auth(self):
        auth = GitHubPatAuth(token="ghp_test")
        platform = GitHubPlatform(auth=auth)

        assert platform.auth is auth

    def test_init_stores_webhook_secret(self):
        auth = GitHubPatAuth(token="ghp_test")
        platform = GitHubPlatform(auth=auth, webhook_secret="my-secret")

        assert platform.webhook_secret == "my-secret"

    def test_init_webhook_secret_defaults_to_none(self):
        auth = GitHubPatAuth(token="ghp_test")
        platform = GitHubPlatform(auth=auth)

        assert platform.webhook_secret is None

    def test_init_creates_http_client(self):
        auth = GitHubPatAuth(token="ghp_test")
        platform = GitHubPlatform(auth=auth)

        assert platform._client is not None


class TestActiveInstallationId:
    def test_returns_fixed_installation_id_when_context_var_unset(self):
        from nominal_code.platforms.github import GitHubAppAuth

        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        platform = GitHubPlatform(auth=auth, fixed_installation_id=99999)

        assert platform._active_installation_id() == 99999

    def test_returns_zero_when_no_fixed_id_and_context_var_unset(self):
        auth = GitHubPatAuth(token="ghp_test")
        platform = GitHubPlatform(auth=auth)

        assert platform._active_installation_id() == 0


class TestAuthHeaders:
    def test_auth_headers_contains_authorization(self, platform):
        headers = platform._auth_headers()

        assert "Authorization" in headers
        assert headers["Authorization"] == "token ghp_test123"

    def test_auth_headers_contains_accept(self, platform):
        headers = platform._auth_headers()

        assert headers["Accept"] == "application/vnd.github.v3+json"


class TestExtractInstallationId:
    def test_returns_installation_id(self, platform):
        payload = {
            "installation": {"id": 98765},
            "action": "created",
        }
        body = json.dumps(payload).encode()

        assert platform.extract_installation_id(body) == 98765

    def test_returns_zero_when_missing(self, platform):
        payload = {"action": "created"}
        body = json.dumps(payload).encode()

        assert platform.extract_installation_id(body) == 0

    def test_returns_zero_on_malformed_json(self, platform):
        assert platform.extract_installation_id(b"not json") == 0


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_authenticate_with_webhook_body_extracts_and_delegates(
        self, platform
    ):
        payload = {"installation": {"id": 98765}}
        body = json.dumps(payload).encode()

        with patch.object(platform.auth, "ensure_auth", new=AsyncMock()) as mock:
            await platform.authenticate(webhook_body=body)

            mock.assert_awaited_once_with(98765)

    @pytest.mark.asyncio
    async def test_authenticate_with_webhook_body_without_installation(self, platform):
        body = json.dumps({"action": "created"}).encode()

        with patch.object(platform.auth, "ensure_auth", new=AsyncMock()) as mock:
            await platform.authenticate(webhook_body=body)

            mock.assert_awaited_once_with(0)

    @pytest.mark.asyncio
    async def test_authenticate_without_webhook_body_delegates(self, platform):
        with patch.object(platform.auth, "ensure_auth", new=AsyncMock()) as mock:
            await platform.authenticate()

            mock.assert_awaited_once_with(0)


class TestFetchIssueComments:
    @pytest.mark.asyncio
    async def test_fetch_issue_comments_single_page(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "user": {"login": "alice"},
                "body": "Nice work!",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]

        with patch.object(
            platform,
            "_request",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await platform._fetch_issue_comments("owner/repo", 42)

        assert len(result) == 1
        assert result[0].author == "alice"
        assert result[0].body == "Nice work!"

    @pytest.mark.asyncio
    async def test_fetch_issue_comments_empty_page_stops(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        with patch.object(
            platform,
            "_request",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await platform._fetch_issue_comments("owner/repo", 42)

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_issue_comments_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform,
            "_request",
            new=AsyncMock(side_effect=httpx.HTTPError("connection error")),
        ):
            result = await platform._fetch_issue_comments("owner/repo", 42)

        assert result == []


class TestFetchReviewComments:
    @pytest.mark.asyncio
    async def test_fetch_review_comments_returns_inline_comments(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "user": {"login": "bob"},
                "body": "Fix this line.",
                "path": "src/main.py",
                "line": 42,
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]

        with patch.object(
            platform,
            "_request",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await platform._fetch_review_comments("owner/repo", 42)

        assert len(result) == 1
        assert result[0].author == "bob"
        assert result[0].file_path == "src/main.py"
        assert result[0].line == 42

    @pytest.mark.asyncio
    async def test_fetch_review_comments_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform,
            "_request",
            new=AsyncMock(side_effect=httpx.HTTPError("timeout")),
        ):
            result = await platform._fetch_review_comments("owner/repo", 42)

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_review_comments_empty_response(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        with patch.object(
            platform,
            "_request",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await platform._fetch_review_comments("owner/repo", 42)

        assert result == []
