# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.workspace.git import GitWorkspace


@pytest.fixture
def workspace_no_clone_url(tmp_path):
    return GitWorkspace(
        base_dir=tmp_path,
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="",
        branch="feature-branch",
    )


@pytest.fixture
def workspace_with_clone_url(tmp_path):
    return GitWorkspace(
        base_dir=tmp_path,
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="https://token@github.com/owner/repo.git",
        branch="feature-branch",
    )


class TestExternallyManagedWorkspace:
    @pytest.mark.asyncio
    async def test_ensure_ready_skips_when_no_clone_url_and_git_exists(
        self,
        workspace_no_clone_url,
    ):
        git_dir = Path(workspace_no_clone_url.repo_path) / ".git"
        git_dir.mkdir(parents=True)

        with (
            patch.object(
                workspace_no_clone_url,
                "_clone",
                new_callable=AsyncMock,
            ) as mock_clone,
            patch.object(
                workspace_no_clone_url,
                "_update",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await workspace_no_clone_url.ensure_ready()

            mock_clone.assert_not_called()
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_ready_raises_when_no_clone_url_and_no_git(
        self,
        workspace_no_clone_url,
    ):
        with pytest.raises(RuntimeError, match="No clone URL provided"):
            await workspace_no_clone_url.ensure_ready()


class TestManagedWorkspace:
    @pytest.mark.asyncio
    async def test_ensure_ready_clones_when_clone_url_set_and_no_git(
        self,
        workspace_with_clone_url,
    ):
        with patch.object(
            workspace_with_clone_url,
            "_clone",
            new_callable=AsyncMock,
        ) as mock_clone:
            await workspace_with_clone_url.ensure_ready()

            mock_clone.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_ready_updates_when_clone_url_set_and_git_exists(
        self,
        workspace_with_clone_url,
    ):
        git_dir = Path(workspace_with_clone_url.repo_path) / ".git"
        git_dir.mkdir(parents=True)

        with patch.object(
            workspace_with_clone_url,
            "_update",
            new_callable=AsyncMock,
        ) as mock_update:
            await workspace_with_clone_url.ensure_ready()

            mock_update.assert_called_once()
