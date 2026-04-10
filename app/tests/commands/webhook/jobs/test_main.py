# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.commands.webhook.jobs.dispatch import JobResult
from nominal_code.commands.webhook.jobs.main import run_job_main
from nominal_code.commands.webhook.jobs.payload import JobPayload
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_reviewer_job():
    event = CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Add feature",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username="alice",
        body="@bot review",
        mention_prompt="review this",
    )

    return JobPayload(
        event=event,
    )


def _make_review_result():
    mock_result = MagicMock()
    mock_result.agent_review = MagicMock()
    mock_result.is_error = False
    mock_result.valid_findings = []
    mock_result.rejected_findings = []
    mock_result.raw_output = ""
    mock_result.cost = None
    mock_result.num_turns = 0
    mock_result.messages = ()
    mock_result.input_prompt = ""

    return JobResult(
        review_result=mock_result,
    )


class TestRunJobMain:
    @pytest.mark.asyncio
    async def test_missing_payload_returns_none(self, monkeypatch):
        monkeypatch.delenv("REVIEW_JOB_PAYLOAD", raising=False)

        result = await run_job_main()

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self, monkeypatch):
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", "not valid json")

        result = await run_job_main()

        assert result is None

    @pytest.mark.asyncio
    async def test_successful_reviewer_job(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                return_value=_make_review_result(),
            ) as mock_execute,
        ):
            result = await run_job_main()

        assert result is not None
        assert isinstance(result, JobResult)
        mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_pre_cloned_forwarded_to_execute_job(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                return_value=_make_review_result(),
            ) as mock_execute,
        ):
            await run_job_main(pre_cloned=True)

        _, call_kwargs = mock_execute.call_args
        assert call_kwargs["pre_cloned"] is True

    @pytest.mark.asyncio
    async def test_pre_cloned_defaults_to_false(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                return_value=_make_review_result(),
            ) as mock_execute,
        ):
            await run_job_main()

        _, call_kwargs = mock_execute.call_args
        assert call_kwargs["pre_cloned"] is False

    @pytest.mark.asyncio
    async def test_review_exception_returns_none(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Agent failed"),
            ),
        ):
            result = await run_job_main()

        assert result is None


class TestPublishCompletion:
    @pytest.mark.asyncio
    async def test_publishes_succeeded_on_success(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.build_conversation_store",
                return_value=MagicMock(),
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                return_value=_make_review_result(),
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.publish_job_completion",
            ) as mock_publish,
        ):
            result = await run_job_main()

        assert result is not None
        mock_publish.assert_called_once_with(
            redis_url="redis://localhost:6379",
            channel_key="nc:job:github:owner/repo:42",
            status="succeeded",
        )

    @pytest.mark.asyncio
    async def test_publishes_failed_on_error(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.build_conversation_store",
                return_value=MagicMock(),
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Agent failed"),
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.publish_job_completion",
            ) as mock_publish,
        ):
            result = await run_job_main()

        assert result is None
        mock_publish.assert_called_once_with(
            redis_url="redis://localhost:6379",
            channel_key="nc:job:github:owner/repo:42",
            status="failed",
        )

    @pytest.mark.asyncio
    async def test_skips_publish_without_redis_url(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.delenv("REDIS_URL", raising=False)

        mock_platform = MagicMock()

        with (
            patch(
                "nominal_code.commands.webhook.jobs.main.build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.execute_job",
                new_callable=AsyncMock,
                return_value=_make_review_result(),
            ),
            patch(
                "nominal_code.commands.webhook.jobs.main.publish_job_completion",
            ) as mock_publish,
        ):
            result = await run_job_main()

        assert result is not None
        mock_publish.assert_not_called()
