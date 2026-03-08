from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    SystemMessage,
    UserMessage,
    query,
)
from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal import client as _sdk_client
from claude_agent_sdk._internal import message_parser as _sdk_parser
from claude_agent_sdk.types import (
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from nominal_code.agent.cost import CostSummary
from nominal_code.agent.result import AgentResult
from nominal_code.models import ProviderName

CONVERSATION_ID_INIT_SUBTYPE: str = "init"
MAX_TOOL_RESULT_LOG_LENGTH: int = 500
DEFAULT_PERMISSION_MODE: Literal["bypassPermissions"] = "bypassPermissions"

logger: logging.Logger = logging.getLogger(__name__)

_original_parse_message = _sdk_parser.parse_message


def _patched_parse_message(data: dict[str, Any]) -> Message:
    """
    Wrap the SDK's ``parse_message`` to gracefully handle unknown types.

    The upstream implementation raises ``MessageParseError`` for types it does
    not recognise (e.g. ``rate_limit_event``). Because ``parse_message`` is
    called inside the ``process_query`` async generator, the exception kills
    the generator and the underlying subprocess transport. This wrapper
    catches the error and returns a ``SystemMessage`` placeholder so the
    stream stays alive.

    Args:
        data (dict[str, Any]): Raw message dict from the CLI stream.

    Returns:
        Message: The parsed message, or a SystemMessage placeholder for unknown types.
    """

    try:
        return _original_parse_message(data)
    except MessageParseError:
        is_dict: bool = isinstance(data, dict)
        message_type: str = data.get("type", "unknown") if is_dict else "unknown"

        logger.debug("SDK ignoring unknown message type: %s", message_type)

        return SystemMessage(
            subtype=message_type,
            data=data if is_dict else {},
        )


_sdk_parser.parse_message = _patched_parse_message
_sdk_client.parse_message = _patched_parse_message  # type: ignore[attr-defined]


async def run_agent_cli(
    prompt: str,
    cwd: Path,
    model: str = "",
    max_turns: int = 0,
    cli_path: str = "",
    conversation_id: str | None = None,
    system_prompt: str = "",
    permission_mode: Literal[
        "default",
        "acceptEdits",
        "plan",
        "bypassPermissions",
    ] = DEFAULT_PERMISSION_MODE,
    allowed_tools: list[str] | None = None,
) -> AgentResult:
    """
    Run the agent via the Claude Code CLI and return the result.

    Args:
        prompt (str): The user's prompt to pass to the agent.
        cwd (Path): Working directory for the agent.
        model (str): Optional model override (empty string to skip).
        max_turns (int): Maximum agentic turns (0 for unlimited).
        cli_path (str): Path to the agent CLI binary (empty to use bundled).
        conversation_id (str | None): Optional conversation ID to resume a
            previous conversation.
        system_prompt (str): Optional system prompt for the agent.
        permission_mode (str): Permission mode for the agent.
        allowed_tools (list[str] | None): Restrict which tools the agent may use.

    Returns:
        AgentResult: The parsed result from the agent.
    """

    options: ClaudeAgentOptions = ClaudeAgentOptions(
        permission_mode=permission_mode,
        allowed_tools=allowed_tools or [],
        cwd=cwd,
        model=model or None,
        max_turns=max_turns if max_turns > 0 else None,
        cli_path=cli_path or None,
        resume=conversation_id,
        system_prompt=system_prompt or None,
    )

    result: AgentResult | None = None
    returned_conversation_id: str | None = None

    async for message in query(prompt=prompt, options=options):
        _log_message(message)

        if (
            isinstance(message, SystemMessage)
            and message.subtype == CONVERSATION_ID_INIT_SUBTYPE
        ):
            returned_conversation_id = message.data.get("session_id", None)

        if isinstance(message, ResultMessage):
            output: str = message.result or "Done, no output."
            returned_conversation_id = message.session_id or returned_conversation_id

            cli_cost: CostSummary | None = None
            usage_dict: dict[str, Any] | None = (
                message.usage if isinstance(message.usage, dict) else None
            )

            if message.total_cost_usd is not None or usage_dict:
                cli_cost = CostSummary(
                    total_input_tokens=(usage_dict or {}).get("input_tokens", 0),
                    total_output_tokens=(usage_dict or {}).get("output_tokens", 0),
                    total_cache_creation_tokens=(usage_dict or {}).get(
                        "cache_creation_input_tokens",
                        0,
                    ),
                    total_cache_read_tokens=(usage_dict or {}).get(
                        "cache_read_input_tokens",
                        0,
                    ),
                    total_cost_usd=message.total_cost_usd,
                    provider=ProviderName.ANTHROPIC,
                    model=options.model or "",
                )

            result = AgentResult(
                output=output,
                is_error=message.is_error,
                num_turns=message.num_turns,
                duration_ms=message.duration_ms,
                conversation_id=returned_conversation_id,
                cost=cli_cost,
            )

    if result is not None:
        return result

    return AgentResult(
        output="No result received from the agent.",
        is_error=True,
        num_turns=0,
        duration_ms=0,
        conversation_id=returned_conversation_id,
    )


def _log_message(message: Message) -> None:
    """
    Log an agent message at DEBUG level for auditing.

    Logs assistant text, thinking, tool calls, and tool results so the full
    agent conversation can be inspected when debug logging is enabled.

    Args:
        message (Message): The SDK message to log.
    """

    if not logger.isEnabledFor(logging.DEBUG):
        return

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                logger.debug("[assistant] %s", block.text)
            elif isinstance(block, ThinkingBlock):
                logger.debug("[thinking] %s", block.thinking)
            elif isinstance(block, ToolUseBlock):
                logger.debug("[tool_use] %s(%s)", block.name, block.input)

    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                content: str = str(block.content) if block.content else ""

                if len(content) > MAX_TOOL_RESULT_LOG_LENGTH:
                    content = content[:MAX_TOOL_RESULT_LOG_LENGTH] + "...(truncated)"

                logger.debug(
                    "[tool_result] %s error=%s %s",
                    block.tool_use_id,
                    block.is_error,
                    content,
                )
