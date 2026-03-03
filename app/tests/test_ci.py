# type: ignore
import os
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.ci import _load_platform_ci, run_ci_review
from nominal_code.models import AgentReview, EventType, ReviewFinding
from nominal_code.platforms.base import PlatformName, PullRequestEvent
from nominal_code.review.handler import ReviewResult


DUMMY_EVENT = PullRequestEvent(
    platform=PlatformName.GITHUB,
    repo_full_name="owner/repo",
    pr_number=42,
    pr_branch="feature",
    event_type=EventType.PR_OPENED,
)


def _make_platform_ci_module(event=None, platform=None, workspace="/workspace"):
    module = MagicMock(spec=ModuleType)
    module.build_event = MagicMock(return_value=event or DUMMY_EVENT)
    module.build_platform = MagicMock(return_value=platform or _make_mock_platform())
    module.resolve_workspace = MagicMock(return_value=workspace)

    return module


def _make_mock_platform():
    platform = MagicMock()
    platform.post_reply = AsyncMock()
    platform.submit_review = AsyncMock()

    return platform


def _make_review_result(agent_review=None, findings=None, summary="All good", raw_output="{}"):
    return ReviewResult(
        agent_review=agent_review or AgentReview(summary=summary),
        valid_findings=findings or [],
        rejected_findings=[],
        effective_summary=summary,
        raw_output=raw_output,
    )


class TestLoadPlatformCi:
    def test_load_platform_ci_github(self):
        module = _load_platform_ci(PlatformName.GITHUB)

        assert hasattr(module, "build_event")
        assert hasattr(module, "build_platform")
        assert hasattr(module, "resolve_workspace")

    def test_load_platform_ci_gitlab(self):
        module = _load_platform_ci(PlatformName.GITLAB)

        assert hasattr(module, "build_event")
        assert hasattr(module, "build_platform")
        assert hasattr(module, "resolve_workspace")


class TestRunCiReview:
    @pytest.mark.asyncio
    async def test_run_ci_review_unknown_platform(self):
        exit_code = await run_ci_review("bitbucket")

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_ci_review_success_with_findings(self):
        mock_platform = _make_mock_platform()
        findings = [ReviewFinding(file_path="a.py", line=10, body="Bug")]
        result = _make_review_result(
            agent_review=AgentReview(summary="Issues", findings=findings),
            findings=findings,
            summary="Issues",
        )
        module = _make_platform_ci_module(platform=mock_platform)

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "0", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, return_value=result):
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 0
        mock_platform.submit_review.assert_called_once()
        mock_platform.post_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_ci_review_success_no_findings(self):
        mock_platform = _make_mock_platform()
        result = _make_review_result()
        module = _make_platform_ci_module(platform=mock_platform)

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "0", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, return_value=result):
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 0
        mock_platform.submit_review.assert_not_called()
        mock_platform.post_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_ci_review_posts_raw_output_on_parse_failure(self):
        mock_platform = _make_mock_platform()
        result = ReviewResult(
            agent_review=None,
            valid_findings=[],
            rejected_findings=[],
            effective_summary="",
            raw_output="broken json",
        )
        module = _make_platform_ci_module(platform=mock_platform)

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "0", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, return_value=result):
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 0
        mock_platform.post_reply.assert_called_once()
        call_args = mock_platform.post_reply.call_args
        assert "broken json" in call_args[0][1].body

    @pytest.mark.asyncio
    async def test_run_ci_review_returns_one_on_runtime_error(self):
        module = _make_platform_ci_module()

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "0", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_ci_review_returns_one_on_unexpected_error(self):
        module = _make_platform_ci_module()

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "0", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, side_effect=TypeError("unexpected")):
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_ci_review_parses_max_turns(self):
        module = _make_platform_ci_module()
        result = _make_review_result()

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "invalid", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, return_value=result) as mock_review:
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 0
        config = mock_review.call_args.kwargs["config"]
        assert config.agent.max_turns == 0

    @pytest.mark.asyncio
    async def test_run_ci_review_passes_custom_prompt(self):
        module = _make_platform_ci_module()
        result = _make_review_result()

        env = {
            "INPUT_PROMPT": "focus on security",
            "INPUT_MODEL": "",
            "INPUT_MAX_TURNS": "0",
            "INPUT_CODING_GUIDELINES": "",
        }

        with patch("nominal_code.ci._load_platform_ci", return_value=module):
            with patch("nominal_code.ci.review", new_callable=AsyncMock, return_value=result) as mock_review:
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("github")

        assert exit_code == 0
        assert mock_review.call_args.kwargs["prompt"] == "focus on security"

    @pytest.mark.asyncio
    async def test_run_ci_review_dispatches_to_gitlab(self):
        gitlab_event = PullRequestEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/project",
            pr_number=7,
            pr_branch="fix",
            event_type=EventType.PR_OPENED,
        )
        module = _make_platform_ci_module(event=gitlab_event)
        result = _make_review_result()

        env = {"INPUT_PROMPT": "", "INPUT_MODEL": "", "INPUT_MAX_TURNS": "0", "INPUT_CODING_GUIDELINES": ""}

        with patch("nominal_code.ci._load_platform_ci", return_value=module) as mock_load:
            with patch("nominal_code.ci.review", new_callable=AsyncMock, return_value=result):
                with patch.dict(os.environ, env, clear=False):
                    exit_code = await run_ci_review("gitlab")

        assert exit_code == 0
        mock_load.assert_called_once_with(PlatformName.GITLAB)
