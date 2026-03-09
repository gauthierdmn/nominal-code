# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.cli.queue import JobQueue
from nominal_code.config import CliAgentConfig, ReviewerConfig, WorkerConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.process import ProcessRunner
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
    )

    return JobPayload(event=event, prompt="fix this", bot_type="worker")


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
    )

    return JobPayload(event=event, prompt="review this", bot_type="reviewer")


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

    return JobPayload(event=event, prompt="", bot_type="reviewer")


class TestProcessRunner:
    @pytest.mark.asyncio
    async def test_worker_job_enqueues_review_and_fix(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = JobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.process.ProcessRunner._run_worker_job",
            new_callable=AsyncMock,
        ) as mock_run:
            await runner.run(_make_worker_job())

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args.args[0].bot_type == "worker"

    @pytest.mark.asyncio
    async def test_reviewer_job_enqueues_review_and_post(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = JobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.process.ProcessRunner._run_reviewer_job",
            new_callable=AsyncMock,
        ) as mock_run:
            await runner.run(_make_reviewer_job())

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args.args[0].bot_type == "reviewer"

    @pytest.mark.asyncio
    async def test_lifecycle_job_dispatches_reviewer(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = JobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.process.ProcessRunner._run_reviewer_job",
            new_callable=AsyncMock,
        ) as mock_run:
            await runner.run(_make_lifecycle_job())

            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensures_auth_before_dispatch(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = JobQueue()
        conversation_store = MemoryConversationStore()

        runner = ProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.process.ProcessRunner._run_worker_job",
            new_callable=AsyncMock,
        ):
            await runner.run(_make_worker_job())

        platform.ensure_auth.assert_called_once()
