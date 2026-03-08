import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.pytest_plugin import AiohttpClient

from nominal_code.agent.cli.job import JobQueue
from nominal_code.agent.memory import ConversationStore
from nominal_code.config import (
    CliAgentConfig,
    Config,
    ReviewerConfig,
    WorkerConfig,
)
from nominal_code.jobs.in_process import InProcessRunner
from nominal_code.models import EventType
from nominal_code.platforms.github import GitHubPlatform
from nominal_code.platforms.github.auth import GitHubAppAuth
from nominal_code.webhooks.server import create_app
from tests.integration.conftest import PrInfo, wait_for_queue_drain
from tests.integration.github.api import (
    fetch_pr_reviews,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    GITHUB_TEST_REPO,
)

pytestmark = [pytest.mark.integration]

WEBHOOK_SECRET = "test-app-secret"
REVIEWER_BOT = "test-app-reviewer"
ALLOWED_USER = "test-user"


@pytest.fixture(scope="session")
def github_app_credentials() -> tuple[str, str, int]:
    """
    Load GitHub App credentials from environment variables.

    Returns a tuple of (app_id, private_key, installation_id).
    Skips the test session if any credential is missing.
    """

    app_id = os.environ.get("NOMINALBOT_GITHUB_APP_ID", "")
    installation_id_raw = os.environ.get("NOMINALBOT_GITHUB_INSTALLATION_ID", "")

    private_key = os.environ.get("NOMINALBOT_GITHUB_APP_PRIVATE_KEY", "")

    if not private_key:
        key_path = os.environ.get("NOMINALBOT_GITHUB_APP_PRIVATE_KEY_PATH", "")

        if key_path:
            private_key = Path(key_path).read_text()

    if not app_id or not private_key or not installation_id_raw:
        pytest.skip(
            "NOMINALBOT_GITHUB_APP_ID, NOMINALBOT_GITHUB_APP_PRIVATE_KEY "
            "(or _PATH), and NOMINALBOT_GITHUB_INSTALLATION_ID are required"
        )

    return app_id, private_key, int(installation_id_raw)


def _sign_payload(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return f"sha256={signature}"


def _build_webhook_config(
    reviewer_triggers: frozenset[EventType] | None = None,
) -> Config:
    return Config(
        worker=WorkerConfig(
            bot_username="test-app-worker",
            system_prompt="You are a test worker.",
        ),
        reviewer=ReviewerConfig(
            bot_username=REVIEWER_BOT,
            system_prompt="You are a test reviewer.",
        ),
        webhook_host="127.0.0.1",
        webhook_port=0,
        allowed_users=frozenset({ALLOWED_USER}),
        workspace_base_dir=Path(tempfile.mkdtemp()),
        agent=CliAgentConfig(),
        coding_guidelines="",
        language_guidelines={},
        cleanup_interval_hours=0,
        reviewer_triggers=reviewer_triggers or frozenset(),
    )


def _build_issue_comment_payload(
    pr_number: int,
    body: str,
    installation_id: int,
    author: str = ALLOWED_USER,
) -> dict[str, Any]:
    return {
        "action": "created",
        "installation": {"id": installation_id},
        "issue": {
            "number": pr_number,
            "pull_request": {
                "url": f"https://api.github.com/repos/{GITHUB_TEST_REPO}/pulls/{pr_number}"
            },
        },
        "comment": {
            "id": 999999,
            "user": {"login": author},
            "body": body,
        },
        "repository": {"full_name": GITHUB_TEST_REPO},
    }


def _build_pull_request_payload(
    pr_number: int,
    branch: str,
    installation_id: int,
    action: str = "opened",
) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {"id": installation_id},
        "pull_request": {
            "number": pr_number,
            "head": {"ref": branch},
            "title": "integration: test PR",
            "user": {"login": ALLOWED_USER},
            "draft": False,
        },
        "repository": {"full_name": GITHUB_TEST_REPO},
    }


