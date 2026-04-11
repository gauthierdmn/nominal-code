# type: ignore
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.config.settings import GitLabConfig
from nominal_code.models import DiffSide, EventType, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    LifecycleEvent,
    PlatformName,
)
from nominal_code.platforms.gitlab import GitLabPlatform
from nominal_code.platforms.gitlab.auth import GitLabPatAuth
from nominal_code.platforms.gitlab.platform import (
    _format_suggestion_body,
    create_gitlab_platform,
)


@pytest.fixture
def platform():
    auth = GitLabPatAuth(token="glpat-test456")

    return GitLabPlatform(
        auth=auth,
        webhook_secret="gl-secret",
        base_url="https://gitlab.com",
    )


@pytest.fixture
def platform_no_secret():
    auth = GitLabPatAuth(token="glpat-test456")

    return GitLabPlatform(auth=auth)


def _make_headers(headers=None):
    return headers or {}


def _make_comment():
    return CommentEvent(
        platform=PlatformName.GITLAB,
        repo_full_name="group/repo",
        pr_number=10,
        pr_branch="feature",
        event_type=EventType.NOTE,
        comment_id=500,
        author_username="alice",
        body="test",
    )


def _note_payload(
    note="@claude-bot fix this",
    username="alice",
    noteable_type="MergeRequest",
    iid=10,
    source_branch="feature",
    project_id=123,
    path_with_namespace="group/repo",
    note_id=500,
    discussion_id="abc123def456",
):
    return {
        "object_kind": "note",
        "user": {"username": username},
        "project": {
            "id": project_id,
            "path_with_namespace": path_with_namespace,
        },
        "object_attributes": {
            "id": note_id,
            "note": note,
            "noteable_type": noteable_type,
            "discussion_id": discussion_id,
        },
        "merge_request": {
            "iid": iid,
            "source_branch": source_branch,
            "target_branch": "main",
        },
    }


class TestNameProperty:
    def test_name_returns_gitlab(self, platform):
        assert platform.name == "gitlab"


class TestVerifyWebhook:
    def test_verify_webhook_valid_token(self, platform):
        headers = _make_headers({"X-Gitlab-Token": "gl-secret"})

        assert platform.verify_webhook(headers, b"") is True

    def test_verify_webhook_invalid_token(self, platform):
        headers = _make_headers({"X-Gitlab-Token": "wrong"})

        assert platform.verify_webhook(headers, b"") is False

    def test_verify_webhook_missing_token(self, platform):
        headers = _make_headers({})

        assert platform.verify_webhook(headers, b"") is False

    def test_verify_webhook_no_secret_configured(self, platform_no_secret):
        headers = _make_headers({})

        assert platform_no_secret.verify_webhook(headers, b"") is True


