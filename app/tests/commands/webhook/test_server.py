# type: ignore
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from nominal_code.commands.webhook.server import create_app, should_process_event
from nominal_code.config import CliAgentConfig, ReviewerConfig, WorkerConfig
from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PlatformName,
    ReviewerPlatform,
)


def _make_config(
    worker=True,
    reviewer=True,
    reviewer_triggers=None,
    allowed_repos=None,
    pr_title_include_tags=None,
    pr_title_exclude_tags=None,
):
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
    config.agent = CliAgentConfig()
    config.reviewer_triggers = frozenset(reviewer_triggers or [])
    config.allowed_repos = frozenset(allowed_repos or [])
    config.pr_title_include_tags = frozenset(pr_title_include_tags or [])
    config.pr_title_exclude_tags = frozenset(pr_title_exclude_tags or [])

    return config


def _make_github_platform():
    platform = MagicMock(spec=ReviewerPlatform)
    platform.verify_webhook = MagicMock(return_value=True)
    platform.parse_event = MagicMock(return_value=None)
    platform.post_reaction = AsyncMock()
    platform.post_reply = AsyncMock()
    platform.fetch_pr_branch = AsyncMock(return_value="")
    platform.ensure_auth = AsyncMock()
    platform.build_clone_url = MagicMock(
        return_value="https://x-access-token:test@github.com/owner/repo.git"
    )
    platform.post_pr_reaction = AsyncMock()

    return platform


def _make_runner(config, platforms):
    mock_runner = MagicMock()
    mock_runner.enqueue = AsyncMock()

    return mock_runner


@pytest.fixture
def app():
    config = _make_config()
    github_platform = _make_github_platform()
    platforms = {"github": github_platform}
    runner = _make_runner(config=config, platforms=platforms)

    return create_app(
        config=config,
        platforms=platforms,
        runner=runner,
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
        app["platforms"]["github"].parse_event.return_value = None
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
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="just a normal comment",
        )
        app["platforms"]["github"].parse_event.return_value = comment

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
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix the bug",
        )
        app["platforms"]["github"].parse_event.return_value = comment

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200

            data = await response.json()

            assert data["status"] == "accepted"
            app["runner"].enqueue.assert_called_once()
            job = app["runner"].enqueue.call_args.args[0]

            assert job.bot_type == "worker"

    @pytest.mark.asyncio
    async def test_github_webhook_reviewer_mention(self, client, app):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-reviewer review this PR",
        )
        app["platforms"]["github"].parse_event.return_value = comment

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200

            data = await response.json()

            assert data["status"] == "accepted"
            app["runner"].enqueue.assert_called_once()
            job = app["runner"].enqueue.call_args.args[0]

            assert job.bot_type == "reviewer"

    @pytest.mark.asyncio
    async def test_github_webhook_worker_takes_precedence_over_reviewer(
        self,
        client,
        app,
    ):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-worker @claude-reviewer do stuff",
        )
        app["platforms"]["github"].parse_event.return_value = comment

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200
            job = app["runner"].enqueue.call_args.args[0]

            assert job.bot_type == "worker"


class TestSingleBotConfig:
    @pytest.mark.asyncio
    async def test_reviewer_only_config_ignores_worker_mentions(self, aiohttp_client):
        config = _make_config(worker=False, reviewer=True)
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix the bug",
        )
        app["platforms"]["github"].parse_event.return_value = comment

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
        runner = _make_runner(config=config, platforms=platforms)

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-reviewer review this PR",
        )
        app["platforms"]["github"].parse_event.return_value = comment

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "no_mention"


class TestAutoTrigger:
    @pytest.mark.asyncio
    async def test_auto_trigger_dispatches_when_configured(self, aiohttp_client):
        config = _make_config(
            reviewer=True,
            reviewer_triggers=[EventType.PR_OPENED],
        )
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            event_type=EventType.PR_OPENED,
            pr_title="Add new feature",
            pr_author="alice",
        )
        app["platforms"]["github"].parse_event.return_value = event

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.post(
                "/webhooks/github",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )

            assert response.status == 200

            data = await response.json()

            assert data["status"] == "accepted"
            runner.enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifecycle_event_ignored_when_triggers_not_configured(
        self,
        aiohttp_client,
    ):
        config = _make_config(reviewer=True, reviewer_triggers=[])
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            event_type=EventType.PR_OPENED,
            pr_title="New feature",
            pr_author="alice",
        )
        app["platforms"]["github"].parse_event.return_value = event

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_unconfigured_lifecycle_event_type_ignored(self, aiohttp_client):
        config = _make_config(
            reviewer=True,
            reviewer_triggers=[EventType.PR_OPENED],
        )
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            event_type=EventType.PR_PUSH,
            pr_title="Push event",
            pr_author="bob",
        )
        app["platforms"]["github"].parse_event.return_value = event

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200

        data = await response.json()

        assert data["status"] == "ignored"


