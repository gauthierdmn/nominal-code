from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nominal_code.llm.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolChoice,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.llm.provider import (
    ContextLengthError,
    MissingProviderError,
    ProviderError,
    RateLimitError,
)
from nominal_code.llm.registry import INSTALL_INSTRUCTIONS
from nominal_code.models import ProviderName

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import (
        CacheControlEphemeralParam,
        MessageParam,
        TextBlockParam,
        ToolChoiceAnyParam,
        ToolChoiceAutoParam,
        ToolParam,
        ToolResultBlockParam,
        ToolUseBlockParam,
    )
    from anthropic.types import (
        Message as AnthropicMessage,
    )

logger: logging.Logger = logging.getLogger(__name__)

STOP_REASON_MAP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
}


class AnthropicProvider:
    """
    LLM provider for the Anthropic Messages API.

    Attributes:
        _client (anthropic.AsyncAnthropic): The async Anthropic client.
    """

    def __init__(self) -> None:
        """
        Initialize the Anthropic provider.

        Raises:
            MissingProviderError: If the ``anthropic`` package is not installed.
        """

        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise MissingProviderError(
                provider=ProviderName.ANTHROPIC,
                library="anthropic",
                instruction=INSTALL_INSTRUCTIONS[ProviderName.ANTHROPIC],
            ) from exc

        self._client: anthropic.AsyncAnthropic = _anthropic.AsyncAnthropic()

    async def close(self) -> None:
        """
        Close the underlying Anthropic HTTP client.
        """

        await self._client.close()

    async def send(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[ToolDefinition],
        model: str,
        max_tokens: int,
        previous_response_id: str | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> LLMResponse:
        """
        Send a request to the Anthropic Messages API.

        Args:
            messages (list[Message]): The conversation history.
            system_prompt (str): The system prompt.
            tools (list[ToolDefinition]): Available tool definitions.
            model (str): The Anthropic model identifier.
            max_tokens (int): Maximum tokens in the response.
            previous_response_id (str | None): Ignored. Anthropic does not
                support server-side conversation chaining.
            tool_choice (ToolChoice | None): Controls whether the model
                must use tools. ``None`` uses the provider default.

        Returns:
            LLMResponse: The model's response in canonical format.

        Raises:
            ProviderError: On API failures.
            RateLimitError: On rate limit errors.
            ContextLengthError: On context window exceeded.
        """

        import anthropic

        api_messages: list[MessageParam] = _to_api_messages(messages=messages)
        api_tools: list[ToolParam] = _to_api_tools(tools=tools)
        cache: CacheControlEphemeralParam = {"type": "ephemeral"}
        try:
            if tool_choice is not None:
                api_tool_choice: ToolChoiceAnyParam | ToolChoiceAutoParam = (
                    _map_tool_choice(tool_choice)
                )

                response: anthropic.types.Message = (
                    await self._client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        messages=api_messages,
                        cache_control=cache,
                        system=system_prompt,
                        tools=api_tools,
                        tool_choice=api_tool_choice,
                    )
                )
            else:
                response = await self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=api_messages,
                    cache_control=cache,
                    system=system_prompt,
                    tools=api_tools,
                )
        except anthropic.RateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except anthropic.APIError as exc:
            if "context length" in str(exc).lower():
                raise ContextLengthError(str(exc)) from exc

            raise ProviderError(exc.message) from exc

        return _to_llm_response(response=response)


def _map_tool_choice(
    tool_choice: ToolChoice,
) -> ToolChoiceAnyParam | ToolChoiceAutoParam:
    """
    Map a canonical ``ToolChoice`` to the Anthropic API type.

    Args:
        tool_choice (ToolChoice): The canonical tool choice.

    Returns:
        ToolChoiceAnyParam | ToolChoiceAutoParam: The Anthropic API
            tool choice parameter.
    """

    if tool_choice == ToolChoice.REQUIRED:
        result: ToolChoiceAnyParam = {"type": "any"}

        return result

    result_auto: ToolChoiceAutoParam = {"type": "auto"}

    return result_auto


def _to_api_messages(messages: list[Message]) -> list[MessageParam]:
    """
    Convert canonical messages to Anthropic MessageParam format.

    Args:
        messages (list[Message]): Canonical messages.

    Returns:
        list[MessageParam]: Anthropic API message params.
    """

    from anthropic.types import (
        TextBlockParam,
        ToolResultBlockParam,
        ToolUseBlockParam,
    )

    api_messages: list[MessageParam] = []

    for message in messages:
        if message.role == "assistant":
            blocks: list[TextBlockParam | ToolUseBlockParam] = []

            for block in message.content:
                if isinstance(block, TextBlock):
                    blocks.append(TextBlockParam(type="text", text=block.text))
                elif isinstance(block, ToolUseBlock):
                    blocks.append(
                        ToolUseBlockParam(
                            type="tool_use",
                            id=block.id,
                            name=block.name,
                            input=block.input,
                        ),
                    )

            api_messages.append({"role": "assistant", "content": blocks})
        else:
            user_content: list[TextBlockParam | ToolResultBlockParam] = []

            for block in message.content:
                if isinstance(block, TextBlock):
                    user_content.append(
                        TextBlockParam(type="text", text=block.text),
                    )
                elif isinstance(block, ToolResultBlock):
                    user_content.append(
                        ToolResultBlockParam(
                            type="tool_result",
                            tool_use_id=block.tool_use_id,
                            content=block.content,
                            is_error=block.is_error,
                        ),
                    )

            api_messages.append({"role": "user", "content": user_content})

    return api_messages


def _to_api_tools(tools: list[ToolDefinition]) -> list[ToolParam]:
    """
    Convert canonical tool definitions to Anthropic ToolParam format.

    Args:
        tools (list[ToolDefinition]): Canonical tool definitions.

    Returns:
        list[ToolParam]: Anthropic API tool params.
    """

    from anthropic.types import ToolParam

    api_tools: list[ToolParam] = []

    for tool in tools:
        api_tools.append(
            ToolParam(
                name=tool["name"],
                description=tool["description"],
                input_schema=tool["input_schema"],
            ),
        )

    return api_tools


def _to_llm_response(response: AnthropicMessage) -> LLMResponse:
    """
    Convert an Anthropic response to canonical LLMResponse.

    Args:
        response (AnthropicMessage): The Anthropic API response.

    Returns:
        LLMResponse: Canonical response.
    """

    content: list[TextBlock | ToolUseBlock] = []

    for block in response.content:
        if block.type == "text":
            content.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            raw_input: dict[str, Any] = (
                block.input if isinstance(block.input, dict) else {}
            )
            content.append(
                ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=raw_input,
                ),
            )

    stop_reason: StopReason = STOP_REASON_MAP.get(
        response.stop_reason or "",
        StopReason.END_TURN,
    )

    usage: TokenUsage = TokenUsage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
    )

    return LLMResponse(content=content, stop_reason=stop_reason, usage=usage)
