from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushResult:
    """
    Result of a git push operation.

    Attributes:
        success (bool): Whether the push succeeded.
        commit_sha (str): The short commit SHA, or empty on failure.
    """

    success: bool
    commit_sha: str = ""


class GitWorkspace:
    """
    Manages a persistent git workspace for a single PR/MR.

    Each PR gets its own directory under the base workspace path. On first
    use the repository is shallow-cloned and the PR branch checked out.
    On subsequent uses the workspace fetches and resets to the latest remote
    state. All git operations run as async subprocesses.

    Attributes:
        repo_path (str): Absolute path to the cloned repository.
    """

    def __init__(
        self,
        base_dir: str,
        repo_full_name: str,
        pr_number: int,
        clone_url: str,
        branch: str,
    ) -> None:
        """
        Initialize the workspace configuration.

        Args:
            base_dir (str): Base directory for all workspaces.
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull/merge request number.
            clone_url (str): Authenticated clone URL.
            branch (str): Branch to check out.
        """

        safe_name: str = repo_full_name.replace("/", os.sep)
        self._repo_dir: str = os.path.join(base_dir, safe_name)
        self.repo_path: str = os.path.join(
            self._repo_dir,
            f"pr-{pr_number}",
        )
        self._clone_url: str = clone_url
        self._branch: str = branch

    @property
    def deps_path(self) -> str:
        """
        Path to the shared dependencies directory for this repository.

        Returns:
            str: Absolute path to the ``.deps`` directory.
        """

        return os.path.join(self._repo_dir, ".deps")

    def ensure_deps_dir(self) -> None:
        """
        Create the shared dependencies directory if it does not exist.
        """

        os.makedirs(self.deps_path, exist_ok=True)

    async def ensure_ready(self) -> None:
        """
        Ensure the workspace is cloned and up to date.

        If the directory does not exist, performs a shallow clone and
        checks out the target branch. If it already exists, fetches
        from origin and resets to the latest remote state.

        Raises:
            RuntimeError: If a git operation fails.
        """

        if os.path.isdir(os.path.join(self.repo_path, ".git")):
            await self._update()
        else:
            await self._clone()

    async def push_changes(self, commit_message: str) -> PushResult:
        """
        Stage all changes, commit, and push to the remote branch.

        Does nothing if there are no changes to commit.

        Args:
            commit_message (str): The commit message.

        Returns:
            PushResult: The result of the push operation.
        """

        status_output: str = await self._run_git("status", "--porcelain")

        if not status_output.strip():
            logger.info("No changes to commit in %s", self.repo_path)

            return PushResult(success=True)

        await self._run_git("add", "-A")
        await self._run_git("commit", "-m", commit_message)

        commit_sha: str = await self._run_git("rev-parse", "--short", "HEAD")
        commit_sha = commit_sha.strip()

        await self._run_git("push", "origin", self._branch)

        logger.info("Pushed commit %s to %s", commit_sha, self._branch)

        return PushResult(success=True, commit_sha=commit_sha)

    async def _clone(self) -> None:
        """
        Shallow clone the repository and check out the target branch.

        Raises:
            RuntimeError: If the clone or checkout fails.
        """

        os.makedirs(self.repo_path, exist_ok=True)

        logger.info("Cloning %s into %s", self._clone_url, self.repo_path)

        await self._run_command(
            "git",
            "clone",
            "--depth=1",
            f"--branch={self._branch}",
            "--single-branch",
            self._clone_url,
            self.repo_path,
        )

    async def _update(self) -> None:
        """
        Fetch the latest changes and reset to the remote branch.

        Raises:
            RuntimeError: If the fetch or reset fails.
        """

        logger.info("Updating workspace %s", self.repo_path)

        await self._run_git("fetch", "origin", self._branch)
        await self._run_git("reset", "--hard", f"origin/{self._branch}")
        await self._run_git("clean", "-fdx")

    async def _run_git(self, *args: str) -> str:
        """
        Run a git command in the workspace directory.

        Args:
            *args (str): Git subcommand and arguments.

        Returns:
            str: The command's stdout output.

        Raises:
            RuntimeError: If the command exits with a non-zero status.
        """

        return await self._run_command("git", *args, cwd=self.repo_path)

    async def _run_command(
        self,
        *args: str,
        cwd: str | None = None,
    ) -> str:
        """
        Run an external command as an async subprocess.

        Args:
            *args (str): The command and its arguments.
            cwd (str | None): Working directory, or None for the default.

        Returns:
            str: The command's stdout output.

        Raises:
            RuntimeError: If the command exits with a non-zero status.
        """

        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout_bytes, stderr_bytes = await process.communicate()
        stdout_text: str = stdout_bytes.decode().strip()
        stderr_text: str = stderr_bytes.decode().strip()

        if process.returncode != 0:
            command_str: str = " ".join(args)

            raise RuntimeError(
                f"Command '{command_str}' failed (exit {process.returncode}): "
                f"{stderr_text}"
            )

        if stderr_text:
            logger.debug("git stderr: %s", stderr_text)

        return stdout_text
