import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from nominal_code.http import request_with_retry

GITHUB_API_BASE = "https://api.github.com"
TIMEOUT = 30.0
WORKFLOW_POLL_INTERVAL = 10.0
CREATE_PR_RETRY_DELAY = 3.0
CREATE_PR_MAX_RETRIES = 5

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _github_client(token: str) -> AsyncIterator[httpx.AsyncClient]:
    """
    Yield an authenticated GitHub API client.

    Args:
        token (str): GitHub API token.

    Yields:
        httpx.AsyncClient: Configured HTTP client.
    """

    async with httpx.AsyncClient(
        base_url=GITHUB_API_BASE,
        timeout=TIMEOUT,
        headers=_headers(token),
    ) as client:
        yield client


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


async def create_pr(
    token: str,
    repo: str,
    head: str,
    base: str = "main",
    title: str = "test: PR",
) -> int:
    """
    Create a pull request on a unique branch.

    Retries on transient 422 errors with a short delay.

    Args:
        token (str): GitHub API token.
        repo (str): Repository full name (owner/repo).
        head (str): Head branch name.
        base (str): Base branch name.
        title (str): PR title.

    Returns:
        int: The PR number.
    """

    async with _github_client(token) as client:
        for attempt in range(1, CREATE_PR_MAX_RETRIES + 1):
            response = await request_with_retry(
                client,
                "POST",
                f"/repos/{repo}/pulls",
                json={"title": title, "head": head, "base": base},
            )

            if response.status_code != 422:
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                pr_number: int = data["number"]

                return pr_number

            logger.warning(
                "PR creation returned 422 for %s (attempt %d/%d): %s",
                head,
                attempt,
                CREATE_PR_MAX_RETRIES,
                response.text,
            )

            if attempt < CREATE_PR_MAX_RETRIES:
                await asyncio.sleep(CREATE_PR_RETRY_DELAY)

        response.raise_for_status()
        fallback: dict[str, Any] = response.json()
        fallback_number: int = fallback["number"]

        return fallback_number


async def close_pr(token: str, repo: str, pr_number: int) -> None:
    """
    Close a pull request. Silently succeeds if already closed.

    Args:
        token (str): GitHub API token.
        repo (str): Repository full name (owner/repo).
        pr_number (int): PR number to close.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "PATCH",
            f"/repos/{repo}/pulls/{pr_number}",
            json={"state": "closed"},
        )

        if response.status_code == 422:
            return

        response.raise_for_status()


async def fetch_pr_reviews(
    token: str,
    repo: str,
    pr_number: int,
) -> list[dict[str, Any]]:
    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/repos/{repo}/pulls/{pr_number}/reviews",
        )
        response.raise_for_status()

    reviews: list[dict[str, Any]] = response.json()

    return reviews


async def fetch_pr_review_comments(
    token: str,
    repo: str,
    pr_number: int,
) -> list[dict[str, Any]]:
    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/repos/{repo}/pulls/{pr_number}/comments",
        )
        response.raise_for_status()

    comments: list[dict[str, Any]] = response.json()

    return comments


async def fetch_pr_comments(
    token: str,
    repo: str,
    pr_number: int,
) -> list[dict[str, Any]]:
    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/repos/{repo}/issues/{pr_number}/comments",
        )
        response.raise_for_status()

    comments: list[dict[str, Any]] = response.json()

    return comments


async def get_branch_sha(token: str, repo: str, branch: str) -> str:
    """
    Get the HEAD SHA of a branch.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name (e.g. ``owner/repo``).
        branch (str): Branch name.

    Returns:
        str: The SHA of the branch HEAD commit.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/repos/{repo}/branches/{branch}",
        )
        response.raise_for_status()
        branch_data: dict[str, Any] = response.json()

    sha: str = branch_data["commit"]["sha"]

    return sha


async def create_branch(
    token: str,
    repo: str,
    branch: str,
    from_sha: str,
) -> None:
    """
    Create a branch from a given SHA.

    Uses the higher-level Branches API via the create-ref endpoint
    under ``/repos/{repo}/git/refs``. Requires Contents write permission
    on fine-grained tokens.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        branch (str): New branch name.
        from_sha (str): SHA to branch from.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": from_sha},
        )
        response.raise_for_status()


async def delete_branch(token: str, repo: str, branch: str) -> None:
    """
    Delete a branch.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        branch (str): Branch name to delete.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "DELETE",
            f"/repos/{repo}/git/refs/heads/{branch}",
        )
        response.raise_for_status()


