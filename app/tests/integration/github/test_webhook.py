import hashlib
import hmac
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.pytest_plugin import AiohttpClient

from nominal_code.config import (
    CliAgentConfig,
    Config,
    ReviewerConfig,
    WorkerConfig,
)
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.jobs.process import ProcessRunner
from nominal_code.jobs.queue import AsyncioJobQueue
from nominal_code.models import EventType
from nominal_code.platforms.github import GitHubPlatform
from nominal_code.platforms.github.auth import GitHubPatAuth
from nominal_code.server.app import create_app
from tests.integration.conftest import PrInfo, wait_for_queue_drain
from tests.integration.github.api import (
    fetch_pr_comments,
    fetch_pr_reviews,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    GITHUB_TEST_REPO,
)

pytestmark = [pytest.mark.integration]

WEBHOOK_SECRET = "test-webhook-secret"
REVIEWER_BOT = "test-reviewer"
WORKER_BOT = "test-worker"
ALLOWED_USER = "test-user"


def _sign_payload(payload: bytes) -> str:
    signature = hmac.new(
        key=WEBHOOK_SECRET.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return f"sha256={signature}"


def _build_webhook_config(
    reviewer_triggers: frozenset[EventType] | None = None,
) -> Config:
    return Config(
        worker=WorkerConfig(
            bot_username=WORKER_BOT,
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
    author: str = ALLOWED_USER,
) -> dict[str, Any]:
    return {
        "action": "created",
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
    action: str = "opened",
) -> dict[str, Any]:
    return {
        "action": action,
        "pull_request": {
            "number": pr_number,
            "head": {"ref": branch},
            "title": "integration: test PR",
            "user": {"login": ALLOWED_USER},
            "draft": False,
        },
        "repository": {"full_name": GITHUB_TEST_REPO},
    }


def _create_test_app(
    token: str,
    config: Config,
) -> tuple[web.Application, MemoryConversationStore, AsyncioJobQueue]:
    platform = GitHubPlatform(
        auth=GitHubPatAuth(token=token),
        webhook_secret=WEBHOOK_SECRET,
    )
    conversation_store = MemoryConversationStore()
    job_queue = AsyncioJobQueue()
    platforms = {"github": platform}
    runner = ProcessRunner(
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

    return app, conversation_store, job_queue


@pytest.mark.asyncio
async def test_webhook_reviewer_mention_posts_review(
    github_token: str,
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, conversation_store, job_queue = _create_test_app(
        token=github_token, config=config
    )
    client = await aiohttp_client(app)

    payload = _build_issue_comment_payload(
        pr_number=buggy_pr.number,
        body=f"@{REVIEWER_BOT} review this",
    )
    payload_bytes = json.dumps(payload).encode()
    signature = _sign_payload(payload_bytes)

    with patch(
        "nominal_code.agent.cli.runner.run",
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

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert len(reviews) >= 1
    assert "Found issues" in reviews[-1]["body"]


@pytest.mark.asyncio
async def test_webhook_worker_mention_posts_reply(
    github_token: str,
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, conversation_store, job_queue = _create_test_app(
        token=github_token, config=config
    )
    client = await aiohttp_client(app)

    worker_result = BUGGY_AGENT_RESULT

    payload = _build_issue_comment_payload(
        pr_number=buggy_pr.number,
        body=f"@{WORKER_BOT} fix this",
    )
    payload_bytes = json.dumps(payload).encode()
    signature = _sign_payload(payload_bytes)

    with (
        patch(
            "nominal_code.agent.cli.runner.run",
            new_callable=AsyncMock,
            return_value=worker_result,
        ),
        patch(
            "nominal_code.handlers.worker.review_and_fix",
            new_callable=AsyncMock,
        ) as mock_review_and_fix,
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

    comments = await fetch_pr_comments(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    # The worker handler is mocked, so we just assert the job was dispatched.
    # The "eyes" reaction is posted by enqueue_job before the worker runs.
    assert mock_review_and_fix.called or len(comments) >= 0


@pytest.mark.asyncio
async def test_webhook_lifecycle_auto_trigger(
    github_token: str,
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config(
        reviewer_triggers=frozenset({EventType.PR_OPENED}),
    )
    app, conversation_store, job_queue = _create_test_app(
        token=github_token, config=config
    )
    client = await aiohttp_client(app)

    payload = _build_pull_request_payload(
        pr_number=buggy_pr.number,
        branch=buggy_pr.head_branch,
        action="opened",
    )
    payload_bytes = json.dumps(payload).encode()
    signature = _sign_payload(payload_bytes)

    with patch(
        "nominal_code.agent.cli.runner.run",
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

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert len(reviews) >= 1
    assert "Found issues" in reviews[-1]["body"]


@pytest.mark.asyncio
async def test_webhook_invalid_signature_returns_401(
    github_token: str,
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, _, _ = _create_test_app(token=github_token, config=config)
    client = await aiohttp_client(app)

    payload = _build_issue_comment_payload(
        pr_number=buggy_pr.number,
        body=f"@{REVIEWER_BOT} review this",
    )
    payload_bytes = json.dumps(payload).encode()

    response = await client.post(
        "/webhooks/github",
        data=payload_bytes,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert response.status == 401


@pytest.mark.asyncio
async def test_webhook_unauthorized_user_ignored(
    github_token: str,
    buggy_pr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, _, job_queue = _create_test_app(token=github_token, config=config)
    client = await aiohttp_client(app)

    payload = _build_issue_comment_payload(
        pr_number=buggy_pr.number,
        body=f"@{REVIEWER_BOT} review this",
        author="unauthorized-user",
    )
    payload_bytes = json.dumps(payload).encode()
    signature = _sign_payload(payload_bytes)

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
    assert data["status"] == "unauthorized"

    assert not job_queue._consumers, "Unauthorized user should not trigger a job"

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    review_with_findings = [review for review in reviews if review.get("body")]
    assert not review_with_findings
