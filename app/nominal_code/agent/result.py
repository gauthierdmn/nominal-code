from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentResult:
    """
    Result from an agent invocation.

    Attributes:
        output (str): The text output from the agent.
        is_error (bool): Whether the invocation ended in error.
        num_turns (int): Number of agentic turns taken.
        duration_ms (int): Wall-clock duration in milliseconds.
        session_id (str): The agent session ID for resumption (CLI only).
    """

    output: str
    is_error: bool
    num_turns: int
    duration_ms: int
    session_id: str = ""
