import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from tests.integration.conftest import (
    BranchInfo,
    PrInfo,
    create_github_branch_with_file,
    unique_branch_name,
)
from tests.integration.github import api as github_api
from tests.integration.helpers.ci_configs import github_actions_workflow_base64
from tests.integration.helpers.fixtures import (
    BUGGY_CALCULATOR_CONTENT_B64,
    BUGGY_CALCULATOR_PATH,
    CLEAN_CALCULATOR_CONTENT_B64,
    CLEAN_CALCULATOR_PATH,
    GITHUB_TEST_REPO,
)

BUGGY_COMMIT_MESSAGE = "test: add buggy calculator"
CLEAN_COMMIT_MESSAGE = "test: add clean calculator"
BUGGY_PR_TITLE = "test: buggy-calculator [{pipeline_id}]"
CLEAN_PR_TITLE = "test: clean-change [{pipeline_id}]"
PIPELINE_PR_TITLE = "test: pipeline review [{pipeline_id}]"
WEBHOOK_PR_TITLE = "test: webhook server [nominalbot] [{pipeline_id}]"
WORKFLOW_COMMIT_MESSAGE = "test: add review workflow"


@pytest.fixture(scope="session")
def github_token() -> str:
    """
    Return the GitHub token from environment.

    Returns:
        str: The GitHub API token.
    """

    token = os.environ.get("TEST_GITHUB_TOKEN", "")

    assert token, "TEST_GITHUB_TOKEN environment variable is required"

    return token


@pytest_asyncio.fixture
async def buggy_pr(
    github_token: str,
    pipeline_id: str,
) -> AsyncGenerator[PrInfo]:
    """
    Create a GitHub PR with buggy calculator content on a unique branch.

    Args:
        github_token (str): GitHub API token.
        pipeline_id (str): Short pipeline identifier for title tagging.

    Yields:
        PrInfo: Information about the created PR.
    """

    branch_name = unique_branch_name("buggy")

    await create_github_branch_with_file(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        branch_name=branch_name,
        file_path=BUGGY_CALCULATOR_PATH,
        content_b64=BUGGY_CALCULATOR_CONTENT_B64,
        commit_message=BUGGY_COMMIT_MESSAGE,
    )

    pr_number = await github_api.create_pr(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        head=branch_name,
        title=BUGGY_PR_TITLE.format(pipeline_id=pipeline_id),
    )

    try:
        yield PrInfo(
            repo=GITHUB_TEST_REPO,
            number=pr_number,
            head_branch=branch_name,
        )
    finally:
        try:
            await github_api.close_pr(github_token, GITHUB_TEST_REPO, pr_number)
        except Exception:
            pass

        try:
            await github_api.delete_branch(
                github_token,
                GITHUB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass


@pytest_asyncio.fixture
async def clean_pr(
    github_token: str,
    pipeline_id: str,
) -> AsyncGenerator[PrInfo]:
    """
    Create a GitHub PR with clean calculator content on a unique branch.

    Args:
        github_token (str): GitHub API token.
        pipeline_id (str): Short pipeline identifier for title tagging.

    Yields:
        PrInfo: Information about the created PR.
    """

    branch_name = unique_branch_name("clean")

    await create_github_branch_with_file(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        branch_name=branch_name,
        file_path=CLEAN_CALCULATOR_PATH,
        content_b64=CLEAN_CALCULATOR_CONTENT_B64,
        commit_message=CLEAN_COMMIT_MESSAGE,
    )

    pr_number = await github_api.create_pr(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        head=branch_name,
        title=CLEAN_PR_TITLE.format(pipeline_id=pipeline_id),
    )

    try:
        yield PrInfo(
            repo=GITHUB_TEST_REPO,
            number=pr_number,
            head_branch=branch_name,
        )
    finally:
        try:
            await github_api.close_pr(github_token, GITHUB_TEST_REPO, pr_number)
        except Exception:
            pass

        try:
            await github_api.delete_branch(
                github_token,
                GITHUB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass


@pytest_asyncio.fixture
async def github_pipeline_branch(
    github_token: str,
) -> AsyncGenerator[BranchInfo]:
    """
    Create a temp branch with buggy content and a GitHub Actions workflow.
    """

    branch_name = unique_branch_name("pipeline")

    await create_github_branch_with_file(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        branch_name=branch_name,
        file_path=BUGGY_CALCULATOR_PATH,
        content_b64=BUGGY_CALCULATOR_CONTENT_B64,
        commit_message=BUGGY_COMMIT_MESSAGE,
    )

    workflow_b64 = github_actions_workflow_base64()

    await github_api.create_or_update_file(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        path=".github/workflows/review.yml",
        content_b64=workflow_b64,
        message=WORKFLOW_COMMIT_MESSAGE,
        branch=branch_name,
    )

    try:
        yield BranchInfo(
            repo=GITHUB_TEST_REPO,
            branch_name=branch_name,
        )
    finally:
        try:
            await github_api.delete_branch(
                github_token,
                GITHUB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass


@pytest_asyncio.fixture
async def github_pipeline_pr(
    github_token: str,
    github_pipeline_branch: BranchInfo,
    pipeline_id: str,
) -> AsyncGenerator[PrInfo]:
    """
    Create a PR from the pipeline branch to main.

    Args:
        github_token (str): GitHub API token.
        github_pipeline_branch (BranchInfo): The pipeline branch info.
        pipeline_id (str): Short pipeline identifier for title tagging.

    Yields:
        PrInfo: Information about the created PR.
    """

    pr_number = await github_api.create_pr(
        token=github_token,
        repo=github_pipeline_branch.repo,
        head=github_pipeline_branch.branch_name,
        title=PIPELINE_PR_TITLE.format(pipeline_id=pipeline_id),
    )

    try:
        yield PrInfo(
            repo=github_pipeline_branch.repo,
            number=pr_number,
            head_branch=github_pipeline_branch.branch_name,
        )
    finally:
        await github_api.close_pr(
            github_token,
            github_pipeline_branch.repo,
            pr_number,
        )


@pytest_asyncio.fixture
async def github_webhook_branch(
    github_token: str,
) -> AsyncGenerator[BranchInfo]:
    """
    Create a temp branch with buggy content for webhook tests.
    """

    branch_name = unique_branch_name("webhook")

    await create_github_branch_with_file(
        token=github_token,
        repo=GITHUB_TEST_REPO,
        branch_name=branch_name,
        file_path=BUGGY_CALCULATOR_PATH,
        content_b64=BUGGY_CALCULATOR_CONTENT_B64,
        commit_message=BUGGY_COMMIT_MESSAGE,
    )

    try:
        yield BranchInfo(
            repo=GITHUB_TEST_REPO,
            branch_name=branch_name,
        )
    finally:
        try:
            await github_api.delete_branch(
                github_token,
                GITHUB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass
