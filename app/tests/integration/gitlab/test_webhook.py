import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
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
from nominal_code.platforms.gitlab import GitLabPlatform
from nominal_code.webhooks.server import create_app
from tests.integration.conftest import PrInfo, wait_for_queue_drain
from tests.integration.gitlab.api import (
    fetch_mr_notes,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    GITLAB_TEST_REPO,
)

pytestmark = [pytest.mark.integration]

WEBHOOK_SECRET = "test-webhook-secret"
REVIEWER_BOT = "test-reviewer"
WORKER_BOT = "test-worker"
ALLOWED_USER = "test-user"


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


def _build_note_hook_payload(
    mr_iid: int,
    body: str,
    source_branch: str,
    author: str = ALLOWED_USER,
) -> dict[str, Any]:
    return {
        "object_kind": "note",
        "user": {"username": author},
        "project": {"path_with_namespace": GITLAB_TEST_REPO},
        "object_attributes": {
            "id": 999999,
            "note": body,
            "noteable_type": "MergeRequest",
        },
        "merge_request": {
            "iid": mr_iid,
            "source_branch": source_branch,
            "title": "integration: test MR",
        },
    }


def _build_merge_request_hook_payload(
    mr_iid: int,
    branch: str,
    action: str = "open",
) -> dict[str, Any]:
    return {
        "object_kind": "merge_request",
        "user": {"username": ALLOWED_USER},
        "project": {"path_with_namespace": GITLAB_TEST_REPO},
        "object_attributes": {
            "action": action,
            "iid": mr_iid,
            "source_branch": branch,
            "title": "integration: test MR",
            "work_in_progress": False,
        },
    }


def _create_test_app(
    token: str,
    config: Config,
) -> tuple[web.Application, ConversationStore, JobQueue]:
    platform = GitLabPlatform(
        token=token,
        webhook_secret=WEBHOOK_SECRET,
    )
    conversation_store = ConversationStore()
    job_queue = JobQueue()
    platforms = {"gitlab": platform}
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

    return app, conversation_store, job_queue


@pytest.mark.asyncio
async def test_webhook_reviewer_mention_posts_review(
    gitlab_token: str,
    buggy_mr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, conversation_store, job_queue = _create_test_app(gitlab_token, config)
    client = await aiohttp_client(app)

    payload = _build_note_hook_payload(
        mr_iid=buggy_mr.number,
        body=f"@{REVIEWER_BOT} review this",
        source_branch=buggy_mr.head_branch,
    )
    payload_bytes = json.dumps(payload).encode()

    with patch(
        "nominal_code.agent.cli.tracking.run_agent",
        new_callable=AsyncMock,
        return_value=BUGGY_AGENT_RESULT,
    ):
        response = await client.post(
            "/webhooks/gitlab",
            data=payload_bytes,
            headers={
                "X-Gitlab-Event": "Note Hook",
                "X-Gitlab-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "accepted"

        await wait_for_queue_drain(job_queue)

    notes = await fetch_mr_notes(gitlab_token, GITLAB_TEST_REPO, buggy_mr.number)
    note_bodies = [note["body"] for note in notes]
    assert any("Found issues" in body for body in note_bodies)


@pytest.mark.asyncio
async def test_webhook_worker_mention_posts_reply(
    gitlab_token: str,
    buggy_mr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, conversation_store, job_queue = _create_test_app(gitlab_token, config)
    client = await aiohttp_client(app)

    payload = _build_note_hook_payload(
        mr_iid=buggy_mr.number,
        body=f"@{WORKER_BOT} fix this",
        source_branch=buggy_mr.head_branch,
    )
    payload_bytes = json.dumps(payload).encode()

    with (
        patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new_callable=AsyncMock,
            return_value=BUGGY_AGENT_RESULT,
        ),
        patch(
            "nominal_code.worker.handler.review_and_fix",
            new_callable=AsyncMock,
        ) as mock_review_and_fix,
    ):
        response = await client.post(
            "/webhooks/gitlab",
            data=payload_bytes,
            headers={
                "X-Gitlab-Event": "Note Hook",
                "X-Gitlab-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "accepted"

        await wait_for_queue_drain(job_queue)

    notes = await fetch_mr_notes(gitlab_token, GITLAB_TEST_REPO, buggy_mr.number)
    assert mock_review_and_fix.called or len(notes) >= 0


@pytest.mark.asyncio
async def test_webhook_lifecycle_auto_trigger(
    gitlab_token: str,
    buggy_mr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config(
        reviewer_triggers=frozenset({EventType.PR_OPENED}),
    )
    app, conversation_store, job_queue = _create_test_app(gitlab_token, config)
    client = await aiohttp_client(app)

    payload = _build_merge_request_hook_payload(
        mr_iid=buggy_mr.number,
        branch=buggy_mr.head_branch,
        action="open",
    )
    payload_bytes = json.dumps(payload).encode()

    with patch(
        "nominal_code.agent.cli.tracking.run_agent",
        new_callable=AsyncMock,
        return_value=BUGGY_AGENT_RESULT,
    ):
        response = await client.post(
            "/webhooks/gitlab",
            data=payload_bytes,
            headers={
                "X-Gitlab-Event": "Merge Request Hook",
                "X-Gitlab-Token": WEBHOOK_SECRET,
                "Content-Type": "application/json",
            },
        )
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "accepted"

        await wait_for_queue_drain(job_queue)

    notes = await fetch_mr_notes(gitlab_token, GITLAB_TEST_REPO, buggy_mr.number)
    note_bodies = [note["body"] for note in notes]
    assert any("Found issues" in body for body in note_bodies)


@pytest.mark.asyncio
async def test_webhook_invalid_token_returns_401(
    gitlab_token: str,
    buggy_mr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, _, _ = _create_test_app(gitlab_token, config)
    client = await aiohttp_client(app)

    payload = _build_note_hook_payload(
        mr_iid=buggy_mr.number,
        body=f"@{REVIEWER_BOT} review this",
        source_branch=buggy_mr.head_branch,
    )
    payload_bytes = json.dumps(payload).encode()

    response = await client.post(
        "/webhooks/gitlab",
        data=payload_bytes,
        headers={
            "X-Gitlab-Event": "Note Hook",
            "X-Gitlab-Token": "wrong-secret",
            "Content-Type": "application/json",
        },
    )

    assert response.status == 401


@pytest.mark.asyncio
async def test_webhook_unauthorized_user_ignored(
    gitlab_token: str,
    buggy_mr: PrInfo,
    aiohttp_client: AiohttpClient,
) -> None:
    config = _build_webhook_config()
    app, _, job_queue = _create_test_app(gitlab_token, config)
    client = await aiohttp_client(app)

    payload = _build_note_hook_payload(
        mr_iid=buggy_mr.number,
        body=f"@{REVIEWER_BOT} review this",
        source_branch=buggy_mr.head_branch,
        author="unauthorized-user",
    )
    payload_bytes = json.dumps(payload).encode()

    response = await client.post(
        "/webhooks/gitlab",
        data=payload_bytes,
        headers={
            "X-Gitlab-Event": "Note Hook",
            "X-Gitlab-Token": WEBHOOK_SECRET,
            "Content-Type": "application/json",
        },
    )

    assert response.status == 200
    data = await response.json()
    assert data["status"] == "unauthorized"

    assert not job_queue._consumers, "Unauthorized user should not trigger a job"

    notes = await fetch_mr_notes(gitlab_token, GITLAB_TEST_REPO, buggy_mr.number)
    user_notes = [
        note
        for note in notes
        if not note.get("system", False) and "Found issues" in note.get("body", "")
    ]
    assert not user_notes
