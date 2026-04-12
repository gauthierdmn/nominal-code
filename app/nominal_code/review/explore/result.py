from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nominal_code.llm.cost import CostSummary
    from nominal_code.llm.messages import Message, TokenUsage


@dataclass(frozen=True)
class PlannerResult:
    """
    Result from the planner LLM call.

    Attributes:
        groups (list[ExploreGroup]): The exploration groups produced
            by the planner.
        usage (TokenUsage | None): Token usage for the planner call.
    """

    groups: list[ExploreGroup]
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class ExploreGroup:
    """
    A concern-based exploration group.

    Created by the planner and consumed by parallel sub-agents. Each
    group represents an investigation concern (e.g., callers, test
    coverage, type safety), not a file partition.

    Args:
        label (str): Short descriptive label for the concern.
        prompt (str): LLM-authored exploration prompt for the sub-agent.
    """

    label: str
    prompt: str


@dataclass(frozen=True)
class SubAgentResult:
    """
    Result from a single sub-agent execution.

    Wraps the fields from ``AgentResult`` together with the exploration
    group the sub-agent was assigned.

    Args:
        group (ExploreGroup): The exploration group this agent processed.
        output (str): The agent's text output.
        is_error (bool): Whether the execution ended in error.
        num_turns (int): Number of agentic turns taken.
        duration_ms (int): Wall-clock duration in milliseconds.
        messages (tuple[Message, ...]): Full message history from the agent.
        cost (CostSummary | None): Cost information for the invocation.
        notes (str): Structured findings written to the notes file during
            exploration. Empty when no notes were written.
    """

    group: ExploreGroup
    output: str
    is_error: bool
    num_turns: int
    duration_ms: int
    messages: tuple[Message, ...] = ()
    cost: CostSummary | None = None
    notes: str = ""


@dataclass(frozen=True)
class AggregatedMetrics:
    """
    Aggregated metrics across multiple sub-agents.

    Token counts and API calls are summed. Duration is wall-clock time
    (not sum) since sub-agents run in parallel.

    Args:
        total_turns (int): Sum of all sub-agent turns.
        total_api_calls (int): Sum of all API calls.
        total_input_tokens (int): Sum of all input tokens.
        total_output_tokens (int): Sum of all output tokens.
        total_cache_creation_tokens (int): Sum of all cache creation tokens.
        total_cache_read_tokens (int): Sum of all cache read tokens.
        total_cost_usd (float | None): Total cost in USD, or None if unavailable.
        duration_ms (int): Wall-clock duration in milliseconds.
        num_groups (int): Number of exploration groups.
        group_labels (tuple[str, ...]): Labels for each group.
    """

    total_turns: int = 0
    total_api_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float | None = None
    duration_ms: int = 0
    num_groups: int = 0
    group_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParallelExploreResult:
    """
    Aggregated result from parallel sub-agent exploration.

    Args:
        sub_results (tuple[SubAgentResult, ...]): Individual results
            per sub-agent.
        metrics (AggregatedMetrics): Aggregated metrics across all
            sub-agents.
    """

    sub_results: tuple[SubAgentResult, ...] = ()
    metrics: AggregatedMetrics = field(default_factory=AggregatedMetrics)
