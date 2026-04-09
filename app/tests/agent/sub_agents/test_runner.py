# type: ignore
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.agent.sub_agents.result import (
    AggregatedMetrics,
    ExploreGroup,
    SubAgentResult,
)
from nominal_code.agent.sub_agents.runner import (
    DEFAULT_FILE_THRESHOLD,
    aggregate_metrics,
    allocate_turns,
    run_explore,
    run_explore_with_planner,
)
from nominal_code.agent.sub_agents.types import DEFAULT_MAX_TURNS_PER_SUB_AGENT
from nominal_code.llm.cost import CostSummary
from nominal_code.llm.messages import LLMResponse, StopReason, TextBlock
from nominal_code.models import ProviderName


def _make_group(label="test", files=None, prompt="explore"):
    return ExploreGroup(
        label=label,
        files=files or ["a.py"],
        prompt=prompt,
    )


def _make_sub_result(
    label="test",
    num_turns=2,
    duration_ms=500,
    cost=None,
):
    return SubAgentResult(
        group=_make_group(label=label),
        output="Done.",
        is_error=False,
        num_turns=num_turns,
        duration_ms=duration_ms,
        cost=cost,
    )


def _make_cost(
    input_tokens=100,
    output_tokens=50,
    cost_usd=0.01,
    api_calls=1,
):
    return CostSummary(
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cost_usd=cost_usd,
        num_api_calls=api_calls,
    )


def _make_text_response(text="Done."):
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
    )


class TestAllocateTurns:
    def test_divides_evenly(self):
        assert allocate_turns(12, 3) == 4

    def test_minimum_four(self):
        assert allocate_turns(8, 5) == 4

    def test_zero_returns_default(self):
        assert allocate_turns(0, 3) == DEFAULT_MAX_TURNS_PER_SUB_AGENT

    def test_negative_returns_default(self):
        assert allocate_turns(-1, 3) == DEFAULT_MAX_TURNS_PER_SUB_AGENT

    def test_zero_groups_returns_default(self):
        assert allocate_turns(12, 0) == DEFAULT_MAX_TURNS_PER_SUB_AGENT

    def test_large_budget_single_group(self):
        assert allocate_turns(100, 1) == 100


class TestAggregateMetrics:
    def test_sums_across_agents(self):
        results = [
            _make_sub_result(
                label="a",
                num_turns=3,
                cost=_make_cost(input_tokens=100, output_tokens=50, cost_usd=0.01),
            ),
            _make_sub_result(
                label="b",
                num_turns=5,
                cost=_make_cost(input_tokens=200, output_tokens=80, cost_usd=0.02),
            ),
        ]

        metrics = aggregate_metrics(results, duration_ms=5000)

        assert metrics.total_turns == 8
        assert metrics.total_api_calls == 2
        assert metrics.total_input_tokens == 300
        assert metrics.total_output_tokens == 130
        assert metrics.total_cost_usd == pytest.approx(0.03)
        assert metrics.duration_ms == 5000
        assert metrics.num_groups == 2
        assert metrics.group_labels == ("a", "b")

    def test_empty_results(self):
        metrics = aggregate_metrics([], duration_ms=100)

        assert metrics.total_turns == 0
        assert metrics.total_api_calls == 0
        assert metrics.total_cost_usd is None
        assert metrics.duration_ms == 100
        assert metrics.num_groups == 0

    def test_cost_none_when_all_none(self):
        results = [
            _make_sub_result(label="a", cost=None),
            _make_sub_result(label="b", cost=None),
        ]

        metrics = aggregate_metrics(results, duration_ms=100)

        assert metrics.total_cost_usd is None


