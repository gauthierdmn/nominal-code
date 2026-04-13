from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nominal_code.llm.messages import Message

if TYPE_CHECKING:
    from nominal_code.llm.cost import CostSummary


@dataclass(frozen=True)
class AgentResult:
    """
    Result from an agent invocation.

    Attributes:
        output (str): The text output from the agent.
        is_error (bool): Whether the invocation ended in error.
        num_turns (int): Number of agentic turns taken.
        duration_ms (int): Wall-clock duration in milliseconds.
        conversation_id (str | None): Continuation token for the conversation.
            Maps to a CLI conversation ID or a provider response ID.
        messages (tuple[Message, ...]): Full message history from the API
            runner. Empty for CLI runner.
        cost (CostSummary | None): Cost information for the invocation.
        exhausted_without_review (bool): True when max_turns was reached
            without the model calling ``submit_review``.
        sub_agent_costs (tuple[CostSummary, ...]): Cost summaries from
            sub-agents spawned via the Agent tool during this run.
    """

    output: str
    is_error: bool
    num_turns: int
    duration_ms: int
    conversation_id: str | None = None
    messages: tuple[Message, ...] = ()
    cost: CostSummary | None = None
    exhausted_without_review: bool = False
    sub_agent_costs: tuple[CostSummary, ...] = ()
