# type: ignore
from unittest.mock import AsyncMock, MagicMock

import pytest

from nominal_code.commands.webhook.helpers import acknowledge_event, extract_mention
from nominal_code.config.policies import FilteringPolicy
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, LifecycleEvent, PlatformName


def _make_filtering(allowed_users=None):
    return FilteringPolicy(
        allowed_users=frozenset(allowed_users or ["alice"]),
    )


def _make_comment(
    author="alice",
    platform=PlatformName.GITHUB,
    repo="owner/repo",
    pr_number=42,
    branch="feature",
    body="@claude-reviewer review this",
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
    platform.build_clone_url = MagicMock(
        return_value="https://ro-token@github.com/owner/repo.git",
    )
    platform.authenticate = AsyncMock()
    platform.post_pr_reaction = AsyncMock()

    return platform


class TestAcknowledgeEvent:
    @pytest.mark.asyncio
    async def test_unauthorized_user_returns_false(self):
        filtering = _make_filtering(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="eve")

        result = await acknowledge_event(
            event=comment,
            filtering=filtering,
            platform=platform,
        )

        assert result is False
        platform.post_reaction.assert_not_called()
        platform.post_pr_reaction.assert_not_called()
        platform.authenticate.assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_user_posts_reactions_and_returns_true(self):
        filtering = _make_filtering(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")

        result = await acknowledge_event(
            event=comment,
            filtering=filtering,
            platform=platform,
        )

        assert result is True
        platform.post_reaction.assert_called_once()
        platform.post_pr_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifecycle_event_skips_comment_reaction_but_reacts_on_pr(self):
        filtering = _make_filtering(allowed_users=["alice"])
        platform = _make_platform()

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            event_type=EventType.PR_OPENED,
            pr_title="Add feature",
            pr_author="eve",
        )

        result = await acknowledge_event(
            event=event,
            filtering=filtering,
            platform=platform,
        )

        assert result is True
        platform.post_reaction.assert_not_called()
        platform.post_pr_reaction.assert_called_once()
        platform.authenticate.assert_called_once()


class TestExtractMention:
    def test_extract_mention_basic(self):
        result = extract_mention(
            text="@claude-bot fix the bug", bot_username="claude-bot"
        )

        assert result == "fix the bug"

    def test_extract_mention_case_insensitive(self):
        result = extract_mention(
            text="@Claude-Bot fix the bug", bot_username="claude-bot"
        )

        assert result == "fix the bug"

    def test_extract_mention_no_mention_returns_none(self):
        result = extract_mention(
            text="just a regular comment", bot_username="claude-bot"
        )

        assert result is None

    def test_extract_mention_mention_only_returns_none(self):
        result = extract_mention(text="@claude-bot", bot_username="claude-bot")

        assert result is None

    def test_extract_mention_whitespace_only_after_mention_returns_none(self):
        result = extract_mention(text="@claude-bot   ", bot_username="claude-bot")

        assert result is None

    def test_extract_mention_in_middle_of_text(self):
        result = extract_mention(
            text="hey @claude-bot please review this", bot_username="claude-bot"
        )

        assert result == "please review this"

    def test_extract_mention_with_newlines(self):
        result = extract_mention(
            text="@claude-bot\nplease fix\nthis issue", bot_username="claude-bot"
        )

        assert result == "please fix\nthis issue"

    def test_extract_mention_does_not_match_partial_username(self):
        result = extract_mention(
            text="@claude-botv2 fix this", bot_username="claude-bot"
        )

        assert result is None

    def test_extract_mention_special_chars_in_username(self):
        result = extract_mention(text="@my.bot fix this", bot_username="my.bot")

        assert result == "fix this"