class TestParseWebhook:
    def test_parse_mr_note(self, platform):
        payload = _note_payload()
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.platform == "gitlab"
        assert result.repo_full_name == "group/repo"
        assert result.pr_number == 10
        assert result.pr_branch == "feature"
        assert result.comment_id == 500
        assert result.author_username == "alice"
        assert result.base_branch == "main"
        assert result.body == "@claude-bot fix this"
        assert result.event_type == EventType.NOTE
        assert result.discussion_id == "abc123def456"

    def test_parse_mr_note_without_discussion_id(self, platform):
        payload = _note_payload(discussion_id="")
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.discussion_id == ""

    def test_parse_non_note_event(self, platform):
        payload = {"object_kind": "push"}
        body = json.dumps(payload).encode()
        headers = _make_headers()

        assert platform.parse_event(headers, body) is None

    def test_parse_note_on_issue_not_mr(self, platform):
        payload = _note_payload(noteable_type="Issue")
        body = json.dumps(payload).encode()
        headers = _make_headers()

        assert platform.parse_event(headers, body) is None

    def test_parse_note_with_position(self, platform):
        payload = _note_payload()
        payload["object_attributes"]["position"] = {
            "new_path": "src/main.py",
            "old_path": "src/old.py",
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.file_path == "src/main.py"

    def test_parse_clone_url(self, platform):
        payload = _note_payload()
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.clone_url == ""


class TestParseMergeRequest:
    def test_parse_mr_opened(self, platform):
        payload = {
            "object_kind": "merge_request",
            "user": {"username": "alice"},
            "project": {"path_with_namespace": "group/repo"},
            "object_attributes": {
                "iid": 5,
                "action": "open",
                "source_branch": "feature",
                "target_branch": "main",
                "title": "New feature",
                "work_in_progress": False,
            },
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.event_type == EventType.PR_OPENED
        assert result.pr_number == 5
        assert result.pr_branch == "feature"
        assert result.base_branch == "main"
        assert result.pr_title == "New feature"
        assert result.pr_author == "alice"
        assert isinstance(result, LifecycleEvent)

    def test_parse_mr_reopen(self, platform):
        payload = {
            "object_kind": "merge_request",
            "user": {"username": "bob"},
            "project": {"path_with_namespace": "group/repo"},
            "object_attributes": {
                "iid": 5,
                "action": "reopen",
                "source_branch": "fix",
                "title": "Reopened MR",
                "work_in_progress": False,
            },
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.event_type == EventType.PR_REOPENED

    def test_parse_mr_update_with_oldrev(self, platform):
        payload = {
            "object_kind": "merge_request",
            "user": {"username": "charlie"},
            "project": {"path_with_namespace": "group/repo"},
            "object_attributes": {
                "iid": 5,
                "action": "update",
                "oldrev": "abc123",
                "source_branch": "feature",
                "title": "Push event",
                "work_in_progress": False,
            },
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()
        result = platform.parse_event(headers, body)

        assert result is not None
        assert result.event_type == EventType.PR_PUSH

    def test_parse_mr_update_without_oldrev_ignored(self, platform):
        payload = {
            "object_kind": "merge_request",
            "user": {"username": "alice"},
            "project": {"path_with_namespace": "group/repo"},
            "object_attributes": {
                "iid": 5,
                "action": "update",
                "source_branch": "feature",
                "title": "Title change",
                "work_in_progress": False,
            },
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()

        assert platform.parse_event(headers, body) is None

    def test_parse_mr_wip_skipped(self, platform):
        payload = {
            "object_kind": "merge_request",
            "user": {"username": "alice"},
            "project": {"path_with_namespace": "group/repo"},
            "object_attributes": {
                "iid": 5,
                "action": "open",
                "source_branch": "wip-branch",
                "title": "WIP: Draft MR",
                "work_in_progress": True,
            },
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()

        assert platform.parse_event(headers, body) is None

    def test_parse_mr_close_ignored(self, platform):
        payload = {
            "object_kind": "merge_request",
            "user": {"username": "alice"},
            "project": {"path_with_namespace": "group/repo"},
            "object_attributes": {
                "iid": 5,
                "action": "close",
                "source_branch": "feature",
                "title": "Closed MR",
                "work_in_progress": False,
            },
        }
        body = json.dumps(payload).encode()
        headers = _make_headers()

        assert platform.parse_event(headers, body) is None


class TestFetchPrBranch:
    @pytest.mark.asyncio
    async def test_fetch_pr_branch_returns_empty(self, platform):
        result = await platform.fetch_pr_branch("group/repo", 10)

        assert result == ""


class TestBuildCloneUrl:
    def test_build_clone_url(self, platform):
        url = platform.build_clone_url("group/repo")

        assert url == "https://oauth2:glpat-test456@gitlab.com/group/repo.git"


class TestPostReply:
    @pytest.mark.asyncio
    async def test_post_reply_with_discussion_id_uses_threaded_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=10,
            pr_branch="feature",
            event_type=EventType.NOTE,
            comment_id=500,
            author_username="alice",
            body="test",
            discussion_id="abc123def456",
        )
        reply = CommentReply(body="Fixed!")
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
                "/projects/group%2Frepo/merge_requests/10/discussions/abc123def456/notes",
                json={"body": "Fixed!"},
            )

    @pytest.mark.asyncio
    async def test_post_reply_without_discussion_id_uses_notes_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=10,
            pr_branch="feature",
            event_type=EventType.NOTE,
            comment_id=500,
            author_username="alice",
            body="test",
            discussion_id="",
        )
        reply = CommentReply(body="Fixed!")
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
                "/projects/group%2Frepo/merge_requests/10/notes",
                json={"body": "Fixed!"},
            )


class TestPostReaction:
    @pytest.mark.asyncio
    async def test_post_reaction_success(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=10,
            pr_branch="feature",
            event_type=EventType.NOTE,
            comment_id=500,
            author_username="alice",
            body="test",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response

            await platform.post_reaction(comment, "eyes")

            mock_request.assert_called_once()
            call_args = mock_request.call_args

            assert "award_emoji" in call_args[0][1]


class TestFetchPrDiff:
    @pytest.mark.asyncio
    async def test_fetch_pr_diff_returns_changed_files(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "new_path": "src/main.py",
                "old_path": "src/main.py",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -1 +1 @@\n-old\n+new",
            },
            {
                "new_path": "src/new.py",
                "old_path": "src/new.py",
                "new_file": True,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -0,0 +1 @@\n+line",
            },
        ]

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            files = await platform.fetch_pr_diff("group/repo", 10)

        assert len(files) == 2
        assert files[0].file_path == "src/main.py"
        assert files[0].status == FileStatus.MODIFIED
        assert files[1].file_path == "src/new.py"
        assert files[1].status == FileStatus.ADDED

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_deleted_file(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "new_path": "old.py",
                "old_path": "old.py",
                "new_file": False,
                "deleted_file": True,
                "renamed_file": False,
                "diff": "",
            },
        ]

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            files = await platform.fetch_pr_diff("group/repo", 10)

        assert files[0].status == FileStatus.REMOVED

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_renamed_file(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "new_path": "new_name.py",
                "old_path": "old_name.py",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": True,
                "diff": "",
            },
        ]

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            files = await platform.fetch_pr_diff("group/repo", 10)

        assert files[0].status == FileStatus.RENAMED
        assert files[0].file_path == "new_name.py"

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = httpx.HTTPError("connection failed")
            files = await platform.fetch_pr_diff("group/repo", 10)

        assert files == []


