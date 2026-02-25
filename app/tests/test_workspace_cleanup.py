# type: ignore
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.workspace_cleanup import WorkspaceCleaner


@pytest.fixture
def base_dir(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture
def mock_platform():
    platform = MagicMock()
    platform.name = "github"
    platform.is_pr_open = AsyncMock(return_value=False)

    return platform


@pytest.fixture
def cleaner(base_dir, mock_platform):
    return WorkspaceCleaner(
        base_dir=str(base_dir),
        platforms={"github": mock_platform},
        interval_seconds=3600,
    )


def _create_pr_dir(base_dir, owner, repo, pr_number):
    pr_dir = base_dir / owner / repo / f"pr-{pr_number}"
    pr_dir.mkdir(parents=True)
    (pr_dir / "README.md").write_text("placeholder")

    return pr_dir


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_run_once_deletes_closed_pr(self, base_dir, cleaner, mock_platform):
        pr_dir = _create_pr_dir(base_dir, "owner", "repo", 42)
        mock_platform.is_pr_open.return_value = False

        await cleaner.run_once()

        assert not pr_dir.exists()
        mock_platform.is_pr_open.assert_called_once_with("owner/repo", 42)

    @pytest.mark.asyncio
    async def test_run_once_keeps_open_pr(self, base_dir, cleaner, mock_platform):
        pr_dir = _create_pr_dir(base_dir, "owner", "repo", 42)
        mock_platform.is_pr_open.return_value = True

        await cleaner.run_once()

        assert pr_dir.exists()

    @pytest.mark.asyncio
    async def test_run_once_keeps_on_api_error(self, base_dir, cleaner, mock_platform):
        pr_dir = _create_pr_dir(base_dir, "owner", "repo", 42)
        mock_platform.is_pr_open.side_effect = RuntimeError("API down")

        await cleaner.run_once()

        assert pr_dir.exists()

    @pytest.mark.asyncio
    async def test_run_once_cleans_empty_parent_dirs(
        self,
        base_dir,
        cleaner,
        mock_platform,
    ):
        _create_pr_dir(base_dir, "owner", "repo", 7)
        mock_platform.is_pr_open.return_value = False

        await cleaner.run_once()

        assert not (base_dir / "owner" / "repo").exists()
        assert not (base_dir / "owner").exists()

    @pytest.mark.asyncio
    async def test_run_once_preserves_sibling_dirs(
        self,
        base_dir,
        cleaner,
        mock_platform,
    ):
        _create_pr_dir(base_dir, "owner", "repo", 1)
        _create_pr_dir(base_dir, "owner", "repo", 2)
        mock_platform.is_pr_open.side_effect = [False, True]

        await cleaner.run_once()

        assert not (base_dir / "owner" / "repo" / "pr-1").exists()
        assert (base_dir / "owner" / "repo" / "pr-2").exists()
        assert (base_dir / "owner").exists()

    @pytest.mark.asyncio
    async def test_run_once_skips_non_pr_dirs(
        self,
        base_dir,
        cleaner,
        mock_platform,
    ):
        other_dir = base_dir / "owner" / "repo" / "not-a-pr"
        other_dir.mkdir(parents=True)

        await cleaner.run_once()

        assert other_dir.exists()
        mock_platform.is_pr_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_once_no_base_dir(self, cleaner, mock_platform):
        await cleaner.run_once()

        mock_platform.is_pr_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_once_multiple_platforms_any_open_keeps(
        self,
        base_dir,
        mock_platform,
    ):
        second_platform = MagicMock()
        second_platform.name = "gitlab"
        second_platform.is_pr_open = AsyncMock(return_value=True)
        mock_platform.is_pr_open.return_value = False

        cleaner = WorkspaceCleaner(
            base_dir=str(base_dir),
            platforms={"github": mock_platform, "gitlab": second_platform},
            interval_seconds=3600,
        )
        pr_dir = _create_pr_dir(base_dir, "owner", "repo", 5)

        await cleaner.run_once()

        assert pr_dir.exists()

    @pytest.mark.asyncio
    async def test_run_once_checks_multiple_prs_concurrently(
        self,
        base_dir,
        cleaner,
        mock_platform,
    ):
        _create_pr_dir(base_dir, "owner", "repo", 1)
        _create_pr_dir(base_dir, "owner", "repo", 2)
        _create_pr_dir(base_dir, "owner", "repo", 3)
        mock_platform.is_pr_open.return_value = False

        with patch(
            "nominal_code.workspace_cleanup.asyncio.gather",
            wraps=asyncio.gather,
        ) as mock_gather:
            await cleaner.run_once()

            mock_gather.assert_called_once()
            gather_args = mock_gather.call_args.args

            assert len(gather_args) == 3

        assert mock_platform.is_pr_open.call_count == 3


class TestOrphanedDepsCleanup:
    @pytest.mark.asyncio
    async def test_run_once_removes_orphaned_deps_when_no_prs_remain(
        self,
        base_dir,
        cleaner,
        mock_platform,
    ):
        pr_dir = _create_pr_dir(base_dir, "owner", "repo", 10)
        deps_dir = base_dir / "owner" / "repo" / ".deps" / "dep-owner" / "dep-repo"
        deps_dir.mkdir(parents=True)
        (deps_dir / "setup.py").write_text("placeholder")
        mock_platform.is_pr_open.return_value = False

        await cleaner.run_once()

        assert not pr_dir.exists()
        assert not (base_dir / "owner" / "repo" / ".deps").exists()

    @pytest.mark.asyncio
    async def test_run_once_keeps_deps_when_open_prs_exist(
        self,
        base_dir,
        cleaner,
        mock_platform,
    ):
        _create_pr_dir(base_dir, "owner", "repo", 10)
        deps_dir = base_dir / "owner" / "repo" / ".deps"
        deps_dir.mkdir(parents=True)
        mock_platform.is_pr_open.return_value = True

        await cleaner.run_once()

        assert deps_dir.exists()


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, cleaner):
        await cleaner.start()

        assert cleaner._task is not None
        assert not cleaner._task.done()

        await cleaner.stop()

        assert cleaner._task is None

    @pytest.mark.asyncio
    async def test_stop_without_start(self, cleaner):
        await cleaner.stop()

        assert cleaner._task is None
