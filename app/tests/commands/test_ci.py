# type: ignore
import contextlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.commands.ci.main import (
    _build_ci_config,
    run_ci_review,
)
from nominal_code.config import ApiAgentConfig
from nominal_code.llm.cost import CostSummary, format_cost_summary
from nominal_code.models import AgentReview, EventType, ProviderName, ReviewFinding
from nominal_code.platforms.base import PlatformName, PullRequestEvent
from nominal_code.review.handler import ReviewResult

DUMMY_EVENT = PullRequestEvent(
    platform=PlatformName.GITHUB,
    repo_full_name="owner/repo",
    pr_number=42,
    pr_branch="feature",
    event_type=EventType.PR_OPENED,
)

CI_ENV = {
    "INPUT_PROMPT": "",
    "INPUT_MODEL": "",
    "INPUT_MAX_TURNS": "0",
    "INPUT_CODING_GUIDELINES": "",
}

BUILD_PLATFORM = "nominal_code.commands.ci.main.build_platform"
BUILD_EVENT = "nominal_code.commands.ci.main._build_ci_event"
RESOLVE_WORKSPACE = "nominal_code.commands.ci.main._resolve_ci_workspace"
REVIEW = "nominal_code.commands.ci.main.run_and_post_review"


def _make_mock_platform():
    platform = MagicMock()
    platform.post_reply = AsyncMock()
    platform.submit_review = AsyncMock()

    return platform


def _make_review_result(
    agent_review=None,
    findings=None,
    summary="All good",
    raw_output="{}",
):
    return ReviewResult(
        agent_review=agent_review or AgentReview(summary=summary),
        valid_findings=findings or [],
        rejected_findings=[],
        effective_summary=summary,
        raw_output=raw_output,
    )


@contextlib.contextmanager
def _patch_ci_setup(event=None, platform=None, workspace="/workspace"):
    with (
        patch(BUILD_PLATFORM, return_value=platform or _make_mock_platform()),
        patch(BUILD_EVENT, return_value=event or DUMMY_EVENT),
        patch(RESOLVE_WORKSPACE, return_value=workspace),
    ):
        yield


class TestBuildCiConfig:
    def test_defaults_to_anthropic(self):
        env = {
            "INPUT_MODEL": "",
            "INPUT_MAX_TURNS": "0",
            "INPUT_CODING_GUIDELINES": "",
        }

        with patch.dict(os.environ, env, clear=False):
            config = _build_ci_config()

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.provider.name == ProviderName.ANTHROPIC

    def test_custom_provider(self):
        env = {
            "AGENT_PROVIDER": "openai",
            "INPUT_MODEL": "",
            "INPUT_MAX_TURNS": "0",
            "INPUT_CODING_GUIDELINES": "",
        }

        with patch.dict(os.environ, env, clear=False):
            config = _build_ci_config()

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.provider.name == ProviderName.OPENAI

    def test_invalid_provider_raises(self):
        env = {
            "AGENT_PROVIDER": "nonexistent",
            "INPUT_MODEL": "",
            "INPUT_MAX_TURNS": "0",
            "INPUT_CODING_GUIDELINES": "",
        }

        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="nonexistent"):
                _build_ci_config()

    def test_invalid_max_turns_defaults_to_zero(self):
        env = {
            "INPUT_MODEL": "",
            "INPUT_MAX_TURNS": "not-a-number",
            "INPUT_CODING_GUIDELINES": "",
        }

        with patch.dict(os.environ, env, clear=False):
            config = _build_ci_config()

        assert config.agent.max_turns == 0

    def test_custom_max_turns(self):
        env = {
            "INPUT_MODEL": "",
            "INPUT_MAX_TURNS": "5",
            "INPUT_CODING_GUIDELINES": "",
        }

        with patch.dict(os.environ, env, clear=False):
            config = _build_ci_config()

        assert config.agent.max_turns == 5


class TestFormatCostSummary:
    def test_none_returns_empty(self):
        result = format_cost_summary(None)

        assert result == ""

    def test_includes_model_and_provider(self):
        cost = CostSummary(
            model="gpt-4.1",
            provider=ProviderName.OPENAI,
            total_input_tokens=100,
            total_output_tokens=50,
        )

        result = format_cost_summary(cost)

        assert "gpt-4.1" in result
        assert "openai" in result

    def test_includes_token_counts(self):
        cost = CostSummary(
            total_input_tokens=1000,
            total_output_tokens=500,
        )

        result = format_cost_summary(cost)

        assert "1,000 in" in result
        assert "500 out" in result

    def test_includes_cache_read_tokens(self):
        cost = CostSummary(
            total_input_tokens=1000,
            total_output_tokens=500,
            total_cache_read_tokens=200,
        )

        result = format_cost_summary(cost)

        assert "cache read: 200" in result

    def test_omits_cache_read_when_zero(self):
        cost = CostSummary(
            total_input_tokens=1000,
            total_output_tokens=500,
            total_cache_read_tokens=0,
        )

        result = format_cost_summary(cost)

        assert "cache read" not in result

    def test_includes_cost_usd(self):
        cost = CostSummary(
            total_input_tokens=100,
            total_output_tokens=50,
            total_cost_usd=0.0123,
        )

        result = format_cost_summary(cost)

        assert "$0.0123" in result

    def test_includes_api_calls(self):
        cost = CostSummary(
            total_input_tokens=100,
            total_output_tokens=50,
            num_api_calls=3,
        )

        result = format_cost_summary(cost)

        assert "API calls: 3" in result

    def test_omits_api_calls_when_zero(self):
        cost = CostSummary(
            total_input_tokens=100,
            total_output_tokens=50,
            num_api_calls=0,
        )

        result = format_cost_summary(cost)

        assert "API calls" not in result


class TestRunCiReview:
    @pytest.mark.asyncio
    async def test_run_ci_review_unknown_platform(self):
        exit_code = await run_ci_review("bitbucket")

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_ci_review_success_with_findings(self):
        mock_platform = _make_mock_platform()
        findings = [
            ReviewFinding(file_path="a.py", line=10, body="Bug"),
        ]
        result = _make_review_result(
            agent_review=AgentReview(
                summary="Issues",
                findings=findings,
            ),
            findings=findings,
            summary="Issues",
        )

        with (
            _patch_ci_setup(platform=mock_platform),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_run_ci_review_success_no_findings(self):
        result = _make_review_result()

        with (
            _patch_ci_setup(),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_run_ci_review_returns_one_on_runtime_error(self):
        with (
            _patch_ci_setup(),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_ci_review_returns_one_on_unexpected_error(self):
        with (
            _patch_ci_setup(),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                side_effect=TypeError("unexpected"),
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_ci_review_parses_max_turns(self):
        result = _make_review_result()
        env = {**CI_ENV, "INPUT_MAX_TURNS": "invalid"}

        with (
            _patch_ci_setup(),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ) as mock_review,
            patch.dict(os.environ, env, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 0
        config = mock_review.call_args.kwargs["config"]
        assert config.agent.max_turns == 0

    @pytest.mark.asyncio
    async def test_run_ci_review_passes_custom_prompt(self):
        result = _make_review_result()
        env = {**CI_ENV, "INPUT_PROMPT": "focus on security"}

        with (
            _patch_ci_setup(),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ) as mock_review,
            patch.dict(os.environ, env, clear=False),
        ):
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
        result = _make_review_result()

        with (
            _patch_ci_setup(event=gitlab_event),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("gitlab")

        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_run_ci_review_platform_not_configured(self):
        with patch(
            BUILD_PLATFORM,
            side_effect=ValueError("not configured"),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 1
