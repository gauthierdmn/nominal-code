from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

from nominal_code.agent.providers.base import (
    ContextLengthError,
    MissingProviderError,
    ProviderError,
    RateLimitError,
)
from nominal_code.agent.providers.registry import INSTALL_INSTRUCTIONS
from nominal_code.agent.providers.types import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.models import ProviderName

if TYPE_CHECKING:
    import openai
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionAssistantMessageParam,
        ChatCompletionMessage,
        ChatCompletionMessageParam,
        ChatCompletionToolParam,
    )
    from openai.types.chat.chat_completion import Choice

logger: logging.Logger = logging.getLogger(__name__)


class OpenAIProvider:
    """
    LLM provider for OpenAI-compatible APIs.

    Works with OpenAI, DeepSeek, Groq, Together, Fireworks, and any other
    provider that implements the OpenAI chat completions API.

    Attributes:
        _client (openai.AsyncOpenAI): The async OpenAI client.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
    ) -> None:
        """
        Initialize the OpenAI-compatible provider.

        Args:
            api_key (str): The API key. Empty string uses the SDK default
                (``OPENAI_API_KEY`` env var).
            base_url (str | None): Optional base URL for the API endpoint.
                None uses the default OpenAI endpoint.

        Raises:
            MissingProviderError: If the ``openai`` package is not installed.
        """

        try:
            import openai as _openai
        except ImportError as exc:
            raise MissingProviderError(
                ProviderName.OPENAI,
                "openai",
                INSTALL_INSTRUCTIONS[ProviderName.OPENAI],
            ) from exc

        self._client: openai.AsyncOpenAI = _openai.AsyncOpenAI(
            api_key=api_key or None,
            base_url=base_url,
        )

    async def send(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[ToolDefinition],
        model: str,
        max_tokens: int,
    ) -> LLMResponse:
        """
        Send a request to an OpenAI-compatible chat completions API.

        Args:
            messages (list[Message]): The conversation history.
            system_prompt (str): The system prompt.
            tools (list[ToolDefinition]): Available tool definitions.
            model (str): The model identifier.
            max_tokens (int): Maximum tokens in the response.

        Returns:
            LLMResponse: The model's response in canonical format.

        Raises:
            ProviderError: On API failures.
            RateLimitError: On rate limit errors.
            ContextLengthError: On context window exceeded.
        """

        import openai

        api_messages: list[ChatCompletionMessageParam] = _to_api_messages(
            messages,
            system_prompt,
        )
        api_tools: list[ChatCompletionToolParam] = _to_api_tools(tools)

        try:
            if api_tools:
                response = await self._client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=api_messages,
                    tools=api_tools,
                )
            else:
                response = await self._client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=api_messages,
                )
        except openai.RateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except openai.APIError as exc:
            error_message: str = str(exc)

            if "context length" in error_message.lower():
                raise ContextLengthError(error_message) from exc

            raise ProviderError(error_message) from exc

        return _to_llm_response(response)


def _to_api_messages(
    messages: list[Message],
    system_prompt: str,
) -> list[ChatCompletionMessageParam]:
    """
    Convert canonical messages to OpenAI chat message format.

    Args:
        messages (list[Message]): Canonical messages.
        system_prompt (str): The system prompt (prepended as a system message).

    Returns:
        list[ChatCompletionMessageParam]: OpenAI API messages.
    """

    api_messages: list[ChatCompletionMessageParam] = []

    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})

    for message in messages:
        if message.role == "user":
            has_tool_results: bool = any(
                isinstance(block, ToolResultBlock) for block in message.content
            )

            if has_tool_results:
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        api_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.tool_use_id,
                                "content": block.content,
                            },
                        )
                    elif isinstance(block, TextBlock):
                        api_messages.append(
                            {"role": "user", "content": block.text},
                        )
            else:
                text_parts: list[str] = [
                    block.text
                    for block in message.content
                    if isinstance(block, TextBlock)
                ]
                api_messages.append(
                    {"role": "user", "content": "\n".join(text_parts)},
                )
        else:
            tool_calls: list[dict[str, Any]] = []
            text_parts = []

            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input),
                            },
                        },
                    )

            assistant_msg: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            }

            if tool_calls:
                assistant_msg["tool_calls"] = cast(Any, tool_calls)

            api_messages.append(assistant_msg)

    return api_messages


def _to_api_tools(
    tools: list[ToolDefinition],
) -> list[ChatCompletionToolParam]:
    """
    Convert canonical tool definitions to OpenAI function tool format.

    Args:
        tools (list[ToolDefinition]): Canonical tool definitions.

    Returns:
        list[ChatCompletionToolParam]: OpenAI API tool params.
    """

    api_tools: list[ChatCompletionToolParam] = []

    for tool in tools:
        api_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            },
        )

    return api_tools


def _to_llm_response(response: ChatCompletion) -> LLMResponse:
    """
    Convert an OpenAI chat completion response to canonical LLMResponse.

    Args:
        response (ChatCompletion): The OpenAI chat completion response.

    Returns:
        LLMResponse: Canonical response.
    """

    choice: Choice = response.choices[0]
    response_message: ChatCompletionMessage = choice.message
    content: list[TextBlock | ToolUseBlock] = []

    if response_message.content:
        content.append(TextBlock(text=response_message.content))

    if response_message.tool_calls:
        from openai.types.chat import ChatCompletionMessageToolCall

        for tool_call in response_message.tool_calls:
            if not isinstance(tool_call, ChatCompletionMessageToolCall):
                continue

            arguments: dict[str, Any] = {}

            try:
                arguments = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Failed to parse tool call arguments for %s",
                    tool_call.function.name,
                )

            content.append(
                ToolUseBlock(
                    id=tool_call.id,
                    name=tool_call.function.name,
                    input=arguments,
                ),
            )

    stop_reason: StopReason
    finish_reason: str | None = choice.finish_reason

    if finish_reason == "tool_calls":
        stop_reason = StopReason.TOOL_USE
    elif finish_reason == "length":
        stop_reason = StopReason.MAX_TOKENS
    else:
        stop_reason = StopReason.END_TURN

    return LLMResponse(content=content, stop_reason=stop_reason)
