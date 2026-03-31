# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.jobs.dispatch import JobResult, execute_job, extract_prompt
from nominal_code.models import BotType, EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PlatformName,
    ReviewerPlatform,
)


def _make_comment_event(mention_prompt="review this"):
    return CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Add feature",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username="alice",
        body="@bot review",
        mention_prompt=mention_prompt,
    )


def _make_lifecycle_event():
    return LifecycleEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="New feature",
        event_type=EventType.PR_OPENED,
        pr_author="alice",
    )


class TestExtractPrompt:
    def test_worker_comment_event_returns_mention_prompt(self):
        event = _make_comment_event(mention_prompt="fix this bug")

        assert extract_prompt(event, BotType.WORKER) == "fix this bug"

    def test_worker_comment_event_empty_mention_prompt(self):
        event = _make_comment_event(mention_prompt="")

        assert extract_prompt(event, BotType.WORKER) == ""

    def test_worker_lifecycle_event_raises(self):
        event = _make_lifecycle_event()

        with pytest.raises(RuntimeError, match="Worker job requires a comment event"):
            extract_prompt(event, BotType.WORKER)

    def test_reviewer_comment_event_returns_mention_prompt(self):
        event = _make_comment_event(mention_prompt="review this")

        assert extract_prompt(event, BotType.REVIEWER) == "review this"

    def test_reviewer_comment_event_empty_mention_prompt(self):
        event = _make_comment_event(mention_prompt="")

        assert extract_prompt(event, BotType.REVIEWER) == ""

    def test_reviewer_lifecycle_event_returns_empty(self):
        event = _make_lifecycle_event()

        assert extract_prompt(event, BotType.REVIEWER) == ""


class TestExecuteJob:
    @pytest.mark.asyncio
    async def test_reviewer_happy_path(self):
        mock_review_result = MagicMock()
        mock_handler = MagicMock()
        mock_handler.handle_review = AsyncMock(return_value=mock_review_result)
        mock_platform = MagicMock(spec=ReviewerPlatform)
        mock_platform.authenticate = AsyncMock()

        event = _make_comment_event()
        job = MagicMock()
        job.bot_type = "reviewer"
        job.event = event
        job.namespace = "test"

        with patch(
            "nominal_code.jobs.dispatch.prepare_job_event",
            new_callable=AsyncMock,
            return_value=event,
        ):
            result = await execute_job(
                job=job,
                platform=mock_platform,
                handler=mock_handler,
                config=MagicMock(),
                conversation_store=MagicMock(),
            )

        assert isinstance(result, JobResult)
        assert result.bot_type == BotType.REVIEWER
        assert result.review_result is mock_review_result
        mock_handler.handle_review.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_happy_path(self):
        mock_handler = MagicMock()
        mock_handler.handle_worker = AsyncMock()
        mock_platform = MagicMock(spec=ReviewerPlatform)
        mock_platform.authenticate = AsyncMock()

        event = _make_comment_event(mention_prompt="fix this")
        job = MagicMock()
        job.bot_type = "worker"
        job.event = event
        job.namespace = "test"

        with patch(
            "nominal_code.jobs.dispatch.prepare_job_event",
            new_callable=AsyncMock,
            return_value=event,
        ):
            result = await execute_job(
                job=job,
                platform=mock_platform,
                handler=mock_handler,
                config=MagicMock(),
                conversation_store=MagicMock(),
            )

        assert isinstance(result, JobResult)
        assert result.bot_type == BotType.WORKER
        assert result.review_result is None
        mock_handler.handle_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_reviewer_non_reviewer_platform_raises(self):
        mock_handler = MagicMock()
        mock_platform = MagicMock(spec=[])
        mock_platform.authenticate = AsyncMock()

        event = _make_comment_event()
        job = MagicMock()
        job.bot_type = "reviewer"
        job.event = event

        with pytest.raises(RuntimeError, match="does not support reviewer operations"):
            await execute_job(
                job=job,
                platform=mock_platform,
                handler=mock_handler,
                config=MagicMock(),
            )

    @pytest.mark.asyncio
    async def test_authenticates_platform(self):
        mock_handler = MagicMock()
        mock_handler.handle_worker = AsyncMock()
        mock_platform = MagicMock(spec=ReviewerPlatform)
        mock_platform.authenticate = AsyncMock()

        event = _make_comment_event()
        job = MagicMock()
        job.bot_type = "worker"
        job.event = event
        job.namespace = ""

        with patch(
            "nominal_code.jobs.dispatch.prepare_job_event",
            new_callable=AsyncMock,
            return_value=event,
        ):
            await execute_job(
                job=job,
                platform=mock_platform,
                handler=mock_handler,
                config=MagicMock(),
            )

        mock_platform.authenticate.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifecycle_reviewer_job(self):
        mock_review_result = MagicMock()
        mock_handler = MagicMock()
        mock_handler.handle_review = AsyncMock(return_value=mock_review_result)
        mock_platform = MagicMock(spec=ReviewerPlatform)
        mock_platform.authenticate = AsyncMock()

        event = _make_lifecycle_event()
        job = MagicMock()
        job.bot_type = "reviewer"
        job.event = event
        job.namespace = ""

        with patch(
            "nominal_code.jobs.dispatch.prepare_job_event",
            new_callable=AsyncMock,
            return_value=event,
        ):
            result = await execute_job(
                job=job,
                platform=mock_platform,
                handler=mock_handler,
                config=MagicMock(),
            )

        assert result.bot_type == BotType.REVIEWER
        call_kwargs = mock_handler.handle_review.call_args.kwargs
        assert call_kwargs["prompt"] == ""
