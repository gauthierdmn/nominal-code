# type: ignore
import os
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.commands.ci import (
    _build_ci_config,
    run_ci_review,
)
from nominal_code.config import ApiAgentConfig
from nominal_code.handlers.review import ReviewResult
from nominal_code.llm.cost import CostSummary, format_cost_summary
from nominal_code.models import AgentReview, EventType, ProviderName, ReviewFinding
from nominal_code.platforms import load_platform_ci
from nominal_code.platforms.base import PlatformName, PullRequestEvent

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

LOAD_CI = "nominal_code.commands.ci.load_platform_ci"
REVIEW = "nominal_code.commands.ci.review"


def _make_platform_ci_module(
    event=None,
    platform=None,
    workspace="/workspace",
):
    module = MagicMock(spec=ModuleType)
    module.build_event = MagicMock(
        return_value=event or DUMMY_EVENT,
    )
    module.build_platform = MagicMock(
        return_value=platform or _make_mock_platform(),
    )
    module.resolve_workspace = MagicMock(
        return_value=workspace,
    )

    return module


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


class TestLoadPlatformCi:
    def test_load_platform_ci_github(self):
        module = load_platform_ci(PlatformName.GITHUB)

        assert hasattr(module, "build_event")
        assert hasattr(module, "build_platform")
        assert hasattr(module, "resolve_workspace")

    def test_load_platform_ci_gitlab(self):
        module = load_platform_ci(PlatformName.GITLAB)

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
        module = _make_platform_ci_module(
            platform=mock_platform,
        )

        with (
            patch(LOAD_CI, return_value=module),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 0
        mock_platform.submit_review.assert_called_once()
        mock_platform.post_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_ci_review_success_no_findings(self):
        mock_platform = _make_mock_platform()
        result = _make_review_result()
        module = _make_platform_ci_module(
            platform=mock_platform,
        )

        with (
            patch(LOAD_CI, return_value=module),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 0
        mock_platform.submit_review.assert_not_called()
        mock_platform.post_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_ci_review_posts_raw_on_parse_failure(self):
        mock_platform = _make_mock_platform()
        result = ReviewResult(
            agent_review=None,
            valid_findings=[],
            rejected_findings=[],
            effective_summary="",
            raw_output="broken json",
        )
        module = _make_platform_ci_module(
            platform=mock_platform,
        )

        with (
            patch(LOAD_CI, return_value=module),
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("github")

        assert exit_code == 0
        mock_platform.post_reply.assert_called_once()
        call_args = mock_platform.post_reply.call_args
        assert "broken json" in call_args.kwargs["reply"].body

    @pytest.mark.asyncio
    async def test_run_ci_review_returns_one_on_runtime_error(
        self,
    ):
        module = _make_platform_ci_module()

        with (
            patch(LOAD_CI, return_value=module),
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
    async def test_run_ci_review_returns_one_on_unexpected_error(
        self,
    ):
        module = _make_platform_ci_module()

        with (
            patch(LOAD_CI, return_value=module),
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
        module = _make_platform_ci_module()
        result = _make_review_result()
        env = {**CI_ENV, "INPUT_MAX_TURNS": "invalid"}

        with (
            patch(LOAD_CI, return_value=module),
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
        module = _make_platform_ci_module()
        result = _make_review_result()
        env = {**CI_ENV, "INPUT_PROMPT": "focus on security"}

        with (
            patch(LOAD_CI, return_value=module),
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
        module = _make_platform_ci_module(event=gitlab_event)
        result = _make_review_result()

        with (
            patch(LOAD_CI, return_value=module) as mock_load,
            patch(
                REVIEW,
                new_callable=AsyncMock,
                return_value=result,
            ),
            patch.dict(os.environ, CI_ENV, clear=False),
        ):
            exit_code = await run_ci_review("gitlab")

        assert exit_code == 0
        mock_load.assert_called_once_with(platform_name=PlatformName.GITLAB)
