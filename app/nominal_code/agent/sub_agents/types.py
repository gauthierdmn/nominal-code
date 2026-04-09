from __future__ import annotations

from enum import StrEnum


class AgentType(StrEnum):
    """
    Sub-agent type with associated tool restrictions.

    Each type defines a fixed set of allowed tools. Sub-agents cannot
    spawn other sub-agents (no recursive agent calls).

    Attributes:
        EXPLORE: Read-only codebase exploration.
        PLAN: Read-only exploration for planning and analysis.
    """

    EXPLORE = "explore"
    PLAN = "plan"


AGENT_TYPE_TOOLS: dict[AgentType, list[str]] = {
    AgentType.EXPLORE: ["Read", "Glob", "Grep", "Bash"],
    AgentType.PLAN: ["Read", "Glob", "Grep", "Bash"],
}

SUB_AGENT_SYSTEM_SUFFIX: str = (
    "\n\nYou are a background sub-agent of type `{agent_type}`. "
    "Work only on the delegated task, use only the tools available to you, "
    "do not ask the user questions, and finish with a concise result."
)

DEFAULT_MAX_TURNS_PER_SUB_AGENT: int = 32
