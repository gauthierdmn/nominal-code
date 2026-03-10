import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx

from nominal_code.platforms.http import request_with_retry

GITLAB_API_BASE = "https://gitlab.com/api/v4"
TIMEOUT = 30.0
DIFF_POLL_INTERVAL = 2.0
DIFF_POLL_TIMEOUT = 60.0
CREATE_MR_RETRY_DELAY = 2.0
CREATE_MR_MAX_RETRIES = 3
PIPELINE_POLL_INTERVAL = 10.0

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _gitlab_client(token: str) -> AsyncIterator[httpx.AsyncClient]:
    """
    Yield an authenticated GitLab API client.

    Args:
        token (str): GitLab API token.

    Yields:
        httpx.AsyncClient: Configured HTTP client.
    """

    async with httpx.AsyncClient(
        base_url=GITLAB_API_BASE,
        timeout=TIMEOUT,
        headers=_headers(token),
    ) as client:
        yield client


def _headers(token: str) -> dict[str, str]:
    return {"PRIVATE-TOKEN": token}


def _encode_project(repo: str) -> str:
    """
    URL-encode a project path for use in GitLab API URLs.

    Args:
        repo (str): Project path like ``owner/repo``.

    Returns:
        str: URL-encoded project path.
    """

    return quote(repo, safe="")


async def create_mr(
    token: str,
    repo: str,
    head: str,
    base: str = "main",
    title: str = "test: MR",
) -> int:
    """
    Create a merge request on a unique branch.

    Retries on transient 409 errors with a short delay.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path (e.g. ``owner/repo``).
        head (str): Source branch name.
        base (str): Target branch name.
        title (str): Merge request title.

    Returns:
        int: The merge request IID.
    """

    project = _encode_project(repo)

    async with _gitlab_client(token) as client:
        for attempt in range(1, CREATE_MR_MAX_RETRIES + 1):
            response = await request_with_retry(
                client,
                "POST",
                f"/projects/{project}/merge_requests",
                json={
                    "title": title,
                    "source_branch": head,
                    "target_branch": base,
                },
            )

            if response.status_code != 409:
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                mr_iid: int = data["iid"]

                return mr_iid

            logger.warning(
                "MR creation returned 409 for %s (attempt %d/%d): %s",
                head,
                attempt,
                CREATE_MR_MAX_RETRIES,
                response.text,
            )

            if attempt < CREATE_MR_MAX_RETRIES:
                await asyncio.sleep(CREATE_MR_RETRY_DELAY)

        response.raise_for_status()
        fallback: dict[str, Any] = response.json()
        fallback_iid: int = fallback["iid"]

        return fallback_iid


async def wait_for_mr_diff(token: str, repo: str, mr_iid: int) -> None:
    """
    Poll until GitLab has computed the MR diff.

    GitLab computes diffs asynchronously after MR creation. This function
    polls the diffs endpoint until at least one file appears.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        mr_iid (int): Merge request IID.

    Raises:
        TimeoutError: If the diff is not ready within the timeout.
    """

    elapsed = 0.0

    async with _gitlab_client(token) as client:
        while elapsed < DIFF_POLL_TIMEOUT:
            response = await request_with_retry(
                client,
                "GET",
                f"/projects/{_encode_project(repo)}/merge_requests/{mr_iid}/diffs",
            )
            response.raise_for_status()
            data: list[dict[str, Any]] = response.json()

            if data:
                return

            await asyncio.sleep(DIFF_POLL_INTERVAL)
            elapsed += DIFF_POLL_INTERVAL

    raise TimeoutError(
        f"MR diff not ready after {DIFF_POLL_TIMEOUT}s for {repo}!{mr_iid}"
    )


async def close_mr(token: str, repo: str, mr_iid: int) -> None:
    """
    Close a merge request. Silently succeeds if already closed.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        mr_iid (int): Merge request IID.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "PUT",
            f"/projects/{_encode_project(repo)}/merge_requests/{mr_iid}",
            json={"state_event": "close"},
        )

        if response.status_code == 422:
            return

        response.raise_for_status()


async def fetch_mr_notes(
    token: str,
    repo: str,
    mr_iid: int,
) -> list[dict[str, Any]]:
    """
    Fetch all notes (comments) on a merge request.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        mr_iid (int): Merge request IID.

    Returns:
        list[dict[str, Any]]: List of note objects.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/projects/{_encode_project(repo)}/merge_requests/{mr_iid}/notes",
        )
        response.raise_for_status()

    notes: list[dict[str, Any]] = response.json()

    return notes


