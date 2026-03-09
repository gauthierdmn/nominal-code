from __future__ import annotations

from typing import Protocol

from nominal_code.llm.messages import (
    LLMResponse,
    Message,
    ToolDefinition,
)


class ProviderError(Exception):
    """
    Base error for all LLM provider failures.
    """


class RateLimitError(ProviderError):
    """
    Raised when the provider returns a rate limit error.
    """


class ContextLengthError(ProviderError):
    """
    Raised when the context window is exceeded.
    """


class MissingProviderError(ProviderError):
    """
    Raised when a provider's SDK is not installed.
    """

    def __init__(self, provider: str, library: str, instruction: str) -> None:
        """
        Initialize the error with install instructions.

        Args:
            provider (str): The provider name.
            library (str): The missing library name.
            instruction (str): The install command to show the user.
        """

        super().__init__(
            f"Provider {provider!r} requires the {library!r} package, "
            f"but it is not installed.\n\n    $ {instruction}\n",
        )


class LLMProvider(Protocol):
    """
    Protocol for LLM providers.

    Implementations convert between canonical types and their native SDK,
    then make the API call.
    """

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
        Send a request to the LLM and return the response.

        Providers that support server-side conversation chaining (e.g.
        OpenAI Responses API) use ``previous_response_id`` to resume a
        conversation and return the new ID in ``LLMResponse.response_id``.
        Providers that don't support this simply ignore the parameter and
        return ``None`` for ``response_id``.

        Args:
            messages (list[Message]): The conversation history.
            system_prompt (str): The system prompt.
            tools (list[ToolDefinition]): Available tool definitions.
            model (str): The model identifier.
            max_tokens (int): Maximum tokens in the response.
            previous_response_id (str | None): Provider response ID from
                the previous turn for server-side continuity. ``None``
                when not applicable.

        Returns:
            LLMResponse: The model's response.

        Raises:
            ProviderError: On API failures.
            RateLimitError: On rate limit errors.
            ContextLengthError: On context window exceeded.
        """

        ...

    async def close(self) -> None:
        """
        Release resources held by the provider.

        Default implementation is a no-op. Providers that hold open
        connections (e.g. Google GenAI) should override this to clean up.
        """

        ...
