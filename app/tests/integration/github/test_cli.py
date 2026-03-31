import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.config import load_config
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentReply, PlatformName, PullRequestEvent
from nominal_code.platforms.github import GitHubPlatform
from nominal_code.platforms.github.auth import GitHubPatAuth
from nominal_code.review.handler import review
from tests.integration.conftest import PrInfo
from tests.integration.github.api import (
    fetch_pr_comments,
    fetch_pr_review_comments,
    fetch_pr_reviews,
)
from tests.integration.helpers.fixtures import (
    BUGGY_AGENT_RESULT,
    CLEAN_AGENT_RESULT,
    GITHUB_TEST_REPO,
)

pytestmark = [pytest.mark.integration]


def _build_platform(token: str) -> GitHubPlatform:
    return GitHubPlatform(auth=GitHubPatAuth(token=token))


def _build_event(pr_info: PrInfo) -> PullRequestEvent:
    return PullRequestEvent(
        platform=PlatformName.GITHUB,
        repo_full_name=pr_info.repo,
        pr_number=pr_info.number,
        pr_branch=pr_info.head_branch,
        clone_url="",
        event_type=EventType.PR_OPENED,
    )


async def _run_review(
    platform: GitHubPlatform,
    event: PullRequestEvent,
    canned_result: AgentResult,
    dry_run: bool = False,
) -> None:
    config = load_config()

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
    github_token: str,
    buggy_pr: PrInfo,
) -> None:
    platform = _build_platform(github_token)
    event = _build_event(buggy_pr)

    await _run_review(
        platform=platform,
        event=event,
        canned_result=BUGGY_AGENT_RESULT,
        dry_run=True,
    )

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    review_with_body = [review for review in reviews if review.get("body")]
    assert not review_with_body

    comments = await fetch_pr_comments(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert not comments


@pytest.mark.asyncio
async def test_cli_review_posts_review(
    github_token: str,
    buggy_pr: PrInfo,
) -> None:
    platform = _build_platform(github_token)
    event = _build_event(buggy_pr)

    await _run_review(
        platform=platform,
        event=event,
        canned_result=BUGGY_AGENT_RESULT,
        dry_run=False,
    )

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert len(reviews) >= 1

    latest_review = reviews[-1]
    assert "Found issues" in latest_review["body"]

    review_comments = await fetch_pr_review_comments(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert len(review_comments) >= 1


@pytest.mark.asyncio
async def test_cli_review_no_findings_posts_comment(
    github_token: str,
    clean_pr: PrInfo,
) -> None:
    platform = _build_platform(github_token)
    event = _build_event(clean_pr)

    await _run_review(
        platform=platform,
        event=event,
        canned_result=CLEAN_AGENT_RESULT,
        dry_run=False,
    )

    comments = await fetch_pr_comments(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=clean_pr.number,
    )
    assert len(comments) >= 1

    comment_bodies = [comment["body"] for comment in comments]
    assert any("No issues found" in body for body in comment_bodies)
