import asyncio

import pytest

from tests.integration.conftest import BranchInfo, PrInfo
from tests.integration.github.api import (
    fetch_pr_comments,
    fetch_pr_reviews,
    wait_for_workflow_run,
)
from tests.integration.helpers.fixtures import GITHUB_TEST_REPO

pytestmark = [pytest.mark.integration_pipeline]

REVIEW_POLL_INTERVAL = 5.0
REVIEW_POLL_TIMEOUT = 60.0


@pytest.mark.asyncio
async def test_pipeline_posts_review_on_buggy_pr(
    github_token: str,
    github_pipeline_branch: BranchInfo,
    github_pipeline_pr: PrInfo,
) -> None:
    run = await wait_for_workflow_run(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        branch=github_pipeline_branch.branch_name,
        timeout=540.0,
    )

    assert run["conclusion"] == "success", (
        f"Workflow run failed: {run.get('html_url', 'unknown')}"
    )

    found = False
    elapsed = 0.0

    while elapsed < REVIEW_POLL_TIMEOUT:
        reviews = await fetch_pr_reviews(
            github_token,
            GITHUB_TEST_REPO,
            github_pipeline_pr.number,
        )

        if any(review.get("body") for review in reviews):
            found = True

            break

        comments = await fetch_pr_comments(
            github_token,
            GITHUB_TEST_REPO,
            github_pipeline_pr.number,
        )

        if any(comment.get("body") for comment in comments):
            found = True

            break

        await asyncio.sleep(REVIEW_POLL_INTERVAL)
        elapsed += REVIEW_POLL_INTERVAL

    assert found, "Expected at least one review or comment with a body"
