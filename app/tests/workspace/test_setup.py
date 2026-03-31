# type: ignore
from unittest.mock import AsyncMock, MagicMock

import pytest

from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    Platform,
    PlatformName,
)
from nominal_code.workspace.git import GitWorkspace
from nominal_code.workspace.setup import (
    create_workspace,
    prepare_job_event,
    resolve_branch,
)


def _make_event(branch="feature"):
    return CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch=branch,
        clone_url="https://token@github.com/owner/repo.git",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=10,
        author_username="alice",
        body="fix this",
    )


def _make_platform(branch="feature"):
    platform = MagicMock()
    platform.fetch_pr_branch = AsyncMock(return_value=branch)
    platform.post_reply = AsyncMock()

    return platform


def _make_config(tmp_path):
    config = MagicMock()
    config.workspace = MagicMock()
    config.workspace.base_dir = tmp_path

    return config


class TestResolveBranch:
    @pytest.mark.asyncio
    async def test_resolve_branch_returns_event_when_branch_already_set(self):
        event = _make_event(branch="feature")
        platform = _make_platform()

        result = await resolve_branch(event, platform)

        assert result is event
        platform.fetch_pr_branch.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_branch_fetches_when_branch_empty(self):
        event = _make_event(branch="")
        platform = _make_platform(branch="fetched-branch")

        result = await resolve_branch(event, platform)

        assert result is not None
        assert result.pr_branch == "fetched-branch"
        platform.fetch_pr_branch.assert_awaited_once_with(
            repo_full_name="owner/repo",
            pr_number=42,
        )

    @pytest.mark.asyncio
    async def test_resolve_branch_returns_none_when_fetch_returns_empty(self):
        event = _make_event(branch="")
        platform = _make_platform(branch="")

        result = await resolve_branch(event, platform)

        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_branch_posts_reply_when_branch_unresolvable(self):
        event = _make_event(branch="")
        platform = _make_platform(branch="")

        await resolve_branch(event, platform)

        platform.post_reply.assert_awaited_once()
        call_kwargs = platform.post_reply.call_args.kwargs

        assert call_kwargs["event"] is event
        assert "branch" in call_kwargs["reply"].body.lower()

    @pytest.mark.asyncio
    async def test_resolve_branch_fetched_event_has_correct_pr_number(self):
        event = _make_event(branch="")
        platform = _make_platform(branch="resolved")

        result = await resolve_branch(event, platform)

        assert result is not None
        assert result.pr_number == 42
        assert result.repo_full_name == "owner/repo"

    @pytest.mark.asyncio
    async def test_resolve_branch_no_reply_posted_when_branch_resolved(self):
        event = _make_event(branch="")
        platform = _make_platform(branch="resolved")

        await resolve_branch(event, platform)

        platform.post_reply.assert_not_called()


class TestCreateWorkspace:
    def test_create_workspace_returns_git_workspace(self, tmp_path):
        event = _make_event(branch="feature")
        config = _make_config(tmp_path)

        workspace = create_workspace(event, config)

        assert isinstance(workspace, GitWorkspace)

    def test_create_workspace_uses_event_repo(self, tmp_path):
        event = _make_event(branch="feature")
        config = _make_config(tmp_path)

        workspace = create_workspace(event, config)

        assert "owner" in str(workspace.repo_path)
        assert "repo" in str(workspace.repo_path)

    def test_create_workspace_uses_pr_number(self, tmp_path):
        event = _make_event(branch="feature")
        config = _make_config(tmp_path)

        workspace = create_workspace(event, config)

        assert "pr-42" in str(workspace.repo_path)

    def test_create_workspace_uses_config_base_dir(self, tmp_path):
        event = _make_event(branch="feature")
        config = _make_config(tmp_path)

        workspace = create_workspace(event, config)

        assert str(workspace.repo_path).startswith(str(tmp_path))

    def test_create_workspace_writable_when_clone_url_set(self, tmp_path):
        event = _make_event(branch="feature")
        config = _make_config(tmp_path)

        workspace = create_workspace(event, config)

        assert workspace.read_only is False

    def test_create_workspace_read_only_when_clone_url_empty(self, tmp_path):
        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="feature",
            clone_url="",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=10,
            author_username="alice",
            body="fix this",
        )
        config = _make_config(tmp_path)

        workspace = create_workspace(event, config)

        assert workspace.read_only is True


def _make_job_platform():
    platform = MagicMock(spec=Platform)
    platform.fetch_pr_branch = AsyncMock(return_value="feature")
    platform.post_reply = AsyncMock()
    platform.build_clone_url = MagicMock(
        return_value="https://token@github.com/owner/repo.git",
    )

    return platform


class TestPrepareJobEvent:
    @pytest.mark.asyncio
    async def test_sets_clone_url(self):
        event = _make_event(branch="feature")
        platform = _make_job_platform()

        result = await prepare_job_event(
            event=event,
            platform=platform,
        )

        assert result.clone_url == "https://token@github.com/owner/repo.git"
        platform.build_clone_url.assert_called_once_with(
            repo_full_name="owner/repo",
        )

    @pytest.mark.asyncio
    async def test_pre_cloned_skips_clone_url_resolution(self):
        event = _make_event(branch="feature")
        platform = _make_job_platform()

        result = await prepare_job_event(
            event=event,
            platform=platform,
            pre_cloned=True,
        )

        assert result.clone_url == ""
        platform.build_clone_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_branch_unresolvable(self):
        event = _make_event(branch="")
        platform = _make_job_platform()
        platform.fetch_pr_branch = AsyncMock(return_value="")

        with pytest.raises(RuntimeError, match="Cannot resolve branch"):
            await prepare_job_event(
                event=event,
                platform=platform,
            )