@pytest.mark.asyncio
async def test_app_auth_reviewer_mention_posts_review(
    github_token: str,
    github_app_credentials: tuple[str, str, int],
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    app_id, private_key, installation_id = github_app_credentials

    auth = GitHubAppAuth(
        app_id=app_id,
        private_key=private_key,
    )
    platform = GitHubPlatform(auth=auth, webhook_secret=WEBHOOK_SECRET)

    config = _build_webhook_config()
    conversation_store = ConversationStore()
    job_queue = JobQueue()
    platforms = {"github": platform}
    runner = InProcessRunner(
        config=config,
        platforms=platforms,
        conversation_store=conversation_store,
        job_queue=job_queue,
    )
    app = create_app(
        config=config,
        platforms=platforms,
        runner=runner,
    )
    client = await aiohttp_client(app)

    payload = _build_issue_comment_payload(
        pr_number=buggy_pr.number,
        body=f"@{REVIEWER_BOT} review this",
        installation_id=installation_id,
    )
    payload_bytes = json.dumps(payload).encode()
    signature = _sign_payload(payload_bytes)

    with patch(
        "nominal_code.agent.cli.tracking.run_agent",
        new_callable=AsyncMock,
        return_value=BUGGY_AGENT_RESULT,
    ):
        response = await client.post(
            "/webhooks/github",
            data=payload_bytes,
            headers={
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": signature,
                "Content-Type": "application/json",
            },
        )
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "accepted"

        await wait_for_queue_drain(job_queue)

    assert auth.installation_id == installation_id
    assert auth._cached_token, "App auth should have obtained an installation token"

    reviews = await fetch_pr_reviews(github_token, GITHUB_TEST_REPO, buggy_pr.number)
    assert len(reviews) >= 1
    assert "Found issues" in reviews[-1]["body"]


@pytest.mark.asyncio
async def test_app_auth_lifecycle_auto_trigger(
    github_token: str,
    github_app_credentials: tuple[str, str, int],
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    app_id, private_key, installation_id = github_app_credentials

    auth = GitHubAppAuth(
        app_id=app_id,
        private_key=private_key,
    )
    platform = GitHubPlatform(auth=auth, webhook_secret=WEBHOOK_SECRET)

    config = _build_webhook_config(
        reviewer_triggers=frozenset({EventType.PR_OPENED}),
    )
    conversation_store = ConversationStore()
    job_queue = JobQueue()
    platforms = {"github": platform}
    runner = InProcessRunner(
        config=config,
        platforms=platforms,
        conversation_store=conversation_store,
        job_queue=job_queue,
    )
    app = create_app(
        config=config,
        platforms=platforms,
        runner=runner,
    )
    client = await aiohttp_client(app)

    payload = _build_pull_request_payload(
        pr_number=buggy_pr.number,
        branch=buggy_pr.head_branch,
        installation_id=installation_id,
        action="opened",
    )
    payload_bytes = json.dumps(payload).encode()
    signature = _sign_payload(payload_bytes)

    with patch(
        "nominal_code.agent.cli.tracking.run_agent",
        new_callable=AsyncMock,
        return_value=BUGGY_AGENT_RESULT,
    ):
        response = await client.post(
            "/webhooks/github",
            data=payload_bytes,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": signature,
                "Content-Type": "application/json",
            },
        )
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "accepted"

        await wait_for_queue_drain(job_queue)

    assert auth.installation_id == installation_id

    reviews = await fetch_pr_reviews(github_token, GITHUB_TEST_REPO, buggy_pr.number)
    assert len(reviews) >= 1
    assert "Found issues" in reviews[-1]["body"]


@pytest.mark.asyncio
async def test_app_auth_token_refresh_on_new_installation(
    github_app_credentials: tuple[str, str, int],
) -> None:
    app_id, private_key, installation_id = github_app_credentials

    auth = GitHubAppAuth(
        app_id=app_id,
        private_key=private_key,
    )

    assert not auth._cached_token
    assert auth.installation_id == 0

    auth.set_installation_id(installation_id)

    await auth.refresh_if_needed()

    assert auth._cached_token
    assert auth._token_expires_at > 0
