from __future__ import annotations

from dataclasses import dataclass

from nominal_code.agent.providers.types import Message


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
    """

    output: str
    is_error: bool
    num_turns: int
    duration_ms: int
    conversation_id: str | None = None
    messages: tuple[Message, ...] = ()
