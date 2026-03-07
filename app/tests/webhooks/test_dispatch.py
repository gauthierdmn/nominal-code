# type: ignore
from unittest.mock import AsyncMock, MagicMock

import pytest

from nominal_code.agent.cli.session import SessionQueue
from nominal_code.config import CliAgentConfig, ReviewerConfig, WorkerConfig
from nominal_code.models import BotType, EventType
from nominal_code.platforms.base import CommentEvent, LifecycleEvent, PlatformName
from nominal_code.webhooks.dispatch import enqueue_job


def _make_config(allowed_users=None):
    config = MagicMock()
    config.allowed_users = frozenset(allowed_users or ["alice"])
    config.workspace_base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()
    config.coding_guidelines = "Use snake_case."
    config.language_guidelines = {"python": "Python style rules."}
    config.worker = WorkerConfig(
        bot_username="claude-worker",
        system_prompt="Be concise.",
    )
    config.reviewer = ReviewerConfig(
        bot_username="claude-reviewer",
        system_prompt="Review code.",
    )

    return config


def _make_comment(
    author="alice",
    platform=PlatformName.GITHUB,
    repo="owner/repo",
    pr_number=42,
    branch="feature",
    body="@claude-worker fix this",
    diff_hunk="",
    file_path="",
):
    return CommentEvent(
        platform=platform,
        repo_full_name=repo,
        pr_number=pr_number,
        pr_branch=branch,
        clone_url="https://token@github.com/owner/repo.git",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username=author,
        body=body,
        diff_hunk=diff_hunk,
        file_path=file_path,
    )


def _make_platform():
    platform = MagicMock()
    platform.post_reaction = AsyncMock()
    platform.post_reply = AsyncMock()
    platform.fetch_pr_branch = AsyncMock(return_value="")
    platform.fetch_pr_diff = AsyncMock(return_value=[])
    platform.fetch_pr_comments = AsyncMock(return_value=[])
    platform.submit_review = AsyncMock()
    platform.build_reviewer_clone_url = MagicMock(
        return_value="https://ro-token@github.com/owner/repo.git",
    )
    platform.ensure_auth = AsyncMock()
    platform.post_pr_reaction = AsyncMock()

    return platform


class TestEnqueueJob:
    @pytest.mark.asyncio
    async def test_enqueue_job_unauthorized_user(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="eve")
        session_queue = SessionQueue()
        mock_job = AsyncMock()

        await enqueue_job(
            event=comment,
            bot_type=BotType.WORKER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=mock_job,
        )

        platform.post_reaction.assert_not_called()
        platform.post_pr_reaction.assert_not_called()
        platform.ensure_auth.assert_not_called()
        mock_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_job_authorized_user_posts_reaction_and_enqueues(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_queue = SessionQueue()
        mock_job = AsyncMock()

        await enqueue_job(
            event=comment,
            bot_type=BotType.WORKER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=mock_job,
        )

        platform.post_reaction.assert_called_once()
        platform.post_pr_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_job_auto_trigger_skips_comment_reaction_but_reacts_on_pr(
        self,
    ):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        session_queue = SessionQueue()
        mock_job = AsyncMock()

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            event_type=EventType.PR_OPENED,
            pr_title="Add feature",
            pr_author="eve",
        )

        await enqueue_job(
            event=event,
            bot_type=BotType.REVIEWER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=mock_job,
        )

        platform.post_reaction.assert_not_called()
        platform.post_pr_reaction.assert_called_once()
        platform.ensure_auth.assert_called_once()
