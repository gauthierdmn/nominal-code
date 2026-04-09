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
        compacted_messages (tuple[Message, ...]): The compacted message
            history sent to the LLM on the final turn. Empty when
            compaction was not active or no compaction occurred.
        cost (CostSummary | None): Cost information for the invocation.
    """

    output: str
    is_error: bool
    num_turns: int
    duration_ms: int
    conversation_id: str | None = None
    messages: tuple[Message, ...] = ()
    compacted_messages: tuple[Message, ...] = ()
    cost: CostSummary | None = None
