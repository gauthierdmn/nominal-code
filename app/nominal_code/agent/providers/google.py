from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
    from google.genai import types as genai_types

logger: logging.Logger = logging.getLogger(__name__)

STOP_REASON_MAP: dict[str, StopReason] = {
    "STOP": StopReason.END_TURN,
    "MAX_TOKENS": StopReason.MAX_TOKENS,
}


class GoogleProvider:
    """
    LLM provider for the Google Gemini API.

    Attributes:
        _client (genai.Client): The Google GenAI client.
    """

    def __init__(self) -> None:
        """
        Initialize the Google Gemini provider.

        Raises:
            MissingProviderError: If the ``google-genai`` package is not
                installed.
        """

        try:
            from google import genai
        except ImportError as exc:
            raise MissingProviderError(
                ProviderName.GOOGLE,
                "google-genai",
                INSTALL_INSTRUCTIONS[ProviderName.GOOGLE],
            ) from exc

        self._client: genai.Client = genai.Client()

    async def send(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[ToolDefinition],
        model: str,
        max_tokens: int,
        previous_response_id: str | None = None,
    ) -> LLMResponse:
        """
        Send a request to the Google Gemini API.

        Args:
            messages (list[Message]): The conversation history.
            system_prompt (str): The system prompt.
            tools (list[ToolDefinition]): Available tool definitions.
            model (str): The Gemini model identifier.
            max_tokens (int): Maximum tokens in the response.
            previous_response_id (str | None): Ignored. Gemini does not
                support server-side conversation chaining.

        Returns:
            LLMResponse: The model's response in canonical format.

        Raises:
            ProviderError: On API failures.
            RateLimitError: On rate limit errors.
            ContextLengthError: On context window exceeded.
        """

        from google.genai import errors as genai_errors
        from google.genai import types as genai_types

        api_contents: list[genai_types.Content] = _to_api_contents(messages)
        api_tools: genai_types.Tool | None = _to_api_tools(tools)

        config: genai_types.GenerateContentConfig = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            tools=[api_tools] if api_tools else None,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=api_contents,
                config=config,
            )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise RateLimitError(str(exc)) from exc

            error_message: str = str(exc).lower()

            if "token limit" in error_message or "context length" in error_message:
                raise ContextLengthError(str(exc)) from exc

            raise ProviderError(str(exc)) from exc
        except genai_errors.ServerError as exc:
            raise ProviderError(str(exc)) from exc

        return _to_llm_response(response)


def _to_api_contents(messages: list[Message]) -> list[genai_types.Content]:
    """
    Convert canonical messages to Gemini Content list.

    Args:
        messages (list[Message]): Canonical messages.

    Returns:
        list[genai_types.Content]: Gemini API content objects.
    """

    from google.genai import types as genai_types

    id_to_name: dict[str, str] = {}

    for message in messages:
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                id_to_name[block.id] = block.name

    contents: list[genai_types.Content] = []

    for message in messages:
        role: str = "model" if message.role == "assistant" else "user"
        parts: list[genai_types.Part] = []

        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(genai_types.Part.from_text(text=block.text))
            elif isinstance(block, ToolUseBlock):
                parts.append(
                    genai_types.Part.from_function_call(
                        name=block.name,
                        args=block.input,
                    ),
                )
            elif isinstance(block, ToolResultBlock):
                function_name: str = id_to_name.get(
                    block.tool_use_id,
                    block.tool_use_id,
                )
                result_key: str = "error" if block.is_error else "result"
                parts.append(
                    genai_types.Part.from_function_response(
                        name=function_name,
                        response={result_key: block.content},
                    ),
                )
                    ),
                )

        if parts:
            contents.append(genai_types.Content(role=role, parts=parts))

    return contents


def _to_api_tools(
    tools: list[ToolDefinition],
) -> genai_types.Tool | None:
    """
    Convert canonical tool definitions to a Gemini Tool object.

    Args:
        tools (list[ToolDefinition]): Canonical tool definitions.

    Returns:
        genai_types.Tool | None: Gemini tool object, or ``None`` if no
            tools are provided.
    """

    if not tools:
        return None

    from google.genai import types as genai_types

    declarations: list[genai_types.FunctionDeclaration] = []

    for tool in tools:
        declarations.append(
            genai_types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters_json_schema=tool["input_schema"],
            ),
        )

    return genai_types.Tool(function_declarations=declarations)


def _to_llm_response(
    response: genai_types.GenerateContentResponse,
) -> LLMResponse:
    """
    Convert a Gemini response to canonical LLMResponse.

    Args:
        response (genai_types.GenerateContentResponse): The Gemini API
            response.

    Returns:
        LLMResponse: Canonical response.
    """

    candidates: list[genai_types.Candidate] = response.candidates or []

    if not candidates:
        return LLMResponse(content=[], stop_reason=StopReason.END_TURN)

    candidate: genai_types.Candidate = candidates[0]
    parts: list[genai_types.Part] = (
        candidate.content.parts if candidate.content else []
    ) or []
    content: list[TextBlock | ToolUseBlock] = []
    has_tool_calls: bool = False

    for index, part in enumerate(parts):
        if part.function_call is not None:
            call_args: dict[str, Any] = dict(part.function_call.args or {})
            content.append(
                ToolUseBlock(
                    id=f"call_{index}",
                    name=part.function_call.name or "",
                    input=call_args,
                ),
            )
            has_tool_calls = True
        elif part.text is not None:
            content.append(TextBlock(text=part.text))

    if has_tool_calls:
        stop_reason: StopReason = StopReason.TOOL_USE
    else:
        finish_reason: str = str(candidate.finish_reason or "")
        stop_reason = STOP_REASON_MAP.get(finish_reason, StopReason.END_TURN)

    return LLMResponse(content=content, stop_reason=stop_reason)
