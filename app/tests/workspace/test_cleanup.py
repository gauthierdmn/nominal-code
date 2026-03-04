# type: ignore
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.workspace.cleanup import WorkspaceCleaner


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
        base_dir=base_dir,
        platforms={"github": mock_platform},
        cleanup_wait=timedelta(hours=1),
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
            base_dir=base_dir,
            platforms={"github": mock_platform, "gitlab": second_platform},
            cleanup_wait=timedelta(hours=1),
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
            "nominal_code.workspace.cleanup.asyncio.gather",
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


class TestPurge:
    def test_purge_deletes_all_directories(self, base_dir, cleaner):
        _create_pr_dir(base_dir, "owner", "repo-a", 1)
        _create_pr_dir(base_dir, "owner", "repo-b", 2)

        cleaner.purge()

        assert base_dir.exists()
        assert not any(base_dir.iterdir())

    def test_purge_noop_when_base_dir_missing(self, cleaner):
        cleaner.purge()

    def test_purge_skips_files(self, base_dir, cleaner):
        base_dir.mkdir(parents=True, exist_ok=True)
        stray_file = base_dir / "stray.txt"
        stray_file.write_text("placeholder")

        cleaner.purge()

        assert stray_file.exists()


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


class TestWorkspaceCleanerInit:
    def test_init_stores_base_dir(self, tmp_path):
        cleaner = WorkspaceCleaner(
            base_dir=tmp_path,
            platforms={},
            cleanup_wait=timedelta(hours=2),
        )

        assert cleaner.base_dir == tmp_path

    def test_init_stores_platforms(self, tmp_path, mock_platform):
        cleaner = WorkspaceCleaner(
            base_dir=tmp_path,
            platforms={"github": mock_platform},
            cleanup_wait=timedelta(hours=1),
        )

        assert "github" in cleaner.platforms

    def test_init_stores_cleanup_wait(self, tmp_path):
        wait = timedelta(hours=3)
        cleaner = WorkspaceCleaner(
            base_dir=tmp_path,
            platforms={},
            cleanup_wait=wait,
        )

        assert cleaner.cleanup_wait == wait

    def test_init_task_is_none(self, tmp_path):
        cleaner = WorkspaceCleaner(
            base_dir=tmp_path,
            platforms={},
            cleanup_wait=timedelta(hours=1),
        )

        assert cleaner._task is None


class TestCleanupOrphanedDeps:
    def test_cleanup_orphaned_deps_removes_deps_when_no_prs(self, base_dir):
        from nominal_code.workspace.git import DEPS_FOLDER_NAME

        repo_dir = base_dir / "owner" / "repo"
        repo_dir.mkdir(parents=True)
        deps_dir = repo_dir / DEPS_FOLDER_NAME
        deps_dir.mkdir()
        (deps_dir / "dep.txt").write_text("placeholder")
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={},
            cleanup_wait=timedelta(hours=1),
        )

        cleaner._cleanup_orphaned_deps(repo_dir)

        assert not deps_dir.exists()

    def test_cleanup_orphaned_deps_keeps_deps_when_pr_dirs_exist(self, base_dir):
        from nominal_code.workspace.git import DEPS_FOLDER_NAME

        repo_dir = base_dir / "owner" / "repo"
        repo_dir.mkdir(parents=True)
        pr_dir = repo_dir / "pr-1"
        pr_dir.mkdir()
        deps_dir = repo_dir / DEPS_FOLDER_NAME
        deps_dir.mkdir()
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={},
            cleanup_wait=timedelta(hours=1),
        )

        cleaner._cleanup_orphaned_deps(repo_dir)

        assert deps_dir.exists()

    def test_cleanup_orphaned_deps_noop_when_deps_missing(self, base_dir):
        repo_dir = base_dir / "owner" / "repo"
        repo_dir.mkdir(parents=True)
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={},
            cleanup_wait=timedelta(hours=1),
        )

        cleaner._cleanup_orphaned_deps(repo_dir)


class TestMaybeDelete:
    @pytest.mark.asyncio
    async def test_maybe_delete_removes_when_all_platforms_say_closed(
        self, base_dir, mock_platform
    ):
        pr_dir = base_dir / "owner" / "repo" / "pr-5"
        pr_dir.mkdir(parents=True)
        mock_platform.is_pr_open.return_value = False
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={"github": mock_platform},
            cleanup_wait=timedelta(hours=1),
        )

        await cleaner._maybe_delete(pr_dir, "owner/repo", 5)

        assert not pr_dir.exists()

    @pytest.mark.asyncio
    async def test_maybe_delete_keeps_when_platform_says_open(
        self, base_dir, mock_platform
    ):
        pr_dir = base_dir / "owner" / "repo" / "pr-5"
        pr_dir.mkdir(parents=True)
        mock_platform.is_pr_open.return_value = True
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={"github": mock_platform},
            cleanup_wait=timedelta(hours=1),
        )

        await cleaner._maybe_delete(pr_dir, "owner/repo", 5)

        assert pr_dir.exists()

    @pytest.mark.asyncio
    async def test_maybe_delete_keeps_on_platform_error(self, base_dir, mock_platform):
        pr_dir = base_dir / "owner" / "repo" / "pr-5"
        pr_dir.mkdir(parents=True)
        mock_platform.is_pr_open.side_effect = RuntimeError("API down")
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={"github": mock_platform},
            cleanup_wait=timedelta(hours=1),
        )

        await cleaner._maybe_delete(pr_dir, "owner/repo", 5)

        assert pr_dir.exists()

    @pytest.mark.asyncio
    async def test_maybe_delete_no_platforms_deletes_dir(self, base_dir):
        pr_dir = base_dir / "owner" / "repo" / "pr-5"
        pr_dir.mkdir(parents=True)
        cleaner = WorkspaceCleaner(
            base_dir=base_dir,
            platforms={},
            cleanup_wait=timedelta(hours=1),
        )

        await cleaner._maybe_delete(pr_dir, "owner/repo", 5)

        assert not pr_dir.exists()


class TestCleanupLoop:
    @pytest.mark.asyncio
    async def test_loop_runs_cleanup_after_wait(self, cleaner):
        call_count = []

        async def fake_run_once():
            call_count.append(1)

        with patch.object(cleaner, "run_once", side_effect=fake_run_once):
            with patch(
                "nominal_code.workspace.cleanup.asyncio.sleep",
                new=AsyncMock(side_effect=[None, asyncio.CancelledError()]),
            ):
                try:
                    await cleaner._loop()
                except asyncio.CancelledError:
                    pass

        assert len(call_count) >= 1

    @pytest.mark.asyncio
    async def test_loop_continues_after_run_once_exception(self, cleaner):
        call_count = []

        async def fake_run_once():
            call_count.append(1)

            raise RuntimeError("cleanup failed")

        with patch.object(cleaner, "run_once", side_effect=fake_run_once):
            with patch(
                "nominal_code.workspace.cleanup.asyncio.sleep",
                new=AsyncMock(side_effect=[None, asyncio.CancelledError()]),
            ):
                try:
                    await cleaner._loop()
                except asyncio.CancelledError:
                    pass

        assert len(call_count) >= 1
