import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

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
from nominal_code.platforms.gitlab import GitLabPlatform
from nominal_code.server.app import create_app
from tests.integration.conftest import (
    BranchInfo,
    install_enqueue_hook,
    wait_for_webhook_processing,
)
from tests.integration.gitlab.api import (
    close_mr,
    create_mr,
    create_webhook,
    delete_webhook,
    fetch_latest_webhook_event,
    fetch_mr_notes,
    resend_webhook_event,
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
        agent=CliAgentConfig(),
        coding_guidelines="",
        language_guidelines={},
        cleanup_interval_hours=0,
        reviewer_triggers=frozenset({EventType.PR_OPENED}),
        pr_title_include_tags=frozenset({"nominalbot"}),
    )

    platform = GitLabPlatform(
        token=gitlab_token,
        webhook_secret=WEBHOOK_SECRET,
    )
    conversation_store = MemoryConversationStore()
    job_queue = AsyncioJobQueue()
    platforms = {"gitlab": platform}
    in_process_runner = ProcessRunner(
        config=config,
        platforms=platforms,
        conversation_store=conversation_store,
        queue=job_queue,
    )

    app = create_app(
        config=config,
        platforms=platforms,
        runner=in_process_runner,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=0)
    await site.start()

    server = site._server
    assert isinstance(server, asyncio.Server), "Server not started"
    assert server.sockets, "No sockets bound"
    sockets = server.sockets
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

        job_enqueued = install_enqueue_hook(job_queue)

        with patch(
            "nominal_code.agent.cli.runner.run",
            new_callable=AsyncMock,
            return_value=BUGGY_AGENT_RESULT,
        ):
            mr_iid = await create_mr(
                token=gitlab_token,
                repo=GITLAB_TEST_REPO,
                head=gitlab_webhook_branch.branch_name,
                title=f"test: webhook server [nominalbot] [{pipeline_id}]",
            )

            await wait_for_mr_diff(
                token=gitlab_token,
                repo=GITLAB_TEST_REPO,
                mr_iid=mr_iid,
            )

            async def _attempt_gitlab_redelivery() -> None:
                event = await fetch_latest_webhook_event(
                    token=gitlab_token,
                    repo=GITLAB_TEST_REPO,
                    hook_id=hook_id,
                )

                if event is None:
                    raise TimeoutError(
                        "No webhook job enqueued and GitLab never attempted delivery",
                    )

                event_id: int = event["id"]
                await resend_webhook_event(
                    token=gitlab_token,
                    repo=GITLAB_TEST_REPO,
                    hook_id=hook_id,
                    event_id=event_id,
                )

            await wait_for_webhook_processing(
                job_enqueued,
                job_queue,
                attempt_redelivery=_attempt_gitlab_redelivery,
            )

            notes = await fetch_mr_notes(
                token=gitlab_token,
                repo=GITLAB_TEST_REPO,
                mr_iid=mr_iid,
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
