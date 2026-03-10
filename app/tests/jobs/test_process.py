# type: ignore
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.config import CliAgentConfig, ReviewerConfig, WorkerConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.process import ProcessRunner
from nominal_code.jobs.queue import AsyncioJobQueue
from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PlatformName,
    ReviewerPlatform,
)


def _make_config():
    config = MagicMock()
    config.worker = WorkerConfig(
        bot_username="claude-worker",
        system_prompt="Be concise.",
    )
    config.reviewer = ReviewerConfig(
        bot_username="claude-reviewer",
        system_prompt="Review code.",
    )
    config.allowed_users = frozenset(["alice"])
    config.workspace_base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()

    return config


def _make_platform():
    platform = MagicMock(spec=ReviewerPlatform)
    platform.ensure_auth = AsyncMock()
    platform.build_clone_url = MagicMock(
        return_value="https://token@github.com/owner/repo.git",
    )
    platform.build_reviewer_clone_url = MagicMock(
        return_value="https://ro-token@github.com/owner/repo.git",
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
        file_path="src/main.py",
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


def _make_lifecycle_job():
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


class TestProcessRunner:
    @pytest.mark.asyncio
    async def test_worker_job_calls_review_and_fix(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = AsyncioJobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(
            config=config,
            platforms=platforms,
            conversation_store=conversation_store,
            queue=job_queue,
        )

        with patch(
            "nominal_code.jobs.process.review_and_fix",
            new_callable=AsyncMock,
        ) as mock_handler:
            await runner.enqueue(_make_worker_job())
            await asyncio.sleep(0.05)

            mock_handler.assert_called_once()
            call_kwargs = mock_handler.call_args.kwargs
            assert call_kwargs["event"].clone_url == (
                "https://token@github.com/owner/repo.git"
            )

    @pytest.mark.asyncio
    async def test_reviewer_job_calls_review_and_post(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = AsyncioJobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(
            config=config,
            platforms=platforms,
            conversation_store=conversation_store,
            queue=job_queue,
        )

        with patch(
            "nominal_code.jobs.process.review_and_post",
            new_callable=AsyncMock,
        ) as mock_handler:
            await runner.enqueue(_make_reviewer_job())
            await asyncio.sleep(0.05)

            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifecycle_job_dispatches_reviewer(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = AsyncioJobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(
            config=config,
            platforms=platforms,
            conversation_store=conversation_store,
            queue=job_queue,
        )

        with patch(
            "nominal_code.jobs.process.review_and_post",
            new_callable=AsyncMock,
        ) as mock_handler:
            await runner.enqueue(_make_lifecycle_job())
            await asyncio.sleep(0.05)

            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensures_auth_before_dispatch(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = AsyncioJobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(
            config=config,
            platforms=platforms,
            conversation_store=conversation_store,
            queue=job_queue,
        )

        with patch(
            "nominal_code.jobs.process.review_and_fix",
            new_callable=AsyncMock,
        ):
            await runner.enqueue(_make_worker_job())
            await asyncio.sleep(0.05)

        platform.ensure_auth.assert_called_once()
