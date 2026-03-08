# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.cli.job import JobQueue
from nominal_code.agent.memory import ConversationStore
from nominal_code.config import CliAgentConfig, ReviewerConfig, WorkerConfig
from nominal_code.jobs.in_process import InProcessRunner
from nominal_code.jobs.payload import ReviewJob
from nominal_code.platforms.base import ReviewerPlatform


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
    return ReviewJob(
        platform="github",
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Fix bug",
        event_type="issue_comment",
        is_comment_event=True,
        author_username="alice",
        comment_body="@bot fix this",
        comment_id=100,
        diff_hunk="",
        file_path="src/main.py",
        discussion_id="",
        prompt="fix this",
        pr_author="",
        bot_type="worker",
    )


def _make_reviewer_job():
    return ReviewJob(
        platform="github",
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Add feature",
        event_type="issue_comment",
        is_comment_event=True,
        author_username="alice",
        comment_body="@bot review this",
        comment_id=200,
        diff_hunk="",
        file_path="",
        discussion_id="",
        prompt="review this",
        pr_author="",
        bot_type="reviewer",
    )


def _make_lifecycle_job():
    return ReviewJob(
        platform="github",
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="New feature",
        event_type="pr_opened",
        is_comment_event=False,
        author_username="",
        comment_body="",
        comment_id=0,
        diff_hunk="",
        file_path="",
        discussion_id="",
        prompt="",
        pr_author="alice",
        bot_type="reviewer",
    )


class TestInProcessRunner:
    @pytest.mark.asyncio
    async def test_worker_job_enqueues_review_and_fix(self):
        config = _make_config()
        platform = _make_platform()
        platforms = {"github": platform}
        job_queue = JobQueue()
        conversation_store = ConversationStore()

        runner = InProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.in_process.InProcessRunner._run_comment_job",
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
        conversation_store = ConversationStore()

        runner = InProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.in_process.InProcessRunner._run_comment_job",
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
        conversation_store = ConversationStore()

        runner = InProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.in_process.InProcessRunner._run_lifecycle_job",
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
        conversation_store = ConversationStore()

        runner = InProcessRunner(config, platforms, conversation_store, job_queue)

        with patch(
            "nominal_code.jobs.in_process.InProcessRunner._run_comment_job",
            new_callable=AsyncMock,
        ):
            await runner.run(_make_worker_job())

        platform.ensure_auth.assert_called_once()
