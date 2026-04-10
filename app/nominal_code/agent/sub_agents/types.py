from __future__ import annotations

from enum import StrEnum

from nominal_code.prompts import load_prompt


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
    AgentType.EXPLORE: ["Read", "Glob", "Grep", "Bash", "WriteNotes"],
    AgentType.PLAN: ["Read", "Glob", "Grep", "Bash"],
}

SUB_AGENT_SYSTEM_SUFFIX: str = load_prompt("sub_agents/suffix.md")

DEFAULT_MAX_TURNS_PER_SUB_AGENT: int = 32
