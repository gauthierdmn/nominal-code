from __future__ import annotations

import logging
from typing import Any

from nominal_code.llm.cost import compute_cost
from nominal_code.llm.messages import (
    Message,
    TextBlock,
    TokenUsage,
    ToolChoice,
    ToolDefinition,
    ToolUseBlock,
)
from nominal_code.llm.provider import LLMProvider
from nominal_code.review.explore.prompts import load_planner_system_prompt
from nominal_code.review.explore.result import ExploreGroup, PlannerResult

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_PLANNER_MAX_TOKENS: int = 4096

SUBMIT_PLAN_TOOL_NAME: str = "submit_plan"

SUBMIT_PLAN_TOOL: ToolDefinition = {
    "name": SUBMIT_PLAN_TOOL_NAME,
    "description": (
        "Submit the exploration plan. You MUST call this tool with your "
        "concern-based groups. Do not output raw JSON — always use this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "description": "Concern-based exploration groups.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short descriptive label for the concern.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Specific exploration instructions for the "
                                "sub-agent investigating this concern."
                            ),
                        },
                    },
                    "required": ["label", "prompt"],
                },
            },
        },
        "required": ["groups"],
    },
}


async def plan_exploration_groups(
    changed_files: list[str],
    diffs: dict[str, str],
    provider: LLMProvider,
    model: str,
    guidelines: str,
    system_prompt: str = "",
    max_tokens: int = DEFAULT_PLANNER_MAX_TOKENS,
) -> PlannerResult | None:
    """
    Partition review work into concern-based exploration groups using an LLM.

    Makes a single tool-use call with file paths, diff line counts, and
    coding guidelines. The planner is forced to call the ``submit_plan``
    tool with structured JSON output.

    The provider instance is shared with the caller and is NOT closed
    by this function.

    Args:
        changed_files (list[str]): Changed file paths relative to the
            repo root.
        diffs (dict[str, str]): Unified diffs per file (used for line
            counts in the prompt).
        provider (LLMProvider): The LLM provider for the planner call.
        model (str): Model identifier for the planner.
        guidelines (str): Resolved coding guidelines for the project.
            May be empty when no guidelines are configured.
        system_prompt (str): Override system prompt. Uses the bundled
            ``planner.md`` prompt when empty.
        max_tokens (int): Maximum tokens in the planner response.

    Returns:
        PlannerResult | None: Parsed groups with token usage, or
            ``None`` if planning failed or produced invalid output.
    """

    if not system_prompt:
        system_prompt = load_planner_system_prompt()

    user_message: str = build_planner_user_message(changed_files, diffs, guidelines)

    logger.info(
        "Planner system prompt (%d chars):\n%s",
        len(system_prompt),
        system_prompt,
    )
    logger.info(
        "Planner user prompt (%d chars):\n%s",
        len(user_message),
        user_message,
    )

    try:
        response = await provider.send(
            messages=[Message(role="user", content=[TextBlock(text=user_message)])],
            system_prompt=system_prompt,
            tools=[SUBMIT_PLAN_TOOL],
            model=model,
            max_tokens=max_tokens,
            tool_choice=ToolChoice.REQUIRED,
        )
    except Exception as exc:
        logger.error("Planner LLM call failed: %s", exc)

        return None

    tool_use_block: ToolUseBlock | None = None

    for block in response.content:
        if isinstance(block, ToolUseBlock) and block.name == SUBMIT_PLAN_TOOL_NAME:
            tool_use_block = block

            break

    usage: TokenUsage | None = response.usage

    if usage is not None:
        planner_cost: float | None = compute_cost(usage=usage, model=model)

        logger.info(
            "Step cost [planner]: tokens_in=%d, tokens_out=%d, api_calls=1, cost=$%.4f",
            usage.input_tokens,
            usage.output_tokens,
            planner_cost or 0.0,
        )

    if tool_use_block is None:
        logger.warning("Planner did not call submit_plan tool")

        return None

    logger.info("Planner response: %s", tool_use_block.input)

    groups: list[ExploreGroup] | None = parse_plan_tool_input(tool_use_block.input)

    if groups is None:
        return None

    return PlannerResult(groups=groups, usage=usage)


def build_planner_user_message(
    changed_files: list[str],
    diffs: dict[str, str],
    guidelines: str,
) -> str:
    """
    Build the user message for the planner with file paths, line counts, and guidelines.

    Args:
        changed_files (list[str]): Changed file paths.
        diffs (dict[str, str]): Unified diffs per file.
        guidelines (str): Resolved coding guidelines. May be empty.

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

    lines.append(f"\nTotal: {len(changed_files)} files")

    if guidelines:
        lines.append(f"\nCoding guidelines:\n\n{guidelines}")

    return "\n".join(lines)


def parse_plan_tool_input(
    tool_input: dict[str, Any],
) -> list[ExploreGroup] | None:
    """
    Parse the submit_plan tool input into exploration groups.

    Args:
        tool_input (dict[str, Any]): The tool call input from the LLM.

    Returns:
        list[ExploreGroup] | None: Parsed groups, or ``None`` if
            validation failed.
    """

    raw_groups: list[dict[str, Any]] = tool_input.get("groups", [])

    if not isinstance(raw_groups, list):
        logger.warning("Planner tool input 'groups' is not a list")

        return None

    groups: list[ExploreGroup] = []

    for entry in raw_groups:
        if not isinstance(entry, dict):
            continue

        label: str = str(entry.get("label", "")).strip()
        prompt: str = str(entry.get("prompt", "")).strip()

        if not label or not prompt:
            continue

        groups.append(
            ExploreGroup(label=label, prompt=prompt),
        )

    if not groups:
        logger.warning("No valid groups in planner tool input")

        return None

    return groups
