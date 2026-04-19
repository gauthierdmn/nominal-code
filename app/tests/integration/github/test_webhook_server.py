import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from nominal_code.commands.webhook.jobs.queue.asyncio import AsyncioJobQueue
from nominal_code.commands.webhook.jobs.runner.process import ProcessRunner
from nominal_code.commands.webhook.main import create_app
from nominal_code.config import (
    CliAgentConfig,
    Config,
    ReviewerConfig,
    WebhookConfig,
    WorkspaceConfig,
)
from nominal_code.config.policies import FilteringPolicy, RoutingPolicy
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.models import EventType
from nominal_code.platforms.github import GitHubPlatform
from nominal_code.platforms.github.auth import GitHubPatAuth
from tests.integration.conftest import (
    BranchInfo,
    install_enqueue_hook,
    wait_for_webhook_processing,
)
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
ALLOWED_USER = "test-user"


@pytest.mark.asyncio
async def test_webhook_server_posts_review(
    github_token: str,
    github_webhook_branch: BranchInfo,
    pipeline_id: str,
) -> None:
    config = Config(
        reviewer=ReviewerConfig(
            bot_username=REVIEWER_BOT,
        ),
        agent=CliAgentConfig(system_prompt="You are a test reviewer."),
        workspace=WorkspaceConfig(base_dir=Path(tempfile.mkdtemp())),
        webhook=WebhookConfig(
            host="0.0.0.0",
            port=0,
            filtering=FilteringPolicy(
                allowed_users=frozenset({ALLOWED_USER}),
                pr_title_include_tags=frozenset({"nominalbot"}),
            ),
            routing=RoutingPolicy(
                reviewer_triggers=frozenset({EventType.PR_OPENED}),
                reviewer_bot_username=REVIEWER_BOT,
            ),
        ),
    )

    platform = GitHubPlatform(
        auth=GitHubPatAuth(token=github_token),
        webhook_secret=WEBHOOK_SECRET,
    )
    conversation_store = MemoryConversationStore()
    job_queue = AsyncioJobQueue()
    platforms = {"github": platform}
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

        job_enqueued = install_enqueue_hook(job_queue)

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
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
                    token=github_token,
                    repo=GITHUB_TEST_REPO,
                    hook_id=hook_id,
                )

                if delivery is None:
                    raise TimeoutError(
                        "No webhook job enqueued and GitHub never attempted delivery",
                    )

                delivery_id: int = delivery["id"]
                await redeliver_webhook(
                    token=github_token,
                    repo=GITHUB_TEST_REPO,
                    hook_id=hook_id,
                    delivery_id=delivery_id,
                )

            await wait_for_webhook_processing(
                job_enqueued,
                job_queue,
                attempt_redelivery=_attempt_github_redelivery,
            )

            reviews = await fetch_pr_reviews(
                token=github_token,
                repo=GITHUB_TEST_REPO,
                pr_number=pr_number,
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
