# type: ignore
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.models import EventType, FileStatus, ReviewFinding
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    LifecycleEvent,
    PlatformName,
)
from nominal_code.platforms.gitlab import (
    GitLabPlatform,
    _create_gitlab_platform,
)


@pytest.fixture
def platform():
    return GitLabPlatform(
        token="glpat-test456",
        webhook_secret="gl-secret",
        base_url="https://gitlab.com",
    )


@pytest.fixture
def platform_no_secret():
    return GitLabPlatform(token="glpat-test456")


@pytest.fixture
def platform_with_reviewer_token():
    return GitLabPlatform(
        token="glpat-test456",
        webhook_secret="gl-secret",
        base_url="https://gitlab.com",
        reviewer_token="glpat-readonly789",
    )


def _make_request(headers=None):
    request = MagicMock()
    request.headers = headers or {}

    return request


def _make_comment():
    return CommentEvent(
        platform=PlatformName.GITLAB,
        repo_full_name="group/repo",
        pr_number=10,
        pr_branch="feature",
        clone_url="",
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
        },
    }


class TestNameProperty:
    def test_name_returns_gitlab(self, platform):
        assert platform.name == "gitlab"


class TestVerifyWebhook:
    def test_verify_webhook_valid_token(self, platform):
        request = _make_request({"X-Gitlab-Token": "gl-secret"})

        assert platform.verify_webhook(request, b"") is True

    def test_verify_webhook_invalid_token(self, platform):
        request = _make_request({"X-Gitlab-Token": "wrong"})

        assert platform.verify_webhook(request, b"") is False

    def test_verify_webhook_missing_token(self, platform):
        request = _make_request({})

        assert platform.verify_webhook(request, b"") is False

    def test_verify_webhook_no_secret_configured(self, platform_no_secret):
        request = _make_request({})

        assert platform_no_secret.verify_webhook(request, b"") is True


class TestParseWebhook:
    def test_parse_mr_note(self, platform):
        payload = _note_payload()
        body = json.dumps(payload).encode()
        request = _make_request()
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.platform == "gitlab"
        assert result.repo_full_name == "group/repo"
        assert result.pr_number == 10
        assert result.pr_branch == "feature"
        assert result.comment_id == 500
        assert result.author_username == "alice"
        assert result.body == "@claude-bot fix this"
        assert result.event_type == EventType.NOTE
        assert result.discussion_id == "abc123def456"

    def test_parse_mr_note_without_discussion_id(self, platform):
        payload = _note_payload(discussion_id="")
        body = json.dumps(payload).encode()
        request = _make_request()
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.discussion_id == ""

    def test_parse_non_note_event(self, platform):
        payload = {"object_kind": "push"}
        body = json.dumps(payload).encode()
        request = _make_request()

        assert platform.parse_event(request, body) is None

    def test_parse_note_on_issue_not_mr(self, platform):
        payload = _note_payload(noteable_type="Issue")
        body = json.dumps(payload).encode()
        request = _make_request()

        assert platform.parse_event(request, body) is None

    def test_parse_note_with_position(self, platform):
        payload = _note_payload()
        payload["object_attributes"]["position"] = {
            "new_path": "src/main.py",
            "old_path": "src/old.py",
        }
        body = json.dumps(payload).encode()
        request = _make_request()
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.file_path == "src/main.py"

    def test_parse_clone_url(self, platform):
        payload = _note_payload()
        body = json.dumps(payload).encode()
        request = _make_request()
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.clone_url == (
            "https://oauth2:glpat-test456@gitlab.com/group/repo.git"
        )


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
                "title": "New feature",
                "work_in_progress": False,
            },
        }
        body = json.dumps(payload).encode()
        request = _make_request()
        result = platform.parse_event(request, body)

        assert result is not None
        assert result.event_type == EventType.PR_OPENED
        assert result.pr_number == 5
        assert result.pr_branch == "feature"
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
        request = _make_request()
        result = platform.parse_event(request, body)

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
        request = _make_request()
        result = platform.parse_event(request, body)

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
        request = _make_request()

        assert platform.parse_event(request, body) is None

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
        request = _make_request()

        assert platform.parse_event(request, body) is None

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
        request = _make_request()

        assert platform.parse_event(request, body) is None


class TestFetchPrBranch:
    @pytest.mark.asyncio
    async def test_fetch_pr_branch_returns_empty(self, platform):
        result = await platform.fetch_pr_branch("group/repo", 10)

        assert result == ""


class TestBuildReviewerCloneUrl:
    def test_build_reviewer_clone_url_with_reviewer_token(
        self,
        platform_with_reviewer_token,
    ):
        url = platform_with_reviewer_token.build_reviewer_clone_url("group/repo")

        assert url == ("https://oauth2:glpat-readonly789@gitlab.com/group/repo.git")

    def test_build_reviewer_clone_url_falls_back_to_main_token(self, platform):
        url = platform.build_reviewer_clone_url("group/repo")

        assert url == ("https://oauth2:glpat-test456@gitlab.com/group/repo.git")


