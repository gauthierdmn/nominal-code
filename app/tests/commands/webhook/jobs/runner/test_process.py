# type: ignore
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.commands.webhook.jobs.dispatch import JobResult
from nominal_code.commands.webhook.jobs.payload import JobPayload
from nominal_code.commands.webhook.jobs.queue.asyncio import AsyncioJobQueue
from nominal_code.commands.webhook.jobs.runner.process import ProcessRunner
from nominal_code.config import CliAgentConfig, ReviewerConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    Platform,
    PlatformName,
)


def _make_config():
    config = MagicMock()
    config.reviewer = ReviewerConfig(
        bot_username="claude-reviewer",
        system_prompt="Review code.",
    )
    config.allowed_users = frozenset(["alice"])
    config.workspace = MagicMock()
    config.workspace.base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()

    return config


def _make_platform():
    platform = MagicMock(spec=Platform)
    platform.authenticate = AsyncMock()
    platform.build_clone_url = MagicMock(
        return_value="https://token@github.com/owner/repo.git",
    )

    return platform


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

    return JobPayload(event=event)


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

    return JobPayload(event=event)


class TestProcessRunner:
    @pytest.mark.asyncio
    async def test_reviewer_job_dispatches_via_execute_job(self):
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
            "nominal_code.commands.webhook.jobs.runner.process.execute_job",
            new_callable=AsyncMock,
            return_value=JobResult(
                review_result=MagicMock(),
            ),
        ) as mock_execute:
            await runner.enqueue(_make_reviewer_job())
            await asyncio.sleep(0.05)

            mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifecycle_job_dispatches_via_execute_job(self):
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
            "nominal_code.commands.webhook.jobs.runner.process.execute_job",
            new_callable=AsyncMock,
            return_value=JobResult(
                review_result=MagicMock(),
            ),
        ) as mock_execute:
            await runner.enqueue(_make_lifecycle_job())
            await asyncio.sleep(0.05)

            mock_execute.assert_called_once()