async def fetch_mr_discussions(
    token: str,
    repo: str,
    mr_iid: int,
) -> list[dict[str, Any]]:
    """
    Fetch all discussions on a merge request.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        mr_iid (int): Merge request IID.

    Returns:
        list[dict[str, Any]]: List of discussion objects.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/projects/{_encode_project(repo)}/merge_requests/{mr_iid}/discussions",
        )
        response.raise_for_status()

    discussions: list[dict[str, Any]] = response.json()

    return discussions


async def create_branch(
    token: str,
    repo: str,
    branch: str,
    ref: str,
) -> None:
    """
    Create a branch from a given ref.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        branch (str): New branch name.
        ref (str): Source branch or SHA to branch from.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/projects/{_encode_project(repo)}/repository/branches",
            json={"branch": branch, "ref": ref},
        )
        response.raise_for_status()


async def delete_branch(token: str, repo: str, branch: str) -> None:
    """
    Delete a branch.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        branch (str): Branch name to delete.
    """

    async with _gitlab_client(token) as client:
        encoded_branch = quote(branch, safe="")
        response = await request_with_retry(
            client,
            "DELETE",
            f"/projects/{_encode_project(repo)}/repository/branches/{encoded_branch}",
        )
        response.raise_for_status()


async def create_or_update_file(
    token: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
) -> None:
    """
    Create or update a file in a repository.

    Uses the create endpoint; falls back to update if the file already exists.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        path (str): File path within the repository.
        content (str): File content (plain text).
        message (str): Commit message.
        branch (str): Branch to commit to.
    """

    encoded_project = _encode_project(repo)
    encoded_path = quote(path, safe="")

    async with _gitlab_client(token) as client:
        payload = {
            "branch": branch,
            "content": content,
            "commit_message": message,
        }

        response = await request_with_retry(
            client,
            "POST",
            f"/projects/{encoded_project}/repository/files/{encoded_path}",
            json=payload,
        )

        if response.status_code == 400:
            response = await request_with_retry(
                client,
                "PUT",
                f"/projects/{encoded_project}/repository/files/{encoded_path}",
                json=payload,
            )

        response.raise_for_status()


async def fetch_latest_webhook_event(
    token: str,
    repo: str,
    hook_id: int,
) -> dict[str, Any] | None:
    """
    Fetch the most recent event for a project webhook.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        hook_id (int): Webhook ID.

    Returns:
        dict[str, Any] | None: The most recent event object, or ``None`` if
            no events exist.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/projects/{_encode_project(repo)}/hooks/{hook_id}/events",
            params={"per_page": 1},
        )
        response.raise_for_status()
        events: list[dict[str, Any]] = response.json()

    if not events:
        return None

    return events[0]


async def resend_webhook_event(
    token: str,
    repo: str,
    hook_id: int,
    event_id: int,
) -> None:
    """
    Request GitLab to resend a webhook event.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        hook_id (int): Webhook ID.
        event_id (int): Event ID to resend.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/projects/{_encode_project(repo)}/hooks/{hook_id}/events/{event_id}/resend",
        )
        response.raise_for_status()


async def create_webhook(
    token: str,
    repo: str,
    url: str,
    secret: str,
    merge_requests_events: bool = True,
) -> int:
    """
    Register a webhook on a project.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        url (str): Webhook payload delivery URL.
        secret (str): Webhook secret token.
        merge_requests_events (bool): Subscribe to merge request events.

    Returns:
        int: The webhook ID.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/projects/{_encode_project(repo)}/hooks",
            json={
                "url": url,
                "token": secret,
                "merge_requests_events": merge_requests_events,
                "push_events": False,
            },
        )
        response.raise_for_status()
        hook_data: dict[str, Any] = response.json()

    hook_id: int = hook_data["id"]

    return hook_id


async def delete_webhook(token: str, repo: str, hook_id: int) -> None:
    """
    Delete a webhook from a project.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        hook_id (int): Webhook ID to delete.
    """

    async with _gitlab_client(token) as client:
        response = await request_with_retry(
            client,
            "DELETE",
            f"/projects/{_encode_project(repo)}/hooks/{hook_id}",
        )
        response.raise_for_status()


async def wait_for_pipeline(
    token: str,
    repo: str,
    branch: str,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """
    Poll until a pipeline on the given branch completes.

    Args:
        token (str): GitLab personal access token.
        repo (str): Project path.
        branch (str): Branch name to filter pipelines by.
        timeout (float): Maximum seconds to wait.

    Returns:
        dict[str, Any]: The completed pipeline object.

    Raises:
        TimeoutError: If no completed pipeline is found within the timeout.
    """

    elapsed = 0.0

    async with _gitlab_client(token) as client:
        while elapsed < timeout:
            response = await request_with_retry(
                client,
                "GET",
                f"/projects/{_encode_project(repo)}/pipelines",
                params={"ref": branch, "per_page": 5},
            )
            response.raise_for_status()
            pipelines: list[dict[str, Any]] = response.json()

            for pipeline in pipelines:
                if pipeline["status"] in ("success", "failed", "canceled"):
                    return pipeline

            await asyncio.sleep(PIPELINE_POLL_INTERVAL)
            elapsed += PIPELINE_POLL_INTERVAL

    raise TimeoutError(f"No completed pipeline on {repo}@{branch} after {timeout}s")
