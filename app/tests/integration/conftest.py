import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest

from nominal_code.agent.cli.session import SessionQueue
from tests.integration.github import api as github_api
from tests.integration.gitlab import api as gitlab_api

logger: logging.Logger = logging.getLogger(__name__)

BRANCH_POLL_INTERVAL = 1.0
BRANCH_POLL_TIMEOUT = 15.0


@dataclass(frozen=True)
class PrInfo:
    """
    Holds information about a test PR or MR.

    Attributes:
        repo (str): Repository full name or project path.
        number (int): PR number or MR IID.
        head_branch (str): The head/source branch name.
    """

    repo: str
    number: int
    head_branch: str


@dataclass(frozen=True)
class BranchInfo:
    """
    Holds information about a dynamically created test branch.

    Attributes:
        repo (str): Repository full name or project path.
        branch_name (str): The created branch name.
    """

    repo: str
    branch_name: str


def unique_branch_name(prefix: str) -> str:
    """
    Generate a unique branch name with a UUID suffix.

    Args:
        prefix (str): Branch name prefix (e.g. ``pipeline`` or ``webhook``).

    Returns:
        str: Branch name like ``test/pipeline-a1b2c3d4``.
    """

    short_id = uuid.uuid4().hex[:8]

    return f"test/{prefix}-{short_id}"


async def wait_for_queue_drain(
    session_queue: SessionQueue,
    timeout: float = 60.0,
) -> None:
    """
    Wait for all consumers in the session queue to finish.

    Returns immediately if no consumers are present.

    Args:
        session_queue (SessionQueue): The session queue to monitor.
        timeout (float): Maximum wait time in seconds.

    Raises:
        TimeoutError: If consumers do not finish within the timeout.
    """

    elapsed = 0.0
    interval = 0.5

    while elapsed < timeout:
        if not session_queue._consumers:
            return

        all_done = all(task.done() for task in session_queue._consumers.values())

        if all_done:
            return

        await asyncio.sleep(interval)
        elapsed += interval

    raise TimeoutError("Session queue did not drain within timeout")


async def wait_for_webhook_processing(
    session_queue: SessionQueue,
    timeout: float = 120.0,
    attempt_redelivery: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """
    Wait for at least one job to be enqueued and completed.

    First waits for a consumer to appear (webhook received), then waits
    for all consumers to finish. When ``attempt_redelivery`` is provided,
    the timeout is split into two equal phases: if no webhook arrives in
    phase 1, the callback is invoked to request redelivery before waiting
    again in phase 2.

    Args:
        session_queue (SessionQueue): The session queue to monitor.
        timeout (float): Maximum wait time in seconds.
        attempt_redelivery (Callable[[], Awaitable[None]] | None): Optional
            async callback that checks delivery status and requests
            redelivery from the platform.

    Raises:
        TimeoutError: If no job is enqueued or consumers do not finish.
    """

    phase_timeout = timeout / 2 if attempt_redelivery else timeout
    interval = 1.0
    elapsed = 0.0

    while elapsed < phase_timeout:
        if session_queue._consumers:
            break

        await asyncio.sleep(interval)
        elapsed += interval

    if not session_queue._consumers and attempt_redelivery:
        await attempt_redelivery()

        while elapsed < timeout:
            if session_queue._consumers:
                break

            await asyncio.sleep(interval)
            elapsed += interval

    if not session_queue._consumers:
        raise TimeoutError("No webhook job was enqueued within timeout")

    while elapsed < timeout:
        all_done = all(task.done() for task in session_queue._consumers.values())

        if all_done:
            return

        await asyncio.sleep(interval)
        elapsed += interval

    raise TimeoutError("Session queue did not drain within timeout")


async def create_github_branch_with_file(
    token: str,
    repo: str,
    branch_name: str,
    file_path: str,
    content_b64: str,
    commit_message: str,
) -> None:
    """
    Create a GitHub branch from main and push a file to it.

    Gets the HEAD SHA of ``main``, creates a new branch, waits for it
    to propagate, then pushes the file via the Contents API.

    Args:
        token (str): GitHub API token.
        repo (str): Repository full name.
        branch_name (str): New branch name.
        file_path (str): Path of the file to create.
        content_b64 (str): Base64-encoded file content.
        commit_message (str): Commit message for the file push.
    """

    sha = await github_api.get_branch_sha(token, repo, "main")

    await github_api.create_branch(token, repo, branch_name, sha)

    await _wait_for_github_branch(token, repo, branch_name)

    await github_api.create_or_update_file(
        token=token,
        repo=repo,
        path=file_path,
        content_b64=content_b64,
        message=commit_message,
        branch=branch_name,
    )


async def create_gitlab_branch_with_file(
    token: str,
    repo: str,
    branch_name: str,
    file_path: str,
    content: str,
    commit_message: str,
) -> None:
    """
    Create a GitLab branch from main and push a file to it.

    Creates a new branch from ``main``, then pushes the file via the
    Repository Files API.

    Args:
        token (str): GitLab API token.
        repo (str): Project path.
        branch_name (str): New branch name.
        file_path (str): Path of the file to create.
        content (str): Plain text file content.
        commit_message (str): Commit message for the file push.
    """

    await gitlab_api.create_branch(token, repo, branch_name, "main")

    await gitlab_api.create_or_update_file(
        token=token,
        repo=repo,
        path=file_path,
        content=content,
        message=commit_message,
        branch=branch_name,
    )


@pytest.fixture(scope="session")
def pipeline_id() -> str:
    """
    Return a short identifier for this pipeline run.

    Reads from ``TEST_PIPELINE_ID`` (set to ``${{ github.sha }}`` in CI),
    falling back to a random UUID for local runs. Truncated to 8 characters.

    Returns:
        str: An 8-character pipeline identifier.
    """

    raw = os.environ.get("TEST_PIPELINE_ID", "") or uuid.uuid4().hex

    return raw[:8]


async def _wait_for_github_branch(
    token: str,
    repo: str,
    branch: str,
) -> None:
    """
    Poll until a GitHub branch is visible via the API.

    GitHub ref creation can have a short propagation delay before the
    branch is usable in content operations.

    Args:
        token (str): GitHub API token.
        repo (str): Repository full name.
        branch (str): Branch name to wait for.

    Raises:
        TimeoutError: If the branch is not visible within the timeout.
    """

    elapsed = 0.0

    while elapsed < BRANCH_POLL_TIMEOUT:
        try:
            await github_api.get_branch_sha(token, repo, branch)

            return
        except Exception:
            await asyncio.sleep(BRANCH_POLL_INTERVAL)
            elapsed += BRANCH_POLL_INTERVAL

    raise TimeoutError(
        f"Branch {branch} not visible on {repo} after {BRANCH_POLL_TIMEOUT}s",
    )
