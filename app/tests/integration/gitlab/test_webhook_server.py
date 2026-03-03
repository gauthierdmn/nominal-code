import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from nominal_code.agent.cli.session import SessionQueue, SessionStore
from nominal_code.config import (
    AgentConfig,
    Config,
    ReviewerConfig,
    WorkerConfig,
)
from nominal_code.models import EventType
from nominal_code.platforms.gitlab import GitLabPlatform
from nominal_code.webhooks.server import create_app
from tests.integration.conftest import BranchInfo, wait_for_webhook_processing
from tests.integration.gitlab.api import (
    close_mr,
    create_mr,
    create_webhook,
    delete_webhook,
    fetch_mr_notes,
    wait_for_mr_diff,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    GITLAB_TEST_REPO,
)
from tests.integration.helpers.tunnel import (
    start_tunnel,
    stop_tunnel,
    wait_for_tunnel_ready,
)

pytestmark = [pytest.mark.integration_webhook_server]

WEBHOOK_SECRET = "test-delivery-secret"
REVIEWER_BOT = "test-reviewer"
WORKER_BOT = "test-worker"
ALLOWED_USER = "test-user"


@pytest.mark.asyncio
async def test_webhook_server_posts_review(
    gitlab_token: str,
    gitlab_webhook_branch: BranchInfo,
    pipeline_id: str,
) -> None:
    config = Config(
        worker=WorkerConfig(
            bot_username=WORKER_BOT,
            system_prompt="You are a test worker.",
        ),
        reviewer=ReviewerConfig(
            bot_username=REVIEWER_BOT,
            system_prompt="You are a test reviewer.",
        ),
        webhook_host="0.0.0.0",
        webhook_port=0,
        allowed_users=frozenset({ALLOWED_USER}),
        workspace_base_dir=Path(tempfile.mkdtemp()),
        agent=AgentConfig(use_api=False),
        coding_guidelines="",
        language_guidelines={},
        cleanup_interval_hours=0,
        reviewer_triggers=frozenset({EventType.PR_OPENED}),
    )

    platform = GitLabPlatform(
        token=gitlab_token,
        webhook_secret=WEBHOOK_SECRET,
    )
    session_store = SessionStore()
    session_queue = SessionQueue()

    app = create_app(
        config=config,
        platforms={"gitlab": platform},
        session_store=session_store,
        session_queue=session_queue,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 0)
    await site.start()

    sockets = site._server.sockets
    assert sockets, "No sockets bound"
    port = sockets[0].getsockname()[1]

    tunnel = None
    hook_id = None
    mr_iid = None

    try:
        tunnel = await start_tunnel(port)

        hook_id = await create_webhook(
            token=gitlab_token,
            repo=GITLAB_TEST_REPO,
            url=f"{tunnel.public_url}/webhooks/gitlab",
            secret=WEBHOOK_SECRET,
            merge_requests_events=True,
        )

        await wait_for_tunnel_ready(tunnel.public_url)

        with patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new_callable=AsyncMock,
            return_value=BUGGY_AGENT_RESULT,
        ):
            mr_iid = await create_mr(
                token=gitlab_token,
                repo=GITLAB_TEST_REPO,
                head=gitlab_webhook_branch.branch_name,
                title=f"test: webhook server [{pipeline_id}]",
            )

            await wait_for_mr_diff(
                gitlab_token,
                GITLAB_TEST_REPO,
                mr_iid,
            )

            await wait_for_webhook_processing(session_queue)

            notes = await fetch_mr_notes(
                gitlab_token,
                GITLAB_TEST_REPO,
                mr_iid,
            )

            user_notes = [
                note
                for note in notes
                if not note.get("system", False) and note.get("body")
            ]

            assert len(user_notes) >= 1, "Expected at least one review note"

            note_bodies = [note["body"] for note in user_notes]

            assert any("Found issues" in body for body in note_bodies)

    finally:
        if hook_id is not None:
            try:
                await delete_webhook(
                    gitlab_token,
                    GITLAB_TEST_REPO,
                    hook_id,
                )
            except Exception:
                pass

        if mr_iid is not None:
            try:
                await close_mr(
                    gitlab_token,
                    GITLAB_TEST_REPO,
                    mr_iid,
                )
            except Exception:
                pass

        if tunnel is not None:
            await stop_tunnel(tunnel)

        await runner.cleanup()
