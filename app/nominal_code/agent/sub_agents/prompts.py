from __future__ import annotations

import logging
from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)

_PROMPTS_DIR: Path = (
    Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "sub_agents"
)

EXPLORE_PROMPT_PATH: Path = _PROMPTS_DIR / "explore.md"
PLANNER_PROMPT_PATH: Path = _PROMPTS_DIR / "planner.md"

_EXPLORE_FALLBACK: str = (
    "You are a code exploration agent. Read the changed files, "
    "search for callers, check tests, and write a structured "
    "summary of what you found. Do NOT produce a review."
)

_PLANNER_FALLBACK: str = (
    "You are a code review planning agent. Split the changed files "
    "into 2-5 groups for parallel exploration. Output JSON only: "
    '[{"label": "...", "files": [...], "prompt": "..."}]'
)


def load_explore_system_prompt() -> str:
    """
    Load the exploration system prompt from the bundled prompt file.

    Returns:
        str: The system prompt text, or a fallback if the file is missing.
    """

    return _load_prompt(EXPLORE_PROMPT_PATH, _EXPLORE_FALLBACK)


def load_planner_system_prompt() -> str:
    """
    Load the planner system prompt from the bundled prompt file.

    Returns:
        str: The system prompt text, or a fallback if the file is missing.
    """

    return _load_prompt(PLANNER_PROMPT_PATH, _PLANNER_FALLBACK)


def _load_prompt(path: Path, fallback: str) -> str:
    """
    Load a prompt file with a fallback for missing files.

    Args:
        path (Path): Path to the prompt file.
        fallback (str): Fallback text if the file is missing.

    Returns:
        str: The prompt text.
    """

    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s, using fallback", path)

        return fallback
