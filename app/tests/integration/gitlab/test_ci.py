import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.config import Config
from nominal_code.handlers.review import review
from nominal_code.llm.registry import PROVIDERS
from nominal_code.models import EventType, ProviderName
from nominal_code.platforms.base import CommentReply, PlatformName, PullRequestEvent
from nominal_code.platforms.gitlab import GitLabPlatform
from tests.integration.conftest import PrInfo
from tests.integration.gitlab.api import (
    fetch_mr_discussions,
    fetch_mr_notes,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    CLEAN_AGENT_RESULT,
    GITLAB_TEST_REPO,
)

pytestmark = [pytest.mark.integration]


def _build_platform(token: str) -> GitLabPlatform:
    return GitLabPlatform(token=token)


def _build_event(pr_info: PrInfo) -> PullRequestEvent:
    return PullRequestEvent(
        platform=PlatformName.GITLAB,
        repo_full_name=pr_info.repo,
        pr_number=pr_info.number,
        pr_branch=pr_info.head_branch,
        clone_url="",
        event_type=EventType.PR_OPENED,
    )


def _build_ci_config() -> Config:
    return Config.for_ci(provider=PROVIDERS[ProviderName.ANTHROPIC])


async def _run_ci_review(
    platform: GitLabPlatform,
    event: PullRequestEvent,
    config: Config,
    canned_result: AgentResult,
) -> int:
    with patch(
        "nominal_code.agent.cli.session.run_agent",
        new_callable=AsyncMock,
        return_value=canned_result,
    ):
        result = await review(
            event=event,
            prompt="",
            config=config,
            platform=platform,
            workspace_path=tempfile.gettempdir(),
        )

    if result.valid_findings:
        await platform.submit_review(
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            findings=result.valid_findings,
            summary=result.effective_summary,
            event=event,
        )
    else:
        await platform.post_reply(
            event=event,
            reply=CommentReply(body=result.effective_summary),
        )

    return 0


@pytest.mark.asyncio
async def test_ci_review_posts_findings_to_mr(
    gitlab_token: str,
    buggy_mr: PrInfo,
) -> None:
    platform = _build_platform(gitlab_token)
    event = _build_event(buggy_mr)
    config = _build_ci_config()

    exit_code = await _run_ci_review(platform, event, config, BUGGY_AGENT_RESULT)

    assert exit_code == 0

    notes = await fetch_mr_notes(gitlab_token, GITLAB_TEST_REPO, buggy_mr.number)
    note_bodies = [note["body"] for note in notes]
    assert any("Found issues" in body for body in note_bodies)

    discussions = await fetch_mr_discussions(
        gitlab_token,
        GITLAB_TEST_REPO,
        buggy_mr.number,
    )
    inline_discussions = [
        disc
        for disc in discussions
        if disc["notes"] and disc["notes"][0].get("position")
    ]
    assert len(inline_discussions) >= 1


@pytest.mark.asyncio
async def test_ci_review_no_findings_posts_comment(
    gitlab_token: str,
    clean_mr: PrInfo,
) -> None:
    platform = _build_platform(gitlab_token)
    event = _build_event(clean_mr)
    config = _build_ci_config()

    exit_code = await _run_ci_review(platform, event, config, CLEAN_AGENT_RESULT)

    assert exit_code == 0

    notes = await fetch_mr_notes(gitlab_token, GITLAB_TEST_REPO, clean_mr.number)
    note_bodies = [note["body"] for note in notes]
    assert any("No issues found" in body for body in note_bodies)

    discussions = await fetch_mr_discussions(
        gitlab_token,
        GITLAB_TEST_REPO,
        clean_mr.number,
    )
    inline_discussions = [
        disc
        for disc in discussions
        if disc["notes"] and disc["notes"][0].get("position")
    ]
    assert not inline_discussions
