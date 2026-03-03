import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from tests.integration.conftest import (
    BranchInfo,
    PrInfo,
    create_gitlab_branch_with_file,
    unique_branch_name,
)
from tests.integration.gitlab import api as gitlab_api
from tests.integration.helpers.ci_configs import gitlab_ci_yaml
from tests.integration.helpers.fixtures import (
    BUGGY_CALCULATOR_CONTENT,
    BUGGY_CALCULATOR_PATH,
    CLEAN_CALCULATOR_CONTENT,
    CLEAN_CALCULATOR_PATH,
    GITLAB_TEST_REPO,
)

BUGGY_COMMIT_MESSAGE = "test: add buggy calculator"
CLEAN_COMMIT_MESSAGE = "test: add clean calculator"
BUGGY_MR_TITLE = "test: buggy-calculator [{pipeline_id}]"
CLEAN_MR_TITLE = "test: clean-change [{pipeline_id}]"
PIPELINE_MR_TITLE = "test: pipeline review [{pipeline_id}]"
WEBHOOK_MR_TITLE = "test: webhook server [{pipeline_id}]"
CI_CONFIG_COMMIT_MESSAGE = "test: add CI config"


@pytest.fixture(scope="session")
def gitlab_token() -> str:
    """
    Return the GitLab token from environment.

    Returns:
        str: The GitLab API token.
    """

    token = os.environ.get("TEST_GITLAB_TOKEN", "")

    assert token, "TEST_GITLAB_TOKEN environment variable is required"

    return token


@pytest_asyncio.fixture
async def buggy_mr(
    gitlab_token: str,
    pipeline_id: str,
) -> AsyncGenerator[PrInfo]:
    """
    Create a GitLab MR with buggy calculator content on a unique branch.

    Args:
        gitlab_token (str): GitLab API token.
        pipeline_id (str): Short pipeline identifier for title tagging.

    Yields:
        PrInfo: Information about the created MR.
    """

    branch_name = unique_branch_name("buggy")

    await create_gitlab_branch_with_file(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        branch_name=branch_name,
        file_path=BUGGY_CALCULATOR_PATH,
        content=BUGGY_CALCULATOR_CONTENT,
        commit_message=BUGGY_COMMIT_MESSAGE,
    )

    mr_iid = await gitlab_api.create_mr(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        head=branch_name,
        title=BUGGY_MR_TITLE.format(pipeline_id=pipeline_id),
    )

    await gitlab_api.wait_for_mr_diff(gitlab_token, GITLAB_TEST_REPO, mr_iid)

    try:
        yield PrInfo(
            repo=GITLAB_TEST_REPO,
            number=mr_iid,
            head_branch=branch_name,
        )
    finally:
        try:
            await gitlab_api.close_mr(gitlab_token, GITLAB_TEST_REPO, mr_iid)
        except Exception:
            pass

        try:
            await gitlab_api.delete_branch(
                gitlab_token,
                GITLAB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass


@pytest_asyncio.fixture
async def clean_mr(
    gitlab_token: str,
    pipeline_id: str,
) -> AsyncGenerator[PrInfo]:
    """
    Create a GitLab MR with clean calculator content on a unique branch.

    Args:
        gitlab_token (str): GitLab API token.
        pipeline_id (str): Short pipeline identifier for title tagging.

    Yields:
        PrInfo: Information about the created MR.
    """

    branch_name = unique_branch_name("clean")

    await create_gitlab_branch_with_file(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        branch_name=branch_name,
        file_path=CLEAN_CALCULATOR_PATH,
        content=CLEAN_CALCULATOR_CONTENT,
        commit_message=CLEAN_COMMIT_MESSAGE,
    )

    mr_iid = await gitlab_api.create_mr(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        head=branch_name,
        title=CLEAN_MR_TITLE.format(pipeline_id=pipeline_id),
    )

    await gitlab_api.wait_for_mr_diff(gitlab_token, GITLAB_TEST_REPO, mr_iid)

    try:
        yield PrInfo(
            repo=GITLAB_TEST_REPO,
            number=mr_iid,
            head_branch=branch_name,
        )
    finally:
        try:
            await gitlab_api.close_mr(gitlab_token, GITLAB_TEST_REPO, mr_iid)
        except Exception:
            pass

        try:
            await gitlab_api.delete_branch(
                gitlab_token,
                GITLAB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass


@pytest_asyncio.fixture
async def gitlab_pipeline_branch(
    gitlab_token: str,
) -> AsyncGenerator[BranchInfo]:
    """
    Create a temp branch with buggy content and a ``.gitlab-ci.yml``.
    """

    branch_name = unique_branch_name("pipeline")

    await create_gitlab_branch_with_file(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        branch_name=branch_name,
        file_path=BUGGY_CALCULATOR_PATH,
        content=BUGGY_CALCULATOR_CONTENT,
        commit_message=BUGGY_COMMIT_MESSAGE,
    )

    ci_content = gitlab_ci_yaml()

    await gitlab_api.create_or_update_file(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        path=".gitlab-ci.yml",
        content=ci_content,
        message=CI_CONFIG_COMMIT_MESSAGE,
        branch=branch_name,
    )

    try:
        yield BranchInfo(
            repo=GITLAB_TEST_REPO,
            branch_name=branch_name,
        )
    finally:
        try:
            await gitlab_api.delete_branch(
                gitlab_token,
                GITLAB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass


@pytest_asyncio.fixture
async def gitlab_pipeline_mr(
    gitlab_token: str,
    gitlab_pipeline_branch: BranchInfo,
    pipeline_id: str,
) -> AsyncGenerator[PrInfo]:
    """
    Create an MR from the pipeline branch to main.

    Args:
        gitlab_token (str): GitLab API token.
        gitlab_pipeline_branch (BranchInfo): The pipeline branch info.
        pipeline_id (str): Short pipeline identifier for title tagging.

    Yields:
        PrInfo: Information about the created MR.
    """

    mr_iid = await gitlab_api.create_mr(
        token=gitlab_token,
        repo=gitlab_pipeline_branch.repo,
        head=gitlab_pipeline_branch.branch_name,
        title=PIPELINE_MR_TITLE.format(pipeline_id=pipeline_id),
    )

    await gitlab_api.wait_for_mr_diff(
        gitlab_token,
        gitlab_pipeline_branch.repo,
        mr_iid,
    )

    try:
        yield PrInfo(
            repo=gitlab_pipeline_branch.repo,
            number=mr_iid,
            head_branch=gitlab_pipeline_branch.branch_name,
        )
    finally:
        await gitlab_api.close_mr(
            gitlab_token,
            gitlab_pipeline_branch.repo,
            mr_iid,
        )


@pytest_asyncio.fixture
async def gitlab_webhook_branch(
    gitlab_token: str,
) -> AsyncGenerator[BranchInfo]:
    """
    Create a temp branch with buggy content for webhook tests.
    """

    branch_name = unique_branch_name("webhook")

    await create_gitlab_branch_with_file(
        token=gitlab_token,
        repo=GITLAB_TEST_REPO,
        branch_name=branch_name,
        file_path=BUGGY_CALCULATOR_PATH,
        content=BUGGY_CALCULATOR_CONTENT,
        commit_message=BUGGY_COMMIT_MESSAGE,
    )

    try:
        yield BranchInfo(
            repo=GITLAB_TEST_REPO,
            branch_name=branch_name,
        )
    finally:
        try:
            await gitlab_api.delete_branch(
                gitlab_token,
                GITLAB_TEST_REPO,
                branch_name,
            )
        except Exception:
            pass