class TestPostReply:
    @pytest.mark.asyncio
    async def test_post_reply_with_discussion_id_uses_threaded_endpoint(self, platform):
        comment = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=10,
            pr_branch="feature",
            clone_url="",
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
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_post.assert_called_once_with(
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
            clone_url="",
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
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reply(comment, reply)

            mock_post.assert_called_once_with(
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
            clone_url="",
            event_type=EventType.NOTE,
            comment_id=500,
            author_username="alice",
            body="test",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            platform._client,
            "post",
            new_callable=AsyncMock,
        ) as mock_post:
            mock_post.return_value = mock_response

            await platform.post_reaction(comment, "eyes")

            mock_post.assert_called_once()
            call_args = mock_post.call_args

            assert "award_emoji" in call_args[0][0]


class TestIsPrOpen:
    @pytest.mark.asyncio
    async def test_is_pr_open_returns_true_when_opened(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"state": "opened"}

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            result = await platform.is_pr_open("group/repo", 10)

        assert result is True
        mock_get.assert_called_once_with(
            "/projects/group%2Frepo/merge_requests/10",
        )

    @pytest.mark.asyncio
    async def test_is_pr_open_returns_false_when_merged(self, platform):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"state": "merged"}

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            result = await platform.is_pr_open("group/repo", 10)

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
            result = await platform.is_pr_open("group/repo", 10)

        assert result is True


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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            files = await platform.fetch_pr_diff("group/repo", 10)

        assert files[0].status == FileStatus.RENAMED
        assert files[0].file_path == "new_name.py"

    @pytest.mark.asyncio
    async def test_fetch_pr_diff_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = httpx.HTTPError("connection failed")
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

        with (
            patch.object(
                platform._client,
                "post",
                new_callable=AsyncMock,
            ) as mock_post,
            patch.object(
                platform._client,
                "get",
                new_callable=AsyncMock,
            ) as mock_get,
        ):
            mock_post.return_value = mock_post_response
            mock_get.return_value = mock_versions_response

            await platform.submit_review(
                "group/repo",
                10,
                findings,
                "Found issues",
                comment,
            )

            assert mock_post.call_count == 2
            assert mock_get.call_count == 1

            summary_call = mock_post.call_args_list[0]

            assert summary_call[1]["json"]["body"] == "Found issues"

            discussion_call = mock_post.call_args_list[1]
            position = discussion_call[1]["json"]["position"]

            assert position["base_sha"] == "base123"
            assert position["new_path"] == "src/main.py"
            assert position["new_line"] == 10

    @pytest.mark.asyncio
    async def test_submit_review_no_findings_skips_discussions(self, platform):
        comment = _make_comment()

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status = MagicMock()

        with (
            patch.object(
                platform._client,
                "post",
                new_callable=AsyncMock,
            ) as mock_post,
            patch.object(
                platform._client,
                "get",
                new_callable=AsyncMock,
            ) as mock_get,
        ):
            mock_post.return_value = mock_post_response

            await platform.submit_review(
                "group/repo",
                10,
                [],
                "No issues found",
                comment,
            )

            mock_post.assert_called_once()
            mock_get.assert_not_called()

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

        with (
            patch.object(
                platform._client,
                "post",
                new_callable=AsyncMock,
            ) as mock_post,
            patch.object(
                platform._client,
                "get",
                new_callable=AsyncMock,
            ) as mock_get,
        ):
            mock_post.return_value = mock_post_response
            mock_get.side_effect = httpx.HTTPError("version fetch failed")

            await platform.submit_review(
                "group/repo",
                10,
                findings,
                "Found issues",
                comment,
            )

            mock_post.assert_called_once()


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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert len(result) == 1
        assert result[0].body == "Real comment"

    @pytest.mark.asyncio
    async def test_fetch_pr_comments_http_error_returns_empty(self, platform):
        import httpx

        with patch.object(
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = httpx.HTTPError("connection failed")
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
            platform._client,
            "get",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = mock_response
            result = await platform.fetch_pr_comments("group/repo", 10)

        assert result[0].author == "alice"
        assert result[1].author == "bob"


class TestFactory:
    def test_factory_returns_platform_when_token_set(self):
        env = {
            "GITLAB_TOKEN": "glpat-test456",
            "GITLAB_WEBHOOK_SECRET": "secret",
            "GITLAB_BASE_URL": "https://git.example.com",
        }

        with patch.dict(os.environ, env, clear=True):
            result = _create_gitlab_platform()

        assert result is not None
        assert isinstance(result, GitLabPlatform)
        assert result.token == "glpat-test456"
        assert result.webhook_secret == "secret"
        assert result.base_url == "https://git.example.com"

    def test_factory_returns_none_when_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _create_gitlab_platform()

        assert result is None

    def test_factory_uses_default_base_url(self):
        env = {"GITLAB_TOKEN": "glpat-test456"}

        with patch.dict(os.environ, env, clear=True):
            result = _create_gitlab_platform()

        assert result is not None
        assert result.base_url == "https://gitlab.com"

    def test_factory_reads_reviewer_token(self):
        env = {
            "GITLAB_TOKEN": "glpat-test456",
            "GITLAB_REVIEWER_TOKEN": "glpat-readonly",
        }

        with patch.dict(os.environ, env, clear=True):
            result = _create_gitlab_platform()

        assert result is not None
        assert result.reviewer_token == "glpat-readonly"
