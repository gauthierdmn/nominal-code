import pytest

from tests.integration.conftest import BranchInfo, PrInfo
from tests.integration.gitlab.api import (
    fetch_mr_notes,
    wait_for_pipeline,
)
from tests.integration.helpers.fixtures import GITLAB_TEST_REPO

pytestmark = [pytest.mark.integration_pipeline]


@pytest.mark.asyncio
async def test_pipeline_posts_review_on_buggy_mr(
    gitlab_token: str,
    gitlab_pipeline_branch: BranchInfo,
    gitlab_pipeline_mr: PrInfo,
) -> None:
    pipeline = await wait_for_pipeline(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        branch=gitlab_pipeline_branch.branch_name,
        timeout=540.0,
    )

    assert pipeline["status"] == "success", (
        f"Pipeline failed: {pipeline.get('web_url', 'unknown')}"
    )

    notes = await fetch_mr_notes(
        gitlab_token,
        GITLAB_TEST_REPO,
        gitlab_pipeline_mr.number,
    )

    user_notes = [
        note for note in notes if not note.get("system", False) and note.get("body")
    ]

    assert len(user_notes) >= 1, "Expected at least one review note"
