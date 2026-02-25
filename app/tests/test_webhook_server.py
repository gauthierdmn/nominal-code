# type: ignore
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from nominal_code.bot_type import BotType
from nominal_code.config import ReviewerConfig, WorkerConfig
from nominal_code.platforms.base import PlatformName, ReviewComment
from nominal_code.session import SessionQueue, SessionStore
from nominal_code.webhook_server import create_app


def _make_config(worker=True, reviewer=True):
    config = MagicMock()
    config.worker = (
        WorkerConfig(bot_username="claude-worker", system_prompt="Be concise.")
        if worker
        else None
    )
    config.reviewer = (
        ReviewerConfig(bot_username="claude-reviewer", system_prompt="Review code.")
        if reviewer
        else None
    )
    config.allowed_users = frozenset(["alice"])
    config.workspace_base_dir = "/tmp/workspaces"
    config.agent_model = ""
    config.agent_max_turns = 0
    config.agent_cli_path = ""

    return config


def _make_github_platform():
    platform = MagicMock()
    platform.verify_webhook = MagicMock(return_value=True)
    platform.parse_webhook = MagicMock(return_value=None)
    platform.post_reaction = AsyncMock()
    platform.post_reply = AsyncMock()
    platform.fetch_pr_branch = AsyncMock(return_value="")

    return platform


@pytest.fixture
def app():
    config = _make_config()
    github_platform = _make_github_platform()
    platforms = {"github": github_platform}

    return create_app(
        config=config,
        platforms=platforms,
        session_store=SessionStore(),
        session_queue=SessionQueue(),
    )


@pytest_asyncio.fixture
async def client(app, aiohttp_client):
    return await aiohttp_client(app)


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        response = await client.get("/health")

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "ok"


class TestGitHubWebhook:
    @pytest.mark.asyncio
    async def test_github_webhook_invalid_signature(self, client, app):
        app["platforms"]["github"].verify_webhook.return_value = False
        payload = {"action": "created"}

        response = await client.post(
            "/webhooks/github",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 401

    @pytest.mark.asyncio
    async def test_github_webhook_irrelevant_event(self, client, app):
        app["platforms"]["github"].parse_webhook.return_value = None
        payload = {"action": "opened"}

        response = await client.post(
            "/webhooks/github",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_github_webhook_no_mention(self, client, app):
        comment = ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="just a normal comment",
            diff_hunk="",
            file_path="",
            clone_url="",
        )
        app["platforms"]["github"].parse_webhook.return_value = comment

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "no_mention"

    @pytest.mark.asyncio
    async def test_github_webhook_worker_mention(self, client, app):
        comment = ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix the bug",
            diff_hunk="",
            file_path="",
            clone_url="",
        )
        app["platforms"]["github"].parse_webhook.return_value = comment

        with patch(
            "nominal_code.webhook_server.handle_comment",
            new_callable=AsyncMock,
        ) as mock_handle:
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200

            data = await response.json()

            assert data["status"] == "accepted"
            mock_handle.assert_called_once()
            call_kwargs = mock_handle.call_args.kwargs

            assert call_kwargs["bot_type"] == BotType.WORKER
            assert call_kwargs["prompt"] == "fix the bug"

    @pytest.mark.asyncio
    async def test_github_webhook_reviewer_mention(self, client, app):
        comment = ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="@claude-reviewer review this PR",
            diff_hunk="",
            file_path="",
            clone_url="",
        )
        app["platforms"]["github"].parse_webhook.return_value = comment

        with patch(
            "nominal_code.webhook_server.handle_comment",
            new_callable=AsyncMock,
        ) as mock_handle:
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200

            data = await response.json()

            assert data["status"] == "accepted"
            mock_handle.assert_called_once()
            call_kwargs = mock_handle.call_args.kwargs

            assert call_kwargs["bot_type"] == BotType.REVIEWER
            assert call_kwargs["prompt"] == "review this PR"

    @pytest.mark.asyncio
    async def test_github_webhook_worker_takes_precedence_over_reviewer(
        self,
        client,
        app,
    ):
        comment = ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="@claude-worker @claude-reviewer do stuff",
            diff_hunk="",
            file_path="",
            clone_url="",
        )
        app["platforms"]["github"].parse_webhook.return_value = comment

        with patch(
            "nominal_code.webhook_server.handle_comment",
            new_callable=AsyncMock,
        ) as mock_handle:
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200
            call_kwargs = mock_handle.call_args.kwargs

            assert call_kwargs["bot_type"] == BotType.WORKER


class TestSingleBotConfig:
    @pytest.mark.asyncio
    async def test_reviewer_only_config_ignores_worker_mentions(self, aiohttp_client):
        config = _make_config(worker=False, reviewer=True)
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}

        app = create_app(
            config=config,
            platforms=platforms,
            session_store=SessionStore(),
            session_queue=SessionQueue(),
        )
        client = await aiohttp_client(app)

        comment = ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix the bug",
            diff_hunk="",
            file_path="",
            clone_url="",
        )
        app["platforms"]["github"].parse_webhook.return_value = comment

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "no_mention"

    @pytest.mark.asyncio
    async def test_worker_only_config_ignores_reviewer_mentions(self, aiohttp_client):
        config = _make_config(worker=True, reviewer=False)
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}

        app = create_app(
            config=config,
            platforms=platforms,
            session_store=SessionStore(),
            session_queue=SessionQueue(),
        )
        client = await aiohttp_client(app)

        comment = ReviewComment(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            comment_id=100,
            author_username="alice",
            body="@claude-reviewer review this PR",
            diff_hunk="",
            file_path="",
            clone_url="",
        )
        app["platforms"]["github"].parse_webhook.return_value = comment

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "no_mention"
