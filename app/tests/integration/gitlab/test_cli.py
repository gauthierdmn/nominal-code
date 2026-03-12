import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.config import Config
from nominal_code.handlers.review import review
from nominal_code.models import EventType
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
    from nominal_code.platforms.gitlab.auth import GitLabPatAuth

    return GitLabPlatform(auth=GitLabPatAuth(token=token))


def _build_event(pr_info: PrInfo) -> PullRequestEvent:
    return PullRequestEvent(
        platform=PlatformName.GITLAB,
        repo_full_name=pr_info.repo,
        pr_number=pr_info.number,
        pr_branch=pr_info.head_branch,
        clone_url="",
        event_type=EventType.PR_OPENED,
    )


async def _run_review(
    platform: GitLabPlatform,
    event: PullRequestEvent,
    canned_result: AgentResult,
    dry_run: bool = False,
) -> None:
    config = Config.for_cli()

    with patch(
        "nominal_code.agent.invoke.run_cli_agent",
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

    if dry_run:
        return

    if result.agent_review is None:
        return

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


@pytest.mark.asyncio
async def test_cli_review_dry_run_does_not_post(
    gitlab_token: str,
    buggy_mr: PrInfo,
) -> None:
    platform = _build_platform(gitlab_token)
    event = _build_event(buggy_mr)

    await _run_review(
        platform=platform,
        event=event,
        canned_result=BUGGY_AGENT_RESULT,
        dry_run=True,
    )

    notes = await fetch_mr_notes(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        mr_iid=buggy_mr.number,
    )
    user_notes = [note for note in notes if not note.get("system", False)]
    assert not user_notes


@pytest.mark.asyncio
async def test_cli_review_posts_review(
    gitlab_token: str,
    buggy_mr: PrInfo,
) -> None:
    platform = _build_platform(gitlab_token)
    event = _build_event(buggy_mr)

    await _run_review(
        platform=platform,
        event=event,
        canned_result=BUGGY_AGENT_RESULT,
        dry_run=False,
    )

    notes = await fetch_mr_notes(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        mr_iid=buggy_mr.number,
    )
    note_bodies = [note["body"] for note in notes]
    assert any("Found issues" in body for body in note_bodies)

    discussions = await fetch_mr_discussions(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        mr_iid=buggy_mr.number,
    )
    inline_discussions = [
        disc
        for disc in discussions
        if disc["notes"] and disc["notes"][0].get("position")
    ]
    assert len(inline_discussions) >= 1


@pytest.mark.asyncio
async def test_cli_review_no_findings_posts_comment(
    gitlab_token: str,
    clean_mr: PrInfo,
) -> None:
    platform = _build_platform(gitlab_token)
    event = _build_event(clean_mr)

    await _run_review(
        platform=platform,
        event=event,
        canned_result=CLEAN_AGENT_RESULT,
        dry_run=False,
    )

    notes = await fetch_mr_notes(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        mr_iid=clean_mr.number,
    )
    note_bodies = [note["body"] for note in notes]
    assert any("No issues found" in body for body in note_bodies)
