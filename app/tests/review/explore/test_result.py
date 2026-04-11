# type: ignore
import pytest

from nominal_code.review.explore.result import (
    AggregatedMetrics,
    ExploreGroup,
    ParallelExploreResult,
    SubAgentResult,
)


class TestExploreGroup:
    def test_construction(self):
        group = ExploreGroup(
            label="callers",
            prompt="Search for callers of changed functions.",
        )

        assert group.label == "callers"
        assert group.prompt == "Search for callers of changed functions."

    def test_frozen(self):
        group = ExploreGroup(label="test", prompt="test")

        with pytest.raises(AttributeError):
            group.label = "changed"


class TestSubAgentResult:
    def test_construction_minimal(self):
        group = ExploreGroup(label="test", prompt="explore")
        result = SubAgentResult(
            group=group,
            output="Done.",
            is_error=False,
            num_turns=3,
            duration_ms=1200,
        )

        assert result.group is group
        assert result.output == "Done."
        assert result.is_error is False
        assert result.num_turns == 3
        assert result.duration_ms == 1200
        assert result.messages == ()
        assert result.cost is None

    def test_frozen(self):
        group = ExploreGroup(label="test", prompt="test")
        result = SubAgentResult(
            group=group,
            output="",
            is_error=False,
            num_turns=0,
            duration_ms=0,
        )

        with pytest.raises(AttributeError):
            result.output = "changed"


class TestAggregatedMetrics:
    def test_default_values(self):
        metrics = AggregatedMetrics()

        assert metrics.total_turns == 0
        assert metrics.total_api_calls == 0
        assert metrics.total_input_tokens == 0
        assert metrics.total_output_tokens == 0
        assert metrics.total_cache_creation_tokens == 0
        assert metrics.total_cache_read_tokens == 0
        assert metrics.total_cost_usd is None
        assert metrics.duration_ms == 0
        assert metrics.num_groups == 0
        assert metrics.group_labels == ()

    def test_construction_with_values(self):
        metrics = AggregatedMetrics(
            total_turns=10,
            total_api_calls=5,
            total_input_tokens=5000,
            total_output_tokens=1000,
            total_cost_usd=0.25,
            duration_ms=8000,
            num_groups=3,
            group_labels=("auth", "api", "tests"),
        )

        assert metrics.total_turns == 10
        assert metrics.total_cost_usd == 0.25
        assert metrics.num_groups == 3
        assert metrics.group_labels == ("auth", "api", "tests")


class TestParallelExploreResult:
    def test_default_empty(self):
        result = ParallelExploreResult()

        assert result.sub_results == ()
        assert isinstance(result.metrics, AggregatedMetrics)
        assert result.metrics.total_turns == 0

    def test_frozen(self):
        result = ParallelExploreResult()

        with pytest.raises(AttributeError):
            result.sub_results = ()