async def create_or_update_file(
    token: str,
    repo: str,
    path: str,
    content_b64: str,
    message: str,
    branch: str,
) -> None:
    """
    Create or update a file in a repository via the Contents API.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        path (str): File path within the repository.
        content_b64 (str): Base64-encoded file content.
        message (str): Commit message.
        branch (str): Branch to commit to.
    """

    async with _github_client(token) as client:
        get_response = await request_with_retry(
            client,
            "GET",
            f"/repos/{repo}/contents/{path}",
            params={"ref": branch},
        )

        payload: dict[str, str] = {
            "message": message,
            "content": content_b64,
            "branch": branch,
        }

        if get_response.status_code == 200:
            existing: dict[str, Any] = get_response.json()
            payload["sha"] = existing["sha"]

        response = await request_with_retry(
            client,
            "PUT",
            f"/repos/{repo}/contents/{path}",
            json=payload,
        )
        response.raise_for_status()


async def create_webhook(
    token: str,
    repo: str,
    url: str,
    secret: str,
    events: list[str],
) -> int:
    """
    Register a webhook on a repository.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        url (str): Webhook payload delivery URL.
        secret (str): Webhook secret for HMAC signing.
        events (list[str]): List of event types to subscribe to.

    Returns:
        int: The webhook ID.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/repos/{repo}/hooks",
            json={
                "name": "web",
                "active": True,
                "events": events,
                "config": {
                    "url": url,
                    "content_type": "json",
                    "secret": secret,
                    "insecure_ssl": "0",
                },
            },
        )
        response.raise_for_status()
        hook_data: dict[str, Any] = response.json()

    hook_id: int = hook_data["id"]

    return hook_id


async def delete_webhook(token: str, repo: str, hook_id: int) -> None:
    """
    Delete a webhook from a repository.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        hook_id (int): Webhook ID to delete.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "DELETE",
            f"/repos/{repo}/hooks/{hook_id}",
        )
        response.raise_for_status()


async def fetch_latest_delivery(
    token: str,
    repo: str,
    hook_id: int,
) -> dict[str, Any] | None:
    """
    Fetch the most recent delivery for a webhook.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        hook_id (int): Webhook ID.

    Returns:
        dict[str, Any] | None: The most recent delivery object, or ``None`` if
            no deliveries exist.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "GET",
            f"/repos/{repo}/hooks/{hook_id}/deliveries",
            params={"per_page": 1},
        )
        response.raise_for_status()
        deliveries: list[dict[str, Any]] = response.json()

    if not deliveries:
        return None

    return deliveries[0]


async def redeliver_webhook(
    token: str,
    repo: str,
    hook_id: int,
    delivery_id: int,
) -> None:
    """
    Request GitHub to redeliver a webhook payload.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        hook_id (int): Webhook ID.
        delivery_id (int): Delivery ID to redeliver.
    """

    async with _github_client(token) as client:
        response = await request_with_retry(
            client,
            "POST",
            f"/repos/{repo}/hooks/{hook_id}/deliveries/{delivery_id}/attempts",
        )
        response.raise_for_status()


async def wait_for_workflow_run(
    token: str,
    repo: str,
    branch: str,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """
    Poll until a workflow run on the given branch completes.

    Args:
        token (str): GitHub personal access token.
        repo (str): Repository full name.
        branch (str): Branch name to filter runs by.
        timeout (float): Maximum seconds to wait.

    Returns:
        dict[str, Any]: The completed workflow run object.

    Raises:
        TimeoutError: If no completed run is found within the timeout.
    """

    elapsed = 0.0

    async with _github_client(token) as client:
        while elapsed < timeout:
            response = await request_with_retry(
                client,
                "GET",
                f"/repos/{repo}/actions/runs",
                params={"branch": branch, "per_page": 5},
            )
            response.raise_for_status()
            runs_data: dict[str, Any] = response.json()

            runs: list[dict[str, Any]] = runs_data.get("workflow_runs", [])

            for run in runs:
                if run["status"] == "completed":
                    return run

            await asyncio.sleep(WORKFLOW_POLL_INTERVAL)
            elapsed += WORKFLOW_POLL_INTERVAL

    raise TimeoutError(f"No completed workflow run on {repo}@{branch} after {timeout}s")
