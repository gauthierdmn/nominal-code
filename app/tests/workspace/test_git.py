# type: ignore
import os
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.workspace.git import GitWorkspace, PushResult


@pytest.fixture
def workspace(tmp_path):
    return GitWorkspace(
        base_dir=str(tmp_path),
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="https://token@github.com/owner/repo.git",
        branch="feature-branch",
    )


class TestInit:
    def test_repo_path_constructed_correctly(self, workspace, tmp_path):
        expected = os.path.join(str(tmp_path), "owner", "repo", "pr-42")

        assert workspace.repo_path == expected


class TestEnsureReady:
    @pytest.mark.asyncio
    async def test_ensure_ready_clones_when_no_git_dir(self, workspace):
        with patch.object(workspace, "_clone", new_callable=AsyncMock) as mock_clone:
            await workspace.ensure_ready()

            mock_clone.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_ready_updates_when_git_dir_exists(self, workspace):
        git_dir = os.path.join(workspace.repo_path, ".git")
        os.makedirs(git_dir)

        with patch.object(workspace, "_update", new_callable=AsyncMock) as mock_update:
            await workspace.ensure_ready()

            mock_update.assert_called_once()


class TestPushChanges:
    @pytest.mark.asyncio
    async def test_push_changes_no_changes(self, workspace):
        with patch.object(
            workspace,
            "_run_git",
            new_callable=AsyncMock,
            return_value="",
        ):
            result = await workspace.push_changes("test commit")

        assert result.success is True
        assert result.commit_sha == ""

    @pytest.mark.asyncio
    async def test_push_changes_with_changes(self, workspace):
        call_count = 0
        call_returns = [
            " M file.py",
            "",
            "",
            "abc1234",
            "",
        ]

        async def mock_run_git(*args):
            nonlocal call_count
            result = call_returns[call_count]
            call_count += 1

            return result

        with patch.object(workspace, "_run_git", side_effect=mock_run_git):
            result = await workspace.push_changes("fix: update code")

        assert result.success is True
        assert result.commit_sha == "abc1234"


class TestDepsPath:
    def test_deps_path_is_sibling_of_repo_path(self, workspace, tmp_path):
        expected = os.path.join(str(tmp_path), "owner", "repo", ".deps")

        assert workspace.deps_path == expected

    def test_ensure_deps_dir_creates_directory(self, workspace):
        workspace.ensure_deps_dir()

        assert os.path.isdir(workspace.deps_path)

    def test_ensure_deps_dir_idempotent(self, workspace):
        workspace.ensure_deps_dir()
        workspace.ensure_deps_dir()

        assert os.path.isdir(workspace.deps_path)


class TestPushResult:
    def test_push_result_defaults(self):
        result = PushResult(success=True)

        assert result.commit_sha == ""

    def test_push_result_with_sha(self):
        result = PushResult(success=True, commit_sha="abc123")

        assert result.commit_sha == "abc123"
