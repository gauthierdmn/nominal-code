from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
from pathlib import Path

from nominal_code.agent.api.tools import (
    AGENT_TOOL_NAME,
    SUBMIT_REVIEW_TOOL_NAME,
    build_agent_tool,
    execute_tool,
    get_tool_definitions,
)
from nominal_code.agent.compaction import compact_with_notes
from nominal_code.agent.result import AgentResult
from nominal_code.agent.sub_agent import SubAgentConfig
from nominal_code.conversation.base import truncate_messages
from nominal_code.llm.cost import CostSummary, build_cost_summary
from nominal_code.llm.messages import (
    ContentBlock,
    LLMResponse,
    Message,
    TextBlock,
    TokenUsage,
    ToolChoice,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.llm.provider import LLMProvider, ProviderError
from nominal_code.models import ProviderName

MAX_RESPONSE_TOKENS: int = 16384
COMPACTION_TOKEN_THRESHOLD: int = 100_000
NOTES_HEADER_TEMPLATE: str = "# Notes: {label}\n\n---\n\n"
NOTES_FILE_EXTENSION: str = ".md"
SUB_AGENT_TEMP_DIR_PREFIX: str = "nominal-subagent-"

LAST_TURN_WARNING: str = (
    "IMPORTANT: This is your last turn. You MUST call submit_review now "
    "with your findings. Do not call any other tools."
)

logger: logging.Logger = logging.getLogger(__name__)


async def run_api_agent(
    prompt: str,
    cwd: Path,
    model: str,
    provider: LLMProvider,
    provider_name: ProviderName,
    max_turns: int = 0,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    prior_messages: list[Message] | None = None,
    notes_file_path: Path | None = None,
    tool_choice: ToolChoice | None = None,
    sub_agent_configs: dict[str, SubAgentConfig] | None = None,
) -> AgentResult:
    """
    Run the agent using an LLM provider with tool use.

    Implements the agentic loop: sends a prompt, processes tool_use
    responses by executing tools locally, sends results back, and repeats
    until the model produces a final text answer or max_turns is reached.

    When ``submit_review`` is in ``allowed_tools``, the corresponding
    structured-output tool is included. If the model calls it, the tool
    input is serialized as JSON and returned as the agent output.

    When ``sub_agent_configs`` is provided, the ``Agent`` tool is added
    to the tool list. The model can spawn sub-agents by type, and their
    notes are returned as tool results.

    When ``notes_file_path`` is provided, the agent can write findings
    via the WriteNotes tool, and messages are periodically compacted
    using the notes content as the summary.

    On the last turn (when ``max_turns`` is set), a warning is injected
    instructing the model to call ``submit_review`` immediately. If
    ``max_turns`` is reached without ``submit_review``, the result has
    ``exhausted_without_review=True``.

    Args:
        prompt (str): The user's prompt to pass to the agent.
        cwd (Path): Working directory for tool execution.
        model (str): The model to use.
        provider (LLMProvider): The LLM provider to use for API calls.
        provider_name (ProviderName): Provider identifier for cost tracking.
        max_turns (int): Maximum agentic turns (0 for unlimited).
        system_prompt (str): Optional system prompt for the agent.
        allowed_tools (list[str] | None): Restrict which tools the agent
            may use.
        prior_messages (list[Message] | None): Prior conversation messages
            for multi-turn continuity. Prepended before the new user message.
        notes_file_path (Path | None): Pre-assigned file path for the
            WriteNotes tool. When provided, enables both note-writing
            and notes-based compaction.
        tool_choice (ToolChoice | None): Controls whether the model must
            use tools. When ``REQUIRED``, the model must call a tool on
            its first response.
        sub_agent_configs (dict[str, SubAgentConfig] | None): Mapping
            of sub-agent type names to their configs. When provided,
            the ``Agent`` tool is added to the tool list.

    Returns:
        AgentResult: The parsed result from the agent.
    """

    tool_definitions: list[ToolDefinition] = get_tool_definitions(
        allowed_tools=allowed_tools,
    )

    if sub_agent_configs:
        descriptions: dict[str, str] = {
            name: config.description for name, config in sub_agent_configs.items()
        }
        tool_definitions.append(build_agent_tool(descriptions))

    has_submit_review: bool = any(
        tool["name"] == SUBMIT_REVIEW_TOOL_NAME for tool in tool_definitions
    )

    messages: list[Message] = prior_messages or []
    messages.append(Message(role="user", content=[TextBlock(text=prompt)]))
    messages = truncate_messages(messages=messages)

    turns: int = 0
    start_time: int = _now_ms()
    conversation_id: str | None = None
    token_usage: TokenUsage | None = None
    api_call_count: int = 0
    collected_sub_agent_costs: list[CostSummary] = []

    try:
        while True:
            effective_tool_choice: ToolChoice | None = (
                tool_choice if turns == 0 else None
            )

            if has_submit_review and max_turns > 0 and turns == max_turns - 1:
                messages.append(
                    Message(
                        role="user",
                        content=[TextBlock(text=LAST_TURN_WARNING)],
                    ),
                )
                effective_tool_choice = ToolChoice.REQUIRED

            response: LLMResponse = await provider.send(
                messages=messages,
                system_prompt=system_prompt,
                tools=tool_definitions,
                model=model,
                max_tokens=MAX_RESPONSE_TOKENS,
                previous_response_id=conversation_id,
                tool_choice=effective_tool_choice,
            )
            conversation_id = response.response_id
            api_call_count += 1

            if response.usage is not None:
                token_usage = (
                    response.usage
                    if token_usage is None
                    else token_usage + response.usage
                )

            messages.append(
                Message(
                    role="assistant",
                    content=list(response.content),
                ),
            )

            tool_use_blocks: list[ToolUseBlock] = [
                block for block in response.content if isinstance(block, ToolUseBlock)
            ]

            if not tool_use_blocks:
                output: str = _extract_text(response=response)
                duration_ms: int = _now_ms() - start_time

                return AgentResult(
                    output=output or "Done, no output.",
                    is_error=False,
                    num_turns=turns,
                    duration_ms=duration_ms,
                    messages=tuple(messages),
                    conversation_id=conversation_id,
                    cost=build_cost_summary(
                        usage=token_usage,
                        model=model,
                        provider=provider_name,
                        num_api_calls=api_call_count,
                    ),
                    sub_agent_costs=tuple(collected_sub_agent_costs),
                )

            for block in tool_use_blocks:
                if block.name == SUBMIT_REVIEW_TOOL_NAME:
                    if len(tool_use_blocks) > 1:
                        logger.warning(
                            "submit_review called alongside "
                            "%d other tool(s); ignoring other calls",
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
                        cost=build_cost_summary(
                            usage=token_usage,
                            model=model,
                            provider=provider_name,
                            num_api_calls=api_call_count,
                        ),
                        sub_agent_costs=tuple(collected_sub_agent_costs),
                    )

            tool_results, sub_costs = await _dispatch_tools(
                tool_use_blocks=tool_use_blocks,
                sub_agent_configs=sub_agent_configs,
                cwd=cwd,
                allowed_tools=allowed_tools,
                notes_file_path=notes_file_path,
            )
            collected_sub_agent_costs.extend(sub_costs)

            messages.append(Message(role="user", content=tool_results))

            turns += 1

            context_window_tokens: int = (
                response.usage.input_tokens
                + response.usage.output_tokens
                + response.usage.cache_creation_input_tokens
                + response.usage.cache_read_input_tokens
                if response.usage
                else 0
            )

            if (
                notes_file_path is not None
                and context_window_tokens >= COMPACTION_TOKEN_THRESHOLD
            ):
                notes_for_compaction: str = ""

                if notes_file_path.exists():
                    notes_for_compaction = notes_file_path.read_text(
                        encoding="utf-8",
                    )

                compaction_result = compact_with_notes(
                    messages,
                    notes_for_compaction,
                )

                if compaction_result.summary_text:
                    messages = compaction_result.messages

                    logger.info(
                        "Compacted LLM context at %d context tokens",
                        context_window_tokens,
                    )

            if max_turns > 0 and turns >= max_turns:
                logger.warning(
                    "Agent reached max turns (%d), stopping",
                    max_turns,
                )

                output = _extract_last_text(messages=messages)
                duration_ms = _now_ms() - start_time

                return AgentResult(
                    output=output or "Max turns reached.",
                    is_error=False,
                    num_turns=turns,
                    duration_ms=duration_ms,
                    messages=tuple(messages),
                    conversation_id=conversation_id,
                    cost=build_cost_summary(
                        usage=token_usage,
                        model=model,
                        provider=provider_name,
                        num_api_calls=api_call_count,
                    ),
                    exhausted_without_review=has_submit_review,
                    sub_agent_costs=tuple(collected_sub_agent_costs),
                )

    except ProviderError as exc:
        duration_ms = _now_ms() - start_time

        logger.exception("LLM provider error")

        return AgentResult(
            output=f"API error: {exc}",
            is_error=True,
            num_turns=turns,
            duration_ms=duration_ms,
            cost=build_cost_summary(
                usage=token_usage,
                model=model,
                provider=provider_name,
                num_api_calls=api_call_count,
            ),
        )

    except Exception as exc:
        duration_ms = _now_ms() - start_time

        logger.exception("Unexpected error in API runner")

        return AgentResult(
            output=f"Unexpected error: {exc}",
            is_error=True,
            num_turns=turns,
            duration_ms=duration_ms,
            cost=build_cost_summary(
                usage=token_usage,
                model=model,
                provider=provider_name,
                num_api_calls=api_call_count,
            ),
        )


async def _dispatch_tools(
    tool_use_blocks: list[ToolUseBlock],
    sub_agent_configs: dict[str, SubAgentConfig] | None,
    cwd: Path,
    allowed_tools: list[str] | None,
    notes_file_path: Path | None,
) -> tuple[list[ContentBlock], list[CostSummary]]:
    """
    Dispatch tool calls, running Agent calls concurrently.

    Agent tool calls are kicked off as tasks up front via
    ``asyncio.gather``, then all blocks are iterated in order —
    agent results come from the completed tasks, other tools run
    sequentially. Results are returned in the original tool call order.

    Args:
        tool_use_blocks (list[ToolUseBlock]): Tool calls from the model.
        sub_agent_configs (dict[str, SubAgentConfig] | None): Available
            sub-agent configurations.
        cwd (Path): Working directory for tool execution.
        allowed_tools (list[str] | None): Allowed tool names/patterns.
        notes_file_path (Path | None): Notes file for WriteNotes.

    Returns:
        tuple[list[ContentBlock], list[CostSummary]]: Tool result blocks
            in original call order, and cost summaries from any
            sub-agents that ran.
    """

    agent_tasks: dict[str, asyncio.Task[tuple[str, bool, CostSummary | None]]] = {}

    for block in tool_use_blocks:
        logger.debug("[tool_use] %s(%s)", block.name, block.input)

        if block.name == AGENT_TOOL_NAME and sub_agent_configs:
            agent_tasks[block.id] = asyncio.create_task(
                _handle_agent_tool(
                    tool_input=block.input,
                    sub_agent_configs=sub_agent_configs,
                    cwd=cwd,
                ),
            )

    if agent_tasks:
        await asyncio.gather(*agent_tasks.values())

    results: list[ContentBlock] = []
    sub_agent_costs: list[CostSummary] = []

    for block in tool_use_blocks:
        if block.id in agent_tasks:
            tool_output, is_error, cost = agent_tasks[block.id].result()

            if cost is not None:
                sub_agent_costs.append(cost)
        else:
            tool_output, is_error = await execute_tool(
                name=block.name,
                tool_input=block.input,
                cwd=cwd,
                allowed_tools=allowed_tools,
                notes_file_path=notes_file_path,
            )

        logger.debug(
            "[tool_result] %s error=%s %.500s",
            block.id,
            is_error,
            tool_output,
        )

        results.append(
            ToolResultBlock(
                tool_use_id=block.id,
                content=tool_output,
                is_error=is_error,
            ),
        )

    return results, sub_agent_costs


async def _handle_agent_tool(
    tool_input: dict[str, object],
    sub_agent_configs: dict[str, SubAgentConfig],
    cwd: Path,
) -> tuple[str, bool, CostSummary | None]:
    """
    Handle an Agent tool call by spawning a sub-agent.

    Validates the requested type and prompt, creates a temporary notes
    directory, runs ``run_api_agent`` with the config's provider/model/tools,
    and returns the notes content along with cost information.

    Args:
        tool_input (dict[str, object]): The tool call input with
            ``subagent_type`` and ``prompt`` fields.
        sub_agent_configs (dict[str, SubAgentConfig]): Available
            sub-agent configurations.
        cwd (Path): Working directory for tool execution.

    Returns:
        tuple[str, bool, CostSummary | None]: The tool output, error
            flag, and cost summary from the sub-agent.
    """

    agent_type: str = str(tool_input.get("subagent_type", ""))
    agent_prompt: str = str(tool_input.get("prompt", ""))

    config: SubAgentConfig | None = sub_agent_configs.get(agent_type)

    if config is None:
        return f"Unknown sub-agent type: {agent_type}", True, None

    if not agent_prompt:
        return "prompt is required", True, None

    safe_label: str = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_type)
    notes_dir: Path = Path(
        tempfile.mkdtemp(prefix=f"{SUB_AGENT_TEMP_DIR_PREFIX}{safe_label}-"),
    )
    notes_file_path: Path = notes_dir / f"{safe_label}{NOTES_FILE_EXTENSION}"
    notes_file_path.write_text(
        NOTES_HEADER_TEMPLATE.format(label=agent_type),
        encoding="utf-8",
    )

    logger.info(
        "Starting sub-agent '%s': %d turns",
        agent_type,
        config.max_turns,
    )

    try:
        result: AgentResult = await run_api_agent(
            prompt=agent_prompt,
            cwd=cwd,
            model=config.model,
            provider=config.provider,
            max_turns=config.max_turns,
            system_prompt=config.system_prompt,
            allowed_tools=config.allowed_tools,
            provider_name=config.provider_name,
            notes_file_path=notes_file_path,
        )
    except Exception as exc:
        logger.exception("Sub-agent '%s' failed", agent_type)

        return f"Sub-agent error: {exc}", True, None

    notes: str = ""

    if notes_file_path.exists():
        notes = notes_file_path.read_text(encoding="utf-8")

    logger.info(
        "Sub-agent '%s' complete: %d turns, %d chars of notes",
        agent_type,
        result.num_turns,
        len(notes),
    )

    output: str = notes or result.output or "No findings."

    return output, False, result.cost


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
        messages (list[Message]): The message history.

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