class TestRunExplore:
    @pytest.mark.asyncio
    async def test_empty_groups_returns_empty(self, tmp_path):
        mock_provider = AsyncMock()

        result = await run_explore(
            groups=[],
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert result.sub_results == ()
        assert result.metrics.num_groups == 0

    @pytest.mark.asyncio
    async def test_single_group_success(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Found callers."),
        )

        group = _make_group(label="core", files=["src/core.py"])

        result = await run_explore(
            groups=[group],
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert len(result.sub_results) == 1
        assert result.sub_results[0].group.label == "core"
        assert result.sub_results[0].output == "Found callers."
        assert result.metrics.num_groups == 1

    @pytest.mark.asyncio
    async def test_parallel_groups_success(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Done."),
        )

        groups = [
            _make_group(label="auth", files=["auth.py"]),
            _make_group(label="api", files=["api.py"]),
        ]

        result = await run_explore(
            groups=groups,
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert len(result.sub_results) == 2
        assert result.metrics.num_groups == 2
        labels = {sub.group.label for sub in result.sub_results}
        assert labels == {"auth", "api"}

    @pytest.mark.asyncio
    @patch("nominal_code.agent.sub_agents.runner._run_single_sub_agent")
    async def test_failed_sub_agent_excluded(self, mock_run, tmp_path):
        success_result = SubAgentResult(
            group=_make_group(label="passing"),
            output="Done.",
            is_error=False,
            num_turns=2,
            duration_ms=500,
        )
        mock_run.side_effect = [
            RuntimeError("coroutine crashed"),
            success_result,
        ]

        mock_provider = AsyncMock()
        groups = [
            _make_group(label="failing", files=["fail.py"]),
            _make_group(label="passing", files=["pass.py"]),
        ]

        result = await run_explore(
            groups=groups,
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert len(result.sub_results) == 1
        assert result.sub_results[0].group.label == "passing"

    @pytest.mark.asyncio
    @patch("nominal_code.agent.sub_agents.runner._run_single_sub_agent")
    async def test_all_fail_returns_empty(self, mock_run, tmp_path):
        mock_run.side_effect = RuntimeError("coroutine crashed")

        mock_provider = AsyncMock()
        groups = [_make_group(label="a"), _make_group(label="b")]

        result = await run_explore(
            groups=groups,
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert len(result.sub_results) == 0
        assert result.metrics.num_groups == 0

    @pytest.mark.asyncio
    async def test_provider_error_returns_error_result(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=RuntimeError("API error"))

        groups = [_make_group(label="error-test")]

        result = await run_explore(
            groups=groups,
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert len(result.sub_results) == 1
        assert result.sub_results[0].is_error is True


class TestRunExploreWithPlanner:
    @pytest.mark.asyncio
    async def test_empty_files_returns_empty(self, tmp_path):
        mock_provider = AsyncMock()

        result = await run_explore_with_planner(
            changed_files=[],
            diffs={},
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
        )

        assert result.sub_results == ()

    @pytest.mark.asyncio
    async def test_below_threshold_single_agent(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Explored."),
        )

        changed_files = ["a.py", "b.py"]

        result = await run_explore_with_planner(
            changed_files=changed_files,
            diffs={"a.py": "+new", "b.py": "+new"},
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
            file_threshold=DEFAULT_FILE_THRESHOLD,
        )

        assert len(result.sub_results) == 1
        assert result.sub_results[0].group.label == "all-files"
        assert set(result.sub_results[0].group.files) == {"a.py", "b.py"}

    @pytest.mark.asyncio
    @patch("nominal_code.agent.sub_agents.runner.plan_exploration_groups")
    async def test_above_threshold_uses_planner(
        self,
        mock_plan,
        tmp_path,
    ):
        mock_plan.return_value = [
            ExploreGroup(label="group-a", files=["a.py", "b.py"], prompt="explore a"),
            ExploreGroup(label="group-b", files=["c.py", "d.py"], prompt="explore b"),
        ]

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Done."),
        )

        changed_files = [f"file{index}.py" for index in range(10)]

        result = await run_explore_with_planner(
            changed_files=changed_files,
            diffs={file_path: "+x" for file_path in changed_files},
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
            file_threshold=8,
        )

        mock_plan.assert_called_once()
        assert len(result.sub_results) == 2

    @pytest.mark.asyncio
    @patch("nominal_code.agent.sub_agents.runner.plan_exploration_groups")
    async def test_planner_failure_falls_back(self, mock_plan, tmp_path):
        mock_plan.return_value = None

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Explored."),
        )

        changed_files = [f"file{index}.py" for index in range(10)]

        result = await run_explore_with_planner(
            changed_files=changed_files,
            diffs={file_path: "+x" for file_path in changed_files},
            cwd=tmp_path,
            provider=mock_provider,
            model="test-model",
            file_threshold=8,
        )

        assert len(result.sub_results) == 1
        assert result.sub_results[0].group.label == "all-files"