class TestSubmitReview:
    @pytest.mark.asyncio
    async def test_submit_review_posts_summary_and_discussions(self, platform):
        comment = _make_comment()
        findings = [
            ReviewFinding(file_path="src/main.py", line=10, body="Bug here"),
        ]

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status = MagicMock()

        mock_versions_response = MagicMock()
        mock_versions_response.raise_for_status = MagicMock()
        mock_versions_response.json.return_value = [
            {
                "base_commit_sha": "base123",
                "head_commit_sha": "head456",
                "start_commit_sha": "start789",
            },
        ]

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [
                mock_post_response,
                mock_versions_response,
                mock_post_response,
            ]

            await platform.submit_review(
                "group/repo",
                10,
                findings,
                "Found issues",
                comment,
            )

            assert mock_request.call_count == 3

            summary_call = mock_request.call_args_list[0]

            assert summary_call[0][0] == "POST"
            assert summary_call[1]["json"]["body"] == "Found issues"

            versions_call = mock_request.call_args_list[1]

            assert versions_call[0][0] == "GET"

            discussion_call = mock_request.call_args_list[2]
            position = discussion_call[1]["json"]["position"]

            assert position["base_sha"] == "base123"
            assert position["new_path"] == "src/main.py"
            assert position["new_line"] == 10

    @pytest.mark.asyncio
    async def test_submit_review_no_findings_skips_discussions(self, platform):
        comment = _make_comment()

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_post_response

            await platform.submit_review(
                "group/repo",
                10,
                [],
                "No issues found",
                comment,
            )

            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_review_left_side_uses_old_path_and_old_line(self, platform):
        comment = _make_comment()
        findings = [
            ReviewFinding(
                file_path="src/deleted.py",
                line=5,
                body="Removed code had a bug",
                side=DiffSide.LEFT,
            ),
        ]

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status = MagicMock()

        mock_versions_response = MagicMock()
        mock_versions_response.raise_for_status = MagicMock()
        mock_versions_response.json.return_value = [
            {
                "base_commit_sha": "base123",
                "head_commit_sha": "head456",
                "start_commit_sha": "start789",
            },
        ]

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [
                mock_post_response,
                mock_versions_response,
                mock_post_response,
            ]

            await platform.submit_review(
                "group/repo",
                10,
                findings,
                "Found deletion issue",
                comment,
            )

            discussion_call = mock_request.call_args_list[2]
            position = discussion_call[1]["json"]["position"]

            assert position["old_path"] == "src/deleted.py"
            assert position["old_line"] == 5
            assert "new_path" not in position
            assert "new_line" not in position

    @pytest.mark.asyncio
    async def test_submit_review_version_fetch_failure_skips_inline_comments(
        self,
        platform,
    ):
        import httpx

        comment = _make_comment()
        findings = [
            ReviewFinding(file_path="src/main.py", line=10, body="Bug"),
        ]

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status = MagicMock()

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = [
                mock_post_response,
                httpx.HTTPError("version fetch failed"),
            ]

            await platform.submit_review(
                "group/repo",
                10,
                findings,
                "Found issues",
                comment,
            )

            assert mock_request.call_count == 2


