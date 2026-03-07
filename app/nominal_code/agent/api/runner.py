from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from nominal_code.agent.api.tools import (
    SUBMIT_REVIEW_TOOL_NAME,
    execute_tool,
    get_tool_definitions,
)
from nominal_code.agent.memory import truncate_messages
from nominal_code.agent.providers.base import LLMProvider, ProviderError
from nominal_code.agent.providers.types import (
    ContentBlock,
    LLMResponse,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.agent.result import AgentResult

MAX_RESPONSE_TOKENS: int = 16384

logger: logging.Logger = logging.getLogger(__name__)


async def run_agent_api(
    prompt: str,
    cwd: Path,
    model: str,
    provider: LLMProvider,
    max_turns: int = 0,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    prior_messages: list[Message] | None = None,
) -> AgentResult:
    """
    Run the agent using an LLM provider with tool use.

    Implements the agentic loop: sends a prompt, processes tool_use
    responses by executing tools locally, sends results back, and repeats
    until the model produces a final text answer or max_turns is reached.

    When ``submit_review`` is in ``allowed_tools``, the corresponding
    structured-output tool is included. If the model calls it, the tool
    input is serialized as JSON and returned as the agent output.

    Args:
        prompt (str): The user's prompt to pass to the agent.
        cwd (Path): Working directory for tool execution.
        model (str): The model to use.
        provider (LLMProvider): The LLM provider to use for API calls.
        max_turns (int): Maximum agentic turns (0 for unlimited).
        system_prompt (str): Optional system prompt for the agent.
        allowed_tools (list[str] | None): Restrict which tools the agent may use.
        prior_messages (list[Message] | None): Prior conversation messages
            for multi-turn continuity. Prepended before the new user message.

    Returns:
        AgentResult: The parsed result from the agent.
    """

    tool_definitions: list[ToolDefinition] = get_tool_definitions(allowed_tools)

    messages: list[Message] = list(prior_messages) if prior_messages else []
    messages.append(Message(role="user", content=[TextBlock(text=prompt)]))
    messages = truncate_messages(messages)

    turns: int = 0
    start_time: int = _now_ms()
    conversation_id: str | None = None

    try:
        while True:
            response: LLMResponse = await provider.send(
                messages=messages,
                system_prompt=system_prompt,
                tools=tool_definitions,
                model=model,
                max_tokens=MAX_RESPONSE_TOKENS,
                previous_response_id=conversation_id,
            )
            conversation_id = response.response_id

            messages.append(
                Message(role="assistant", content=list(response.content)),
            )

            tool_use_blocks: list[ToolUseBlock] = [
                block for block in response.content if isinstance(block, ToolUseBlock)
            ]

            if not tool_use_blocks:
                output: str = _extract_text(response)
                duration_ms: int = _now_ms() - start_time

                return AgentResult(
                    output=output or "Done, no output.",
                    is_error=False,
                    num_turns=turns,
                    duration_ms=duration_ms,
                    messages=tuple(messages),
                    conversation_id=conversation_id,
                )

            for block in tool_use_blocks:
                if block.name == SUBMIT_REVIEW_TOOL_NAME:
                    if len(tool_use_blocks) > 1:
                        logger.warning(
                            "submit_review called alongside %d other tool(s); "
                            "ignoring other calls",
                            len(tool_use_blocks) - 1,
                        )

                    duration_ms = _now_ms() - start_time

                    return AgentResult(
                        output=json.dumps(block.input),
                        is_error=False,
                        num_turns=turns,
                        duration_ms=duration_ms,
                        messages=tuple(messages),
                        conversation_id=conversation_id,
                    )

            tool_results: list[ContentBlock] = []

            for block in tool_use_blocks:
                logger.debug(
                    "[tool_use] %s(%s)",
                    block.name,
                    block.input,
                )

                tool_output: str
                is_error: bool
                tool_output, is_error = await execute_tool(
                    name=block.name,
                    tool_input=block.input,
                    cwd=cwd,
                    allowed_tools=allowed_tools,
                )

                logger.debug(
                    "[tool_result] %s error=%s %.500s",
                    block.id,
                    is_error,
                    tool_output,
                )

                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=block.id,
                        content=tool_output,
                        is_error=is_error,
                    ),
                )

            messages.append(Message(role="user", content=tool_results))

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
                    messages=tuple(messages),
                    conversation_id=conversation_id,
                )

    except ProviderError as exc:
        duration_ms = _now_ms() - start_time

        logger.exception("LLM provider error")

        return AgentResult(
            output=f"API error: {exc}",
            is_error=True,
            num_turns=turns,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = _now_ms() - start_time

        logger.exception("Unexpected error in API runner")

        return AgentResult(
            output=f"Unexpected error: {exc}",
            is_error=True,
            num_turns=turns,
            duration_ms=duration_ms,
        )


def _extract_text(response: LLMResponse) -> str:
    """
    Extract all text blocks from an LLM response.

    Args:
        response (LLMResponse): The LLM response.

    Returns:
        str: Concatenated text from all text blocks.
    """

    parts: list[str] = [
        block.text
        for block in response.content
        if isinstance(block, TextBlock) and block.text
    ]

    return "\n".join(parts)


def _extract_last_text(messages: list[Message]) -> str:
    """
    Extract text from the last assistant message in the history.

    Args:
        messages (list[Message]): The full message history.

    Returns:
        str: Text from the last assistant message, or empty string.
    """

    for message in reversed(messages):
        if message.role != "assistant":
            continue

        parts: list[str] = [
            block.text
            for block in message.content
            if isinstance(block, TextBlock) and block.text
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
