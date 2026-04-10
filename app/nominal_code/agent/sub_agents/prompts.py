from __future__ import annotations

from nominal_code.prompts import load_prompt


def load_explore_system_prompt() -> str:
    """
    Load the exploration system prompt from the bundled prompt file.

    Returns:
        str: The system prompt text.
    """

    return load_prompt("sub_agents/explore.md")


def load_planner_system_prompt() -> str:
    """
    Load the planner system prompt from the bundled prompt file.

    Returns:
        str: The system prompt text.
    """

    return load_prompt("sub_agents/planner.md")
