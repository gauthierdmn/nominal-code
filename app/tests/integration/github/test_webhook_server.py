import asyncio
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
from nominal_code.platforms.github import GitHubPlatform
from nominal_code.platforms.github.auth import GitHubPatAuth
from nominal_code.webhooks.server import create_app
from tests.integration.conftest import BranchInfo, wait_for_webhook_processing
from tests.integration.github.api import (
    close_pr,
    create_pr,
    create_webhook,
    delete_webhook,
    fetch_latest_delivery,
    fetch_pr_reviews,
    redeliver_webhook,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    GITHUB_TEST_REPO,
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
    github_token: str,
    github_webhook_branch: BranchInfo,
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
        pr_title_include_tags=frozenset({"nominalbot"}),
    )

    platform = GitHubPlatform(
        auth=GitHubPatAuth(token=github_token),
        webhook_secret=WEBHOOK_SECRET,
    )
    session_store = SessionStore()
    session_queue = SessionQueue()

    app = create_app(
        config=config,
        platforms={"github": platform},
        session_store=session_store,
        session_queue=session_queue,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 0)
    await site.start()

    server = site._server
    assert isinstance(server, asyncio.Server), "Server not started"
    assert server.sockets, "No sockets bound"
    sockets = server.sockets
    port = sockets[0].getsockname()[1]

    tunnel = None
    hook_id = None
    pr_number = None

    try:
        tunnel = await start_tunnel(port)

        hook_id = await create_webhook(
            token=github_token,
            repo=GITHUB_TEST_REPO,
            url=f"{tunnel.public_url}/webhooks/github",
            secret=WEBHOOK_SECRET,
            events=["pull_request"],
        )

        await wait_for_tunnel_ready(tunnel.public_url)

        with patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new_callable=AsyncMock,
            return_value=BUGGY_AGENT_RESULT,
        ):
            pr_number = await create_pr(
                token=github_token,
                repo=GITHUB_TEST_REPO,
                head=github_webhook_branch.branch_name,
                title=f"test: webhook server [nominalbot] [{pipeline_id}]",
            )

            async def _attempt_github_redelivery() -> None:
                delivery = await fetch_latest_delivery(
                    github_token,
                    GITHUB_TEST_REPO,
                    hook_id,
                )

                if delivery is None:
                    raise TimeoutError(
                        "No webhook job enqueued and GitHub never attempted delivery",
                    )

                delivery_id: int = delivery["id"]
                await redeliver_webhook(
                    github_token,
                    GITHUB_TEST_REPO,
                    hook_id,
                    delivery_id,
                )

            await wait_for_webhook_processing(
                session_queue,
                attempt_redelivery=_attempt_github_redelivery,
            )

            reviews = await fetch_pr_reviews(
                github_token,
                GITHUB_TEST_REPO,
                pr_number,
            )

            review_bodies = [review["body"] for review in reviews if review.get("body")]

            assert len(review_bodies) >= 1, "Expected at least one review"
            assert any("Found issues" in body for body in review_bodies)

    finally:
        if hook_id is not None:
            try:
                await delete_webhook(
                    github_token,
                    GITHUB_TEST_REPO,
                    hook_id,
                )
            except Exception:
                pass

        if pr_number is not None:
            try:
                await close_pr(
                    github_token,
                    GITHUB_TEST_REPO,
                    pr_number,
                )
            except Exception:
                pass

        if tunnel is not None:
            await stop_tunnel(tunnel)

        await runner.cleanup()