class TestFetchPrComments:
    @pytest.mark.asyncio
    async def test_fetch_pr_comments_flattens_discussions(self, platform):
        discussions = [
            {
                "notes": [
                    {
                        "author": {"username": "alice"},
                        "body": "First note",
                        "created_at": "2026-01-01T10:00:00Z",
                    },
                    {
                        "author": {"username": "bob"},
                        "body": "Reply",
                        "created_at": "2026-01-01T11:00:00Z",
                    },
                ],
            },
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = discussions

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert len(result) == 2
        assert result[0].author == "alice"
        assert result[1].author == "bob"

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_extracts_position(self, platform):
        discussions = [
            {
                "notes": [
                    {
                        "author": {"username": "alice"},
                        "body": "Inline comment",
                        "created_at": "2026-01-01T10:00:00Z",
                        "position": {
                            "new_path": "src/main.py",
                            "new_line": 42,
                        },
                    },
                ],
            },
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = discussions

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert result[0].file_path == "src/main.py"
        assert result[0].line == 42

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_resolved_status(self, platform):
        discussions = [
            {
                "resolved": True,
                "notes": [
                    {
                        "author": {"username": "alice"},
                        "body": "Fixed",
                        "created_at": "2026-01-01T10:00:00Z",
                    },
                ],
            },
            {
                "resolved": False,
                "notes": [
                    {
                        "author": {"username": "bob"},
                        "body": "Still open",
                        "created_at": "2026-01-01T11:00:00Z",
                    },
                ],
            },
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = discussions

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert result[0].is_resolved is True
        assert result[1].is_resolved is False

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_skips_system_notes(self, platform):
        discussions = [
            {
                "notes": [
                    {
                        "author": {"username": "alice"},
                        "body": "Real comment",
                        "created_at": "2026-01-01T10:00:00Z",
                        "system": False,
                    },
                    {
                        "author": {"username": "system"},
                        "body": "added 1 commit",
                        "created_at": "2026-01-01T10:01:00Z",
                        "system": True,
                    },
                ],
            },
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = discussions

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert len(result) == 1
        assert result[0].body == "Real comment"

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.side_effect = httpx.HTTPError("connection failed")
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_sorted_by_created_at(self, platform):
        discussions = [
            {
                "notes": [
                    {
                        "author": {"username": "bob"},
                        "body": "Later",
                        "created_at": "2026-01-01T12:00:00Z",
                    },
                ],
            },
            {
                "notes": [
                    {
                        "author": {"username": "alice"},
                        "body": "Earlier",
                        "created_at": "2026-01-01T08:00:00Z",
                    },
                ],
            },
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = discussions

        with patch.object(
            platform,
            "_request",
            new_callable=AsyncMock,
        ) as mock_request:
            mock_request.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert result[0].author == "alice"
        assert result[1].author == "bob"


class TestFormatSuggestionBody:
    def test_format_suggestion_body_plain_comment(self):
        finding = ReviewFinding(file_path="src/main.py", line=10, body="Bug here")

        assert _format_suggestion_body(finding) == "Bug here"

    def test_format_suggestion_body_single_line(self):
        finding = ReviewFinding(
            file_path="src/main.py",
            line=10,
            body="Use snake_case",
            suggestion="user_count = len(users)",
        )
        result = _format_suggestion_body(finding)

        assert "```suggestion:-0+0" in result
        assert "user_count = len(users)" in result
        assert result.endswith("```")

    def test_format_suggestion_body_multiline(self):
        finding = ReviewFinding(
            file_path="src/main.py",
            line=20,
            body="Simplify",
            suggestion="if items:\n    process(items)",
            start_line=18,
        )
        result = _format_suggestion_body(finding)

        assert "```suggestion:-2+0" in result
        assert "if items:\n    process(items)" in result


class TestFactory:
    def test_factory_returns_platform_when_token_set(self):
        config = GitLabConfig(
            token="glpat-test456",
            webhook_secret="secret",
            api_base="https://git.example.com",
        )
        result = create_gitlab_platform(config)

        assert result is not None
        assert isinstance(result, GitLabPlatform)
        assert result.auth.get_api_token() == "glpat-test456"
        assert result.webhook_secret == "secret"
        assert result.base_url == "https://git.example.com"

    def test_factory_returns_none_when_no_token(self):
        config = GitLabConfig()
        result = create_gitlab_platform(config)

        assert result is None

    def test_factory_uses_default_base_url(self):
        config = GitLabConfig(token="glpat-test456")
        result = create_gitlab_platform(config)

        assert result is not None
        assert result.base_url == "https://gitlab.com"


class TestGitLabPlatformInit:
    def test_init_stores_auth(self):
        auth = GitLabPatAuth(token="glpat-abc")
        platform = GitLabPlatform(auth=auth)

        assert platform.auth.get_api_token() == "glpat-abc"

    def test_init_stores_webhook_secret(self):
        auth = GitLabPatAuth(token="glpat-abc")
        platform = GitLabPlatform(auth=auth, webhook_secret="gl-secret")

        assert platform.webhook_secret == "gl-secret"

    def test_init_webhook_secret_defaults_to_none(self):
        auth = GitLabPatAuth(token="glpat-abc")
        platform = GitLabPlatform(auth=auth)

        assert platform.webhook_secret is None

    def test_init_stores_base_url_stripped_of_trailing_slash(self):
        auth = GitLabPatAuth(token="tok")
        platform = GitLabPlatform(auth=auth, base_url="https://gitlab.example.com/")

        assert platform.base_url == "https://gitlab.example.com"

    def test_init_base_url_defaults_to_gitlab_com(self):
        auth = GitLabPatAuth(token="tok")
        platform = GitLabPlatform(auth=auth)

        assert "gitlab.com" in platform.base_url


class TestGitLabHostProperty:
    def test_host_strips_https_scheme(self):
        auth = GitLabPatAuth(token="tok")
        platform = GitLabPlatform(auth=auth, base_url="https://gitlab.example.com")

        assert platform.host == "gitlab.example.com"

    def test_host_strips_http_scheme(self):
        auth = GitLabPatAuth(token="tok")
        platform = GitLabPlatform(auth=auth, base_url="http://self-hosted.example.com")

        assert platform.host == "self-hosted.example.com"

    def test_host_for_gitlab_com(self):
        auth = GitLabPatAuth(token="tok")
        platform = GitLabPlatform(auth=auth)

        assert platform.host == "gitlab.com"


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_authenticate_with_webhook_body(self, platform):
        body = b'{"object_kind": "note"}'

        await platform.authenticate(webhook_body=body)

        assert platform.auth.get_api_token() == "glpat-test456"

    @pytest.mark.asyncio
    async def test_authenticate_without_webhook_body(self, platform):
        await platform.authenticate()

        assert platform.auth.get_api_token() == "glpat-test456"
