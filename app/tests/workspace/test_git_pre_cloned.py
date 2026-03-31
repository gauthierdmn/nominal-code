# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.workspace.git import GitWorkspace


@pytest.fixture
def read_only_workspace(tmp_path):
    return GitWorkspace(
        base_dir=tmp_path,
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="",
        branch="feature-branch",
        read_only=True,
    )


@pytest.fixture
def writable_workspace(tmp_path):
    return GitWorkspace(
        base_dir=tmp_path,
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="https://token@github.com/owner/repo.git",
        branch="feature-branch",
    )


class TestReadOnlyWorkspace:
    @pytest.mark.asyncio
    async def test_ensure_ready_skips_when_git_exists(
        self,
        read_only_workspace,
    ):
        git_dir = Path(read_only_workspace.repo_path) / ".git"
        git_dir.mkdir(parents=True)

        with (
            patch.object(
                read_only_workspace,
                "_clone",
                new_callable=AsyncMock,
            ) as mock_clone,
            patch.object(
                read_only_workspace,
                "_update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await read_only_workspace.ensure_ready()

            mock_clone.assert_not_called()
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_ready_raises_when_no_git_dir(
        self,
        read_only_workspace,
    ):
        with pytest.raises(RuntimeError, match="Read-only workspace"):
            await read_only_workspace.ensure_ready()

    def test_maybe_create_deps_dir_skips(self, read_only_workspace):
        read_only_workspace.maybe_create_deps_dir()

        assert not read_only_workspace.deps_path.exists()

    def test_read_only_flag_is_set(self, read_only_workspace):
        assert read_only_workspace.read_only is True


class TestWritableWorkspace:
    @pytest.mark.asyncio
    async def test_ensure_ready_clones_when_no_git(
        self,
        writable_workspace,
    ):
        with patch.object(
            writable_workspace,
            "_clone",
            new_callable=AsyncMock,
        ) as mock_clone:
            await writable_workspace.ensure_ready()

            mock_clone.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_ready_updates_when_git_exists(
        self,
        writable_workspace,
    ):
        git_dir = Path(writable_workspace.repo_path) / ".git"
        git_dir.mkdir(parents=True)

        with patch.object(
            writable_workspace,
            "_update",
            new_callable=AsyncMock,
        ) as mock_update:
            await writable_workspace.ensure_ready()

            mock_update.assert_called_once()

    def test_maybe_create_deps_dir_creates(self, writable_workspace):
        writable_workspace.maybe_create_deps_dir()

        assert writable_workspace.deps_path.exists()

    def test_read_only_flag_is_false(self, writable_workspace):
        assert writable_workspace.read_only is False