class TestHandleHealth:
    @pytest.mark.asyncio
    async def test_handle_health_returns_200(self, client):
        response = await client.get("/health")

        assert response.status == 200

    @pytest.mark.asyncio
    async def test_handle_health_returns_status_ok(self, client):
        response = await client.get("/health")
        data = await response.json()

        assert data == {"status": "ok"}


class TestMakeWebhookHandler:
    def test_make_webhook_handler_returns_callable(self, app):
        from nominal_code.commands.webhook.server import _make_webhook_handler

        handler = _make_webhook_handler("github")

        assert callable(handler)

    @pytest.mark.asyncio
    async def test_make_webhook_handler_routes_correctly(self, client, app):
        app["platforms"]["github"].parse_event.return_value = None
        app["platforms"]["github"].verify_webhook.return_value = True

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
            headers={"X-GitHub-Event": "unknown"},
        )

        assert response.status == 200


class TestHandleWebhook:
    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_signature_returns_401(self, client, app):
        app["platforms"]["github"].verify_webhook.return_value = False

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
        )

        assert response.status == 401

    @pytest.mark.asyncio
    async def test_handle_webhook_ignored_event_returns_ignored(self, client, app):
        app["platforms"]["github"].verify_webhook.return_value = True
        app["platforms"]["github"].parse_event.return_value = None

        response = await client.post(
            "/webhooks/github",
            data=b"{}",
        )

        data = await response.json()

        assert data["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_handle_webhook_no_mention_returns_no_mention(self, client, app):
        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=1,
            author_username="alice",
            body="No mention here",
        )
        app["platforms"]["github"].verify_webhook.return_value = True
        app["platforms"]["github"].parse_event.return_value = event

        response = await client.post("/webhooks/github", data=b"{}")
        data = await response.json()

        assert data["status"] == "no_mention"

    @pytest.mark.asyncio
    async def test_handle_webhook_worker_mention_returns_accepted(self, client, app):
        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=1,
            author_username="alice",
            body="@claude-worker please fix this",
        )
        app["platforms"]["github"].verify_webhook.return_value = True
        app["platforms"]["github"].parse_event.return_value = event

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new=AsyncMock(return_value=True),
        ):
            response = await client.post("/webhooks/github", data=b"{}")

        data = await response.json()

        assert data["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_handle_webhook_internal_error_returns_500(self, client, app):
        app["platforms"]["github"].verify_webhook.side_effect = RuntimeError("crash")

        response = await client.post("/webhooks/github", data=b"{}")

        assert response.status == 500


class TestAutoTriggerJob:
    @pytest.mark.asyncio
    async def test_auto_trigger_lifecycle_event_returns_accepted(self, client, app):
        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_OPENED,
        )
        app["config"].reviewer_triggers = frozenset([EventType.PR_OPENED])
        app["platforms"]["github"].verify_webhook.return_value = True
        app["platforms"]["github"].parse_event.return_value = event

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new=AsyncMock(return_value=True),
        ):
            response = await client.post("/webhooks/github", data=b"{}")

        data = await response.json()

        assert data["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_auto_trigger_no_reviewer_returns_ignored(self, client, app):
        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_OPENED,
        )
        app["config"].reviewer_triggers = frozenset([EventType.PR_OPENED])
        app["config"].reviewer = None
        app["platforms"]["github"].verify_webhook.return_value = True
        app["platforms"]["github"].parse_event.return_value = event

        response = await client.post("/webhooks/github", data=b"{}")
        data = await response.json()

        assert data["status"] == "ignored"


