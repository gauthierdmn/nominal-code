from __future__ import annotations

from nominal_code.prompts import load_prompt


def load_explore_system_prompt() -> str:
    """
    Load the exploration system prompt from the bundled prompt file.

    Returns:
        str: The system prompt text.
    """

    return load_prompt("explore/explorer.md")


def load_fallback_explore_prompt() -> str:
    """
    Load the fallback exploration prompt from the bundled prompt file.

    Used when the planner is skipped (below file threshold) or fails.

    Returns:
        str: The fallback prompt text.
    """

    return load_prompt("explore/fallback.md")


def load_planner_system_prompt() -> str:
    """
    Load the planner system prompt from the bundled prompt file.

    Returns:
        str: The system prompt text.
    """

    return load_prompt("explore/planner.md")
