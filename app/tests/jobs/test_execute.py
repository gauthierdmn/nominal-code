# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.jobs.execute import execute_job
from nominal_code.jobs.payload import JobPayload
from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PlatformName,
    ReviewerPlatform,
)


def _make_config():
    config = MagicMock()
    config.workspace_base_dir = "/tmp/workspaces"

    return config


def _make_reviewer_platform():
    platform = MagicMock(spec=ReviewerPlatform)
    platform.build_reviewer_clone_url = MagicMock(
        return_value="https://ro-token@github.com/owner/repo.git",
    )
    platform.build_clone_url = MagicMock(
        return_value="https://token@github.com/owner/repo.git",
    )

    return platform


def _make_worker_job():
    event = CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Fix bug",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username="alice",
        body="@bot fix this",
        mention_prompt="fix this",
    )

    return JobPayload(event=event, bot_type="worker")


def _make_reviewer_job():
    event = CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Add feature",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=200,
        author_username="alice",
        body="@bot review this",
        mention_prompt="review this",
    )

    return JobPayload(event=event, bot_type="reviewer")


def _make_lifecycle_reviewer_job():
    event = LifecycleEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="New feature",
        event_type=EventType.PR_OPENED,
        pr_author="alice",
    )

    return JobPayload(event=event, bot_type="reviewer")


class TestExecuteJob:
    @pytest.mark.asyncio
    async def test_worker_job_returns_none(self):
        config = _make_config()
        platform = _make_reviewer_platform()

        with (
            patch(
                "nominal_code.jobs.execute.resolve_branch",
                new_callable=AsyncMock,
                return_value=_make_worker_job().event,
            ),
            patch(
                "nominal_code.jobs.execute.review_and_fix",
                new_callable=AsyncMock,
            ),
        ):
            result = await execute_job(
                job=_make_worker_job(),
                config=config,
                platform=platform,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_reviewer_job_returns_review_result(self):
        config = _make_config()
        platform = _make_reviewer_platform()
        mock_result = MagicMock()

        with (
            patch(
                "nominal_code.jobs.execute.resolve_branch",
                new_callable=AsyncMock,
                return_value=_make_reviewer_job().event,
            ),
            patch(
                "nominal_code.jobs.execute.review",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await execute_job(
                job=_make_reviewer_job(),
                config=config,
                platform=platform,
            )

        assert result is mock_result

    @pytest.mark.asyncio
    async def test_reviewer_job_with_non_reviewer_platform_raises(self):
        config = _make_config()
        platform = MagicMock()

        with pytest.raises(RuntimeError, match="does not support reviewer"):
            await execute_job(
                job=_make_reviewer_job(),
                config=config,
                platform=platform,
            )

    @pytest.mark.asyncio
    async def test_worker_job_with_lifecycle_event_raises(self):
        config = _make_config()
        platform = _make_reviewer_platform()

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="feature",
            pr_title="New feature",
            event_type=EventType.PR_OPENED,
            pr_author="alice",
        )
        job = JobPayload(event=event, bot_type="worker")

        with pytest.raises(RuntimeError, match="Worker job requires a comment event"):
            await execute_job(
                job=job,
                config=config,
                platform=platform,
            )

    @pytest.mark.asyncio
    async def test_reviewer_branch_resolution_failure_raises(self):
        config = _make_config()
        platform = _make_reviewer_platform()

        with patch(
            "nominal_code.jobs.execute.resolve_branch",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(RuntimeError, match="Cannot resolve branch"):
                await execute_job(
                    job=_make_reviewer_job(),
                    config=config,
                    platform=platform,
                )
