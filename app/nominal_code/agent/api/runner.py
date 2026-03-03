from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import anthropic

from nominal_code.agent.api.tools import execute_tool, get_tool_definitions
from nominal_code.agent.result import AgentResult

MAX_RESPONSE_TOKENS: int = 16384

logger: logging.Logger = logging.getLogger(__name__)


async def run_agent_api(
    prompt: str,
    cwd: Path,
    model: str,
    max_turns: int = 0,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
) -> AgentResult:
    """
    Run the agent using the Anthropic Messages API with tool use.

    Implements the agentic loop: sends a prompt, processes tool_use
    responses by executing tools locally, sends results back, and repeats
    until the model produces a final text answer or max_turns is reached.

    Args:
        prompt (str): The user's prompt to pass to the agent.
        cwd (Path): Working directory for tool execution.
        model (str): The Claude model to use.
        max_turns (int): Maximum agentic turns (0 for unlimited).
        system_prompt (str): Optional system prompt for the agent.
        allowed_tools (list[str] | None): Restrict which tools the agent may use.

    Returns:
        AgentResult: The parsed result from the agent.
    """

    client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic()
    tool_definitions: list[dict[str, Any]] = get_tool_definitions(allowed_tools)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    turns: int = 0
    start_time: int = _now_ms()

    try:
        while True:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": MAX_RESPONSE_TOKENS,
                "messages": messages,
                "cache_control": {"type": "ephemeral"},
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            if tool_definitions:
                kwargs["tools"] = tool_definitions

            response: anthropic.types.Message = await client.messages.create(
                **kwargs,
            )

            messages.append(
                {"role": "assistant", "content": _serialize_content(response)},
            )

            tool_use_blocks: list[anthropic.types.ToolUseBlock] = [
                block for block in response.content if block.type == "tool_use"
            ]

            if not tool_use_blocks:
                output: str = _extract_text(response)
                duration_ms: int = _now_ms() - start_time

                return AgentResult(
                    output=output or "Done, no output.",
                    is_error=False,
                    num_turns=turns,
                    duration_ms=duration_ms,
                    session_id="",
                )

            tool_results: list[dict[str, Any]] = []

            for block in tool_use_blocks:
                logger.debug(
                    "[tool_use] %s(%s)",
                    block.name,
                    block.input,
                )

                result: str = await execute_tool(
                    name=block.name,
                    tool_input=block.input,
                    cwd=cwd,
                    allowed_tools=allowed_tools,
                )

                is_error: bool = result.startswith("Error")

                logger.debug(
                    "[tool_result] %s error=%s %.500s",
                    block.id,
                    is_error,
                    result,
                )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                        "is_error": is_error,
                    },
                )

            messages.append({"role": "user", "content": tool_results})

            turns += 1

            if max_turns > 0 and turns >= max_turns:
                logger.warning(
                    "Agent reached max turns (%d), stopping",
                    max_turns,
                )

                output = _extract_last_text(messages)
                duration_ms = _now_ms() - start_time

                return AgentResult(
                    output=output or "Max turns reached.",
                    is_error=False,
                    num_turns=turns,
                    duration_ms=duration_ms,
                    session_id="",
                )

    except anthropic.APIError as exc:
        duration_ms = _now_ms() - start_time

        logger.exception("Anthropic API error")

        return AgentResult(
            output=f"API error: {exc.message}",
            is_error=True,
            num_turns=turns,
            duration_ms=duration_ms,
            session_id="",
        )
    except Exception as exc:
        duration_ms = _now_ms() - start_time

        logger.exception("Unexpected error in API runner")

        return AgentResult(
            output=f"Unexpected error: {exc}",
            is_error=True,
            num_turns=turns,
            duration_ms=duration_ms,
            session_id="",
        )


def _serialize_content(
    response: anthropic.types.Message,
) -> list[dict[str, Any]]:
    """
    Serialize response content blocks to dicts for message history.

    Args:
        response (anthropic.types.Message): The Anthropic API response.

    Returns:
        list[dict[str, Any]]: List of serialized content blocks.
    """

    blocks: list[dict[str, Any]] = []

    for block in response.content:
        if block.type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                },
            )

    return blocks


def _extract_text(response: anthropic.types.Message) -> str:
    """
    Extract all text blocks from an API response.

    Args:
        response (anthropic.types.Message): The Anthropic API response.

    Returns:
        str: Concatenated text from all text blocks.
    """

    parts: list[str] = [
        block.text for block in response.content if block.type == "text" and block.text
    ]

    return "\n".join(parts)


def _extract_last_text(messages: list[dict[str, Any]]) -> str:
    """
    Extract text from the last assistant message in the history.

    Args:
        messages (list[dict[str, Any]]): The full message history.

    Returns:
        str: Text from the last assistant message, or empty string.
    """

    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue

        content: Any = message.get("content", [])

        if isinstance(content, list):
            parts: list[str] = [
                block["text"]
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]

            if parts:
                return "\n".join(parts)

    return ""


def _now_ms() -> int:
    """
    Return the current time in milliseconds.

    Returns:
        int: Current time as integer milliseconds.
    """

    return int(time.monotonic() * 1000)
