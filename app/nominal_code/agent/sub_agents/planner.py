from __future__ import annotations

import json
import logging
import re
from typing import Any

from nominal_code.agent.sub_agents.prompts import load_planner_system_prompt
from nominal_code.agent.sub_agents.result import ExploreGroup
from nominal_code.llm.messages import Message, TextBlock
from nominal_code.llm.provider import LLMProvider

logger: logging.Logger = logging.getLogger(__name__)

JSON_FENCE_PATTERN: re.Pattern[str] = re.compile(
    r"```(?:json)?\s*\n(.*?)\n\s*```",
    re.DOTALL,
)

DEFAULT_PLANNER_MAX_TOKENS: int = 4096


async def plan_exploration_groups(
    changed_files: list[str],
    diffs: dict[str, str],
    provider: LLMProvider,
    model: str,
    system_prompt: str = "",
    max_tokens: int = DEFAULT_PLANNER_MAX_TOKENS,
) -> list[ExploreGroup] | None:
    """
    Partition changed files into exploration groups using an LLM.

    Makes a single completion call (no tools) with file paths and diff
    line counts. Parses the JSON response into ``ExploreGroup`` objects.

    The provider instance is shared with the caller and is NOT closed
    by this function.

    Args:
        changed_files (list[str]): Changed file paths relative to the
            repo root.
        diffs (dict[str, str]): Unified diffs per file (used for line
            counts in the prompt).
        provider (LLMProvider): The LLM provider for the planner call.
        model (str): Model identifier for the planner.
        system_prompt (str): Override system prompt. Uses the bundled
            ``planner.md`` prompt when empty.
        max_tokens (int): Maximum tokens in the planner response.

    Returns:
        list[ExploreGroup] | None: Parsed groups, or ``None`` if
            planning failed or produced invalid output.
    """

    if not system_prompt:
        system_prompt = load_planner_system_prompt()

    user_message: str = build_planner_user_message(changed_files, diffs)

    try:
        response = await provider.send(
            messages=[Message(role="user", content=[TextBlock(text=user_message)])],
            system_prompt=system_prompt,
            tools=[],
            model=model,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.error("Planner LLM call failed: %s", exc)

        return None

    response_text: str = "\n".join(
        block.text for block in response.content if isinstance(block, TextBlock)
    )

    if not response_text.strip():
        logger.warning("Planner returned empty response")

        return None

    logger.info("Planner response: %s", response_text)

    return parse_planner_response(response_text, changed_files)


def build_planner_user_message(
    changed_files: list[str],
    diffs: dict[str, str],
) -> str:
    """
    Build the user message for the planner with file paths and line counts.

    Args:
        changed_files (list[str]): Changed file paths.
        diffs (dict[str, str]): Unified diffs per file.

    Returns:
        str: The formatted user message.
    """

    lines: list[str] = ["Changed files:\n"]

    for file_path in changed_files:
        diff_text: str = diffs.get(file_path, "")
        added: int = 0
        removed: int = 0

        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        lines.append(f"  {file_path}  |  +{added} -{removed}")

    return "\n".join(lines)


def parse_planner_response(
    response_text: str,
    changed_files: list[str],
) -> list[ExploreGroup] | None:
    """
    Parse the planner LLM response into exploration groups.

    Tries direct JSON parsing first, then attempts extraction from
    markdown code fences. Validates structure and filters unknown files.

    Args:
        response_text (str): Raw text from the planner LLM.
        changed_files (list[str]): Valid file paths to filter against.

    Returns:
        list[ExploreGroup] | None: Parsed groups, or ``None`` if parsing
            or validation failed.
    """

    parsed: list[dict[str, Any]] | None = _try_parse_json(response_text)

    if parsed is None:
        match: re.Match[str] | None = JSON_FENCE_PATTERN.search(response_text)

        if match:
            parsed = _try_parse_json(match.group(1))

    if parsed is None:
        logger.warning("Failed to parse planner response as JSON")

        return None

    if not isinstance(parsed, list):
        logger.warning("Planner response is not a JSON array")

        return None

    valid_files: set[str] = set(changed_files)
    groups: list[ExploreGroup] = []

    for entry in parsed:
        if not isinstance(entry, dict):
            continue

        label: str = str(entry.get("label", "")).strip()
        files_raw: list[str] = entry.get("files", [])
        prompt: str = str(entry.get("prompt", "")).strip()

        if not label or not prompt or not isinstance(files_raw, list):
            continue

        filtered_files: list[str] = [
            file_path
            for file_path in files_raw
            if isinstance(file_path, str) and file_path in valid_files
        ]

        if not filtered_files:
            logger.warning(
                "Group '%s' has no valid files after filtering",
                label,
            )

            continue

        groups.append(
            ExploreGroup(label=label, files=filtered_files, prompt=prompt),
        )

    if not groups:
        logger.warning("No valid groups after parsing planner response")

        return None

    return groups


def _try_parse_json(text: str) -> list[dict[str, Any]] | None:
    """
    Attempt to parse text as a JSON array.

    Args:
        text (str): The text to parse.

    Returns:
        list[dict[str, Any]] | None: Parsed JSON array, or ``None``
            on failure.
    """

    try:
        result: Any = json.loads(text.strip())

        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    return None
