from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)

PR_DIR_PATTERN: re.Pattern[str] = re.compile(r"^pr-(\d+)$")


class WorkspaceCleaner:
    """
    Periodically removes workspace directories for closed or merged PRs.

    Scans ``base_dir`` for directories matching the pattern
    ``{owner_or_group}/{repo}/pr-{N}`` and queries all configured platforms
    to determine whether the PR is still open. If no platform reports the
    PR as open, the directory is deleted.

    Attributes:
        base_dir (Path): Root directory containing workspace directories.
        platforms (dict[str, Platform]): Configured platform clients keyed by name.
        interval_seconds (int): Seconds between cleanup runs.
    """

    def __init__(
        self,
        base_dir: str,
        platforms: dict[str, Platform],
        interval_seconds: int,
    ) -> None:
        """
        Initialize the workspace cleaner.

        Args:
            base_dir (str): Root directory containing workspace directories.
            platforms (dict[str, Platform]): Configured platform clients.
            interval_seconds (int): Seconds between cleanup runs.
        """

        self.base_dir: Path = Path(base_dir)
        self.platforms: dict[str, Platform] = platforms
        self.interval_seconds: int = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """
        Spawn the background cleanup loop.
        """

        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Workspace cleaner started (interval=%ds)",
            self.interval_seconds,
        )

    async def stop(self) -> None:
        """
        Cancel the background cleanup loop and wait for it to finish.
        """

        if self._task is not None:
            self._task.cancel()

            try:
                await self._task
            except asyncio.CancelledError:
                pass

            self._task = None
            logger.info("Workspace cleaner stopped")

    async def run_once(self) -> None:
        """
        Perform a single cleanup scan.

        Walks ``base_dir`` two levels deep looking for ``pr-{N}`` directories,
        checks each against all configured platforms, and deletes directories
        where no platform reports the PR as open.
        """

        if not self.base_dir.is_dir():
            return

        for owner_dir in self.base_dir.iterdir():
            if not owner_dir.is_dir():
                continue

            for repo_dir in owner_dir.iterdir():
                if not repo_dir.is_dir():
                    continue

                delete_tasks: list[asyncio.Task[None]] = []

                for pr_dir in repo_dir.iterdir():
                    if not pr_dir.is_dir():
                        continue

                    match: re.Match[str] | None = PR_DIR_PATTERN.match(
                        pr_dir.name,
                    )

                    if not match:
                        continue

                    pr_number: int = int(match.group(1))
                    repo_full_name: str = f"{owner_dir.name}/{repo_dir.name}"

                    delete_tasks.append(
                        asyncio.create_task(
                            self._maybe_delete(
                                pr_dir,
                                repo_full_name,
                                pr_number,
                            ),
                        ),
                    )

                if delete_tasks:
                    await asyncio.gather(*delete_tasks)

                self._cleanup_orphaned_deps(repo_dir)

                if repo_dir.exists() and not any(repo_dir.iterdir()):
                    repo_dir.rmdir()
                    logger.info("Removed empty repo directory: %s", repo_dir)

            if owner_dir.exists() and not any(owner_dir.iterdir()):
                owner_dir.rmdir()
                logger.info("Removed empty owner directory: %s", owner_dir)

    def _cleanup_orphaned_deps(self, repo_dir: Path) -> None:
        """
        Remove the ``.deps`` directory if no PR workspaces remain.

        Args:
            repo_dir (Path): Path to the ``{owner}/{repo}`` directory.
        """

        deps_dir: Path = repo_dir / ".deps"

        if not deps_dir.is_dir():
            return

        has_pr_dirs: bool = any(
            PR_DIR_PATTERN.match(child.name)
            for child in repo_dir.iterdir()
            if child.is_dir()
        )

        if not has_pr_dirs:
            shutil.rmtree(deps_dir)
            logger.info("Removed orphaned deps directory: %s", deps_dir)

    async def _maybe_delete(
        self,
        pr_dir: Path,
        repo_full_name: str,
        pr_number: int,
    ) -> None:
        """
        Delete a PR workspace directory if no platform reports it as open.

        Args:
            pr_dir (Path): Path to the ``pr-{N}`` directory.
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull request or merge request number.
        """

        for platform in self.platforms.values():
            try:
                is_open: bool = await platform.is_pr_open(
                    repo_full_name,
                    pr_number,
                )

                if is_open:
                    return

            except Exception:
                logger.warning(
                    "Error checking %s#%d on %s, assuming open",
                    repo_full_name,
                    pr_number,
                    platform.name,
                )

                return

        shutil.rmtree(pr_dir)
        logger.info(
            "Deleted workspace for closed PR: %s#%d (%s)",
            repo_full_name,
            pr_number,
            pr_dir,
        )

    async def _loop(self) -> None:
        """
        Background loop that runs cleanup at the configured interval.

        Sleeps first to give the server time to settle on startup.
        """

        while True:
            await asyncio.sleep(self.interval_seconds)

            try:
                await self.run_once()
            except Exception:
                logger.exception("Workspace cleanup scan failed")
