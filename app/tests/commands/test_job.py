# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.commands.job import run_job_main
from nominal_code.jobs.payload import JobPayload
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
        bot_type="reviewer",
    )


class TestRunJobMain:
    @pytest.mark.asyncio
    async def test_missing_payload_returns_1(self, monkeypatch):
        monkeypatch.delenv("REVIEW_JOB_PAYLOAD", raising=False)

        result = await run_job_main()

        assert result == 1

    @pytest.mark.asyncio
    async def test_invalid_json_returns_1(self, monkeypatch):
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", "not valid json")

        result = await run_job_main()

        assert result == 1

    @pytest.mark.asyncio
    async def test_successful_reviewer_job(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        mock_review_result = MagicMock()
        mock_review_result.agent_review = MagicMock()
        mock_review_result.valid_findings = []
        mock_review_result.effective_summary = "LGTM"
        mock_review_result.raw_output = '{"summary": "LGTM", "comments": []}'
        mock_review_result.cost = None

        mock_platform = MagicMock()
        mock_platform.ensure_auth = AsyncMock()
        mock_platform.build_reviewer_clone_url = MagicMock(
            return_value="https://token@github.com/owner/repo.git",
        )

        with (
            patch(
                "nominal_code.commands.job._build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.job.review",
                new_callable=AsyncMock,
                return_value=mock_review_result,
            ),
            patch(
                "nominal_code.commands.job.post_review_result",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            result = await run_job_main()

        assert result == 0
        mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_exception_returns_1(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        mock_platform = MagicMock()
        mock_platform.ensure_auth = AsyncMock()
        mock_platform.build_reviewer_clone_url = MagicMock(
            return_value="https://token@github.com/owner/repo.git",
        )

        with (
            patch(
                "nominal_code.commands.job._build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.job.review",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Agent failed"),
            ),
        ):
            result = await run_job_main()

        assert result == 1


class TestPublishCompletion:
    @pytest.mark.asyncio
    async def test_publishes_succeeded_on_success(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("K8S_JOB_NAME", "test-job-123")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        mock_review_result = MagicMock()
        mock_review_result.agent_review = MagicMock()
        mock_review_result.valid_findings = []
        mock_review_result.effective_summary = "LGTM"
        mock_review_result.raw_output = '{"summary": "LGTM", "comments": []}'
        mock_review_result.cost = None

        mock_platform = MagicMock()
        mock_platform.ensure_auth = AsyncMock()
        mock_platform.build_reviewer_clone_url = MagicMock(
            return_value="https://token@github.com/owner/repo.git",
        )

        with (
            patch(
                "nominal_code.commands.job._build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.job.review",
                new_callable=AsyncMock,
                return_value=mock_review_result,
            ),
            patch(
                "nominal_code.commands.job.post_review_result",
                new_callable=AsyncMock,
            ),
            patch(
                "nominal_code.commands.job.publish_job_completion",
            ) as mock_publish,
        ):
            result = await run_job_main()

        assert result == 0
        mock_publish.assert_called_once_with(
            redis_url="redis://localhost:6379",
            job_name="test-job-123",
            status="succeeded",
        )

    @pytest.mark.asyncio
    async def test_publishes_failed_on_error(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.setenv("K8S_JOB_NAME", "test-job-123")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")

        mock_platform = MagicMock()
        mock_platform.ensure_auth = AsyncMock()
        mock_platform.build_reviewer_clone_url = MagicMock(
            return_value="https://token@github.com/owner/repo.git",
        )

        with (
            patch(
                "nominal_code.commands.job._build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.job.review",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Agent failed"),
            ),
            patch(
                "nominal_code.commands.job.publish_job_completion",
            ) as mock_publish,
        ):
            result = await run_job_main()

        assert result == 1
        mock_publish.assert_called_once_with(
            redis_url="redis://localhost:6379",
            job_name="test-job-123",
            status="failed",
        )

    @pytest.mark.asyncio
    async def test_skips_publish_without_env_vars(self, monkeypatch):
        job = _make_reviewer_job()
        monkeypatch.setenv("REVIEW_JOB_PAYLOAD", job.serialize())
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        monkeypatch.delenv("K8S_JOB_NAME", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)

        mock_review_result = MagicMock()
        mock_review_result.agent_review = MagicMock()
        mock_review_result.valid_findings = []
        mock_review_result.effective_summary = "LGTM"
        mock_review_result.raw_output = '{"summary": "LGTM", "comments": []}'
        mock_review_result.cost = None

        mock_platform = MagicMock()
        mock_platform.ensure_auth = AsyncMock()
        mock_platform.build_reviewer_clone_url = MagicMock(
            return_value="https://token@github.com/owner/repo.git",
        )

        with (
            patch(
                "nominal_code.commands.job._build_platform",
                return_value=mock_platform,
            ),
            patch(
                "nominal_code.commands.job.review",
                new_callable=AsyncMock,
                return_value=mock_review_result,
            ),
            patch(
                "nominal_code.commands.job.post_review_result",
                new_callable=AsyncMock,
            ),
            patch(
                "nominal_code.commands.job.publish_job_completion",
            ) as mock_publish,
        ):
            result = await run_job_main()

        assert result == 0
        mock_publish.assert_not_called()
