from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nominal_code.agent.sandbox import redact_url

logger: logging.Logger = logging.getLogger(__name__)
DEPS_FOLDER_NAME: str = ".deps"
GIT_FOLDER_NAME: str = ".git"
DEFAULT_BASE_DIR: Path = Path(tempfile.gettempdir()) / "nominal-code"


def build_repo_path(base_dir: Path, repo_full_name: str, pr_number: int) -> Path:
    """
    Build the workspace path for a specific PR/MR.

    Args:
        base_dir (Path): Base directory for all workspaces.
        repo_full_name (str): Full repository name (e.g. ``owner/repo``).
        pr_number (int): Pull/merge request number.

    Returns:
        Path: Absolute path to the PR workspace directory.
    """

    return base_dir / repo_full_name / f"pr-{pr_number}"


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
        repo_path (Path): Absolute path to the cloned repository.
        read_only (bool): Whether the workspace is read-only.
    """

    def __init__(
        self,
        base_dir: Path,
        repo_full_name: str,
        pr_number: int,
        clone_url: str,
        branch: str,
        read_only: bool = False,
    ) -> None:
        """
        Initialize the workspace configuration.

        Args:
            base_dir (Path): Base directory for all workspaces.
            repo_full_name (str): Full repository name (e.g. ``owner/repo``).
            pr_number (int): Pull/merge request number.
            clone_url (str): Authenticated clone URL. Empty when
                ``read_only`` is True.
            branch (str): Branch to check out.
            read_only (bool): When True the workspace was pre-cloned.
        """

        self._repo_dir: Path = base_dir / repo_full_name
        self._clone_url: str = clone_url
        self._branch: str = branch
        self.read_only: bool = read_only

        self.repo_path: Path = build_repo_path(base_dir, repo_full_name, pr_number)

    @property
    def deps_path(self) -> Path:
        """
        Path to the shared dependencies directory for this repository.

        Returns:
            Path: Absolute path to the ``.deps`` directory.
        """

        return self._repo_dir / DEPS_FOLDER_NAME

    def maybe_create_deps_dir(self) -> None:
        """
        Create the shared dependencies directory if it does not exist.

        Skipped for read-only workspaces where the filesystem may not
        be writable.
        """

        if self.read_only:
            return

        self.deps_path.mkdir(parents=True, exist_ok=True)

    async def ensure_ready(self) -> None:
        """
        Ensure the workspace is cloned and up to date.

        For read-only workspaces, validates that ``.git`` exists and
        returns without performing any git operations.

        For writable workspaces, clones or fetches as needed.

        Raises:
            RuntimeError: If a git operation fails, or if the workspace
                is read-only and ``.git`` does not exist.
        """

        git_dir_exists: bool = (self.repo_path / GIT_FOLDER_NAME).is_dir()

        if self.read_only:
            if not git_dir_exists:
                raise RuntimeError(
                    f"Read-only workspace at {self.repo_path} has no "
                    f"{GIT_FOLDER_NAME} directory — was the repository cloned?",
                )

            logger.info(
                "Workspace is read-only, skipping clone/fetch for %s",
                self.repo_path,
            )

            return

        if git_dir_exists:
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

        Disables git hooks, symlinks, and the ``file://`` protocol to
        prevent malicious repositories from executing code during clone
        via ``.gitmodules``, ``post-checkout`` hooks, or symlink escapes.

        Raises:
            RuntimeError: If the clone or checkout fails.
        """

        self.repo_path.mkdir(parents=True, exist_ok=True)

        logger.info("Cloning %s into %s", redact_url(self._clone_url), self.repo_path)

        await self._run_command(
            "git",
            "clone",
            "--depth=1",
            f"--branch={self._branch}",
            "--single-branch",
            "--config",
            "core.hooksPath=/dev/null",
            "--config",
            "core.symlinks=false",
            "--config",
            "protocol.file.allow=never",
            self._clone_url,
            self.repo_path,
        )

    async def _update(self) -> None:
        """
        Fetch the latest changes and reset to the remote branch.

        Sets ``core.hooksPath=/dev/null`` before fetching to prevent
        hook execution during fetch/reset on an existing workspace.

        Raises:
            RuntimeError: If the fetch or reset fails.
        """

        logger.info("Updating workspace %s", self.repo_path)

        await self._run_git("config", "core.hooksPath", "/dev/null")
        await self._run_git("config", "core.symlinks", "false")
        await self._run_git("config", "protocol.file.allow", "never")
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
        *args: str | Path,
        cwd: Path | None = None,
    ) -> str:
        """
        Run an external command as an async subprocess.

        Args:
            *args (str | Path): The command and its arguments.
            cwd (Path | None): Working directory, or None for the default.

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
            command_str: str = redact_url(" ".join(str(arg) for arg in args))

            raise RuntimeError(
                f"Command '{command_str}' failed (exit {process.returncode}): "
                f"{redact_url(stderr_text)}"
            )

        if stderr_text:
            logger.debug("git stderr: %s", stderr_text)

        return stdout_text