class TestTitleTagFilter:
    def _lifecycle(self, title=""):
        return LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_OPENED,
            pr_title=title,
            pr_author="alice",
        )

    def _comment(self, title=""):
        return CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            pr_title=title,
            comment_id=100,
            author_username="alice",
            body="@claude-reviewer review",
        )

    def test_both_empty_accepts(self):
        config = _make_config()
        event = self._lifecycle("any title")

        assert should_process_event(event=event, config=config) is True

    def test_exclude_tag_in_title_filters(self):
        config = _make_config(pr_title_exclude_tags=["skip"])
        event = self._lifecycle("fix: some change [skip]")

        assert should_process_event(event=event, config=config) is False

    def test_include_tag_in_title_accepts(self):
        config = _make_config(pr_title_include_tags=["nominalbot"])
        event = self._lifecycle("test: webhook [nominalbot]")

        assert should_process_event(event=event, config=config) is True

    def test_include_tags_set_but_no_match_filters(self):
        config = _make_config(pr_title_include_tags=["nominalbot"])
        event = self._lifecycle("test: unrelated change")

        assert should_process_event(event=event, config=config) is False

    def test_exclude_takes_priority_over_include(self):
        config = _make_config(
            pr_title_include_tags=["nominalbot"],
            pr_title_exclude_tags=["skip"],
        )
        event = self._lifecycle("test: [nominalbot] [skip]")

        assert should_process_event(event=event, config=config) is False

    def test_case_insensitive_matching(self):
        config = _make_config(pr_title_include_tags=["nominalbot"])
        event = self._lifecycle("test: [NominalBot] feature")

        assert should_process_event(event=event, config=config) is True

    def test_comment_event_with_pr_title_filtered(self):
        config = _make_config(pr_title_include_tags=["nominalbot"])
        event = self._comment("test: unrelated change")

        assert should_process_event(event=event, config=config) is False

    def test_comment_event_with_pr_title_accepted(self):
        config = _make_config(pr_title_include_tags=["nominalbot"])
        event = self._comment("test: [nominalbot] feature")

        assert should_process_event(event=event, config=config) is True

    def test_multiple_include_tags_any_match(self):
        config = _make_config(pr_title_include_tags=["alpha", "beta"])
        event = self._lifecycle("test: [beta] feature")

        assert should_process_event(event=event, config=config) is True

    def test_multiple_exclude_tags_any_match(self):
        config = _make_config(pr_title_exclude_tags=["skip", "ignore"])
        event = self._lifecycle("test: [ignore] feature")

        assert should_process_event(event=event, config=config) is False

    def test_exclude_only_no_match_accepts(self):
        config = _make_config(pr_title_exclude_tags=["skip"])
        event = self._lifecycle("test: normal feature")

        assert should_process_event(event=event, config=config) is True


class TestAllowedReposFilter:
    @pytest.mark.asyncio
    async def test_allowed_repos_empty_accepts_all(self, aiohttp_client):
        config = _make_config(allowed_repos=[])
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="any-owner/any-repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix this",
        )
        github_platform.parse_event.return_value = comment

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.post("/webhooks/github", data=b"{}")

        data = await response.json()

        assert data["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_allowed_repos_filters_unlisted_repo(self, aiohttp_client):
        config = _make_config(allowed_repos=["owner/allowed-repo"])
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/other-repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix this",
        )
        github_platform.parse_event.return_value = comment

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        response = await client.post("/webhooks/github", data=b"{}")
        data = await response.json()

        assert data["status"] == "filtered"

    @pytest.mark.asyncio
    async def test_allowed_repos_accepts_listed_repo(self, aiohttp_client):
        config = _make_config(allowed_repos=["owner/allowed-repo"])
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/allowed-repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=100,
            author_username="alice",
            body="@claude-worker fix this",
        )
        github_platform.parse_event.return_value = comment

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        with patch(
            "nominal_code.commands.webhook.server.acknowledge_event",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.post("/webhooks/github", data=b"{}")

        data = await response.json()

        assert data["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_allowed_repos_filters_lifecycle_event(self, aiohttp_client):
        config = _make_config(
            allowed_repos=["owner/allowed-repo"],
            reviewer_triggers=[EventType.PR_OPENED],
        )
        github_platform = _make_github_platform()
        platforms = {"github": github_platform}
        runner = _make_runner(config=config, platforms=platforms)

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/other-repo",
            pr_number=1,
            pr_branch="feature",
            event_type=EventType.PR_OPENED,
            pr_title="New feature",
            pr_author="alice",
        )
        github_platform.parse_event.return_value = event

        app = create_app(
            config=config,
            platforms=platforms,
            runner=runner,
        )
        client = await aiohttp_client(app)

        response = await client.post("/webhooks/github", data=b"{}")
        data = await response.json()

        assert data["status"] == "filtered"
