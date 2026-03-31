import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.config import Config, load_config
from nominal_code.models import EventType, ProviderName
from nominal_code.platforms.base import PlatformName, PullRequestEvent
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


def _build_ci_config() -> Config:
    return load_config(default_provider=ProviderName.ANTHROPIC)


async def _run_ci_review(
    platform: GitHubPlatform,
    event: PullRequestEvent,
    config: Config,
    canned_result: AgentResult,
) -> int:
    with patch(
        "nominal_code.review.handler.invoke_agent",
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
        from nominal_code.platforms.base import CommentReply

        await platform.post_reply(
            event=event,
            reply=CommentReply(body=result.effective_summary),
        )

    return 0


@pytest.mark.asyncio
async def test_ci_review_posts_findings_to_pr(
    github_token: str,
    buggy_pr: PrInfo,
) -> None:
    platform = _build_platform(github_token)
    event = _build_event(buggy_pr)
    config = _build_ci_config()

    exit_code = await _run_ci_review(
        platform=platform,
        event=event,
        config=config,
        canned_result=BUGGY_AGENT_RESULT,
    )

    assert exit_code == 0

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert len(reviews) >= 1

    latest_review = reviews[-1]
    assert "Found issues" in latest_review["body"]

    comments = await fetch_pr_review_comments(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=buggy_pr.number,
    )
    assert len(comments) >= 1

    comment_bodies = [comment["body"] for comment in comments]
    assert any("Unused import" in body for body in comment_bodies)


@pytest.mark.asyncio
async def test_ci_review_no_findings_posts_comment(
    github_token: str,
    clean_pr: PrInfo,
) -> None:
    platform = _build_platform(github_token)
    event = _build_event(clean_pr)
    config = _build_ci_config()

    exit_code = await _run_ci_review(
        platform=platform,
        event=event,
        config=config,
        canned_result=CLEAN_AGENT_RESULT,
    )

    assert exit_code == 0

    reviews = await fetch_pr_reviews(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=clean_pr.number,
    )
    review_bodies = [review["body"] for review in reviews if review["body"]]
    assert not review_bodies

    comments = await fetch_pr_comments(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        pr_number=clean_pr.number,
    )
    assert len(comments) >= 1

    comment_bodies = [comment["body"] for comment in comments]
    assert any("No issues found" in body for body in comment_bodies)
