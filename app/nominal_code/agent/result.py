from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nominal_code.llm.messages import Message
from nominal_code.models import InvocationError

if TYPE_CHECKING:
    from nominal_code.llm.cost import CostSummary


@dataclass(frozen=True)
class AgentResult:
    """
    Result from an agent invocation.

    Attributes:
        output (str): The text the model produced. Populated on success
            (returned text response or serialized ``submit_review``
            input). Empty when ``error is not None`` — there is no
            model output to record on failure; the failure details
            live on ``error``.
        num_turns (int): Number of agentic turns taken.
        duration_ms (int): Wall-clock duration in milliseconds.
        conversation_id (str | None): Continuation token for the
            conversation. Maps to a CLI conversation ID or a provider
            response ID.
        messages (tuple[Message, ...]): Full message history from the
            API runner. Empty for CLI runner and for failures that
            originate before any assistant turn completes.
        cost (CostSummary | None): Cost information for the invocation.
        max_turns_reached (bool): True when the agent loop stopped
            because it hit the configured ``max_turns`` budget. Not an
            error — the invocation completed normally — but consumers
            (notably the reviewer's notes-based fallback) need this
            signal because ``output`` may be empty or partial when the
            cap fires mid-tool-use.
        sub_agent_costs (tuple[CostSummary, ...]): Cost summaries from
            sub-agents spawned via the Agent tool during this run.
        error (InvocationError | None): ``None`` on success; otherwise
            wraps the failure classification and message.
    """

    output: str
    num_turns: int
    duration_ms: int
    conversation_id: str | None = None
    messages: tuple[Message, ...] = ()
    cost: CostSummary | None = None
    max_turns_reached: bool = False
    sub_agent_costs: tuple[CostSummary, ...] = ()
    error: InvocationError | None = None
