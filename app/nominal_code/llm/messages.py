from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Literal, TypedDict


class ToolChoice(StrEnum):
    """
    Controls whether the model must use tools.

    Attributes:
        AUTO: The model decides whether to call a tool or return text.
        REQUIRED: The model must call at least one tool.
    """

    AUTO = "auto"
    REQUIRED = "required"


class StopReason(Enum):
    """
    Reason why the model stopped generating.

    Attributes:
        END_TURN: The model finished its response naturally.
        TOOL_USE: The model is requesting one or more tool calls.
        MAX_TOKENS: The response was truncated due to token limits.
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"


@dataclass(frozen=True)
class TextBlock:
    """
    A text content block from the model.

    Attributes:
        text (str): The text content.
    """

    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    """
    A tool call request from the model.

    Attributes:
        id (str): Unique identifier for this tool call.
        name (str): The tool name to invoke.
        input (dict[str, Any]): The tool input parameters.
    """

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResultBlock:
    """
    The result of a tool execution sent back to the model.

    Attributes:
        tool_use_id (str): The ID of the tool call this result corresponds to.
        content (str): The tool output content.
        is_error (bool): Whether the tool execution failed.
    """

    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True)
class Message:
    """
    A conversation message with a role and content blocks.

    Attributes:
        role (Literal["user", "assistant", "system"]): The message sender role.
        content (list[ContentBlock]): The content blocks in the message.
    """

    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock] = field(default_factory=list)


class ToolDefinition(TypedDict):
    """
    A tool definition using JSON Schema for the input.

    Attributes:
        name (str): The tool name.
        description (str): A description of what the tool does.
        input_schema (dict[str, Any]): JSON Schema for the tool's input.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelPricing:
    """
    Per-token pricing for a model.

    All values are in dollars per token (not per million tokens).

    Attributes:
        input_per_token (float): Cost per input token.
        output_per_token (float): Cost per output token.
        cache_write_per_token (float): Cost per cache creation token.
        cache_read_per_token (float): Cost per cache read token.
    """

    input_per_token: float
    output_per_token: float
    cache_write_per_token: float = 0.0
    cache_read_per_token: float = 0.0


@dataclass(frozen=True)
class TokenUsage:
    """
    Token counts from a single LLM API call.

    Attributes:
        input_tokens (int): Number of input tokens.
        output_tokens (int): Number of output tokens.
        cache_creation_input_tokens (int): Tokens written to cache.
        cache_read_input_tokens (int): Tokens read from cache.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def compute_cost(self, pricing: ModelPricing) -> float:
        """
        Compute the dollar cost for this usage.

        Args:
            pricing (ModelPricing): Per-token pricing for the model.

        Returns:
            float: Cost in USD.
        """

        return (
            self.input_tokens * pricing.input_per_token
            + self.output_tokens * pricing.output_per_token
            + self.cache_creation_input_tokens * pricing.cache_write_per_token
            + self.cache_read_input_tokens * pricing.cache_read_per_token
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """
        Sum two TokenUsage instances.

        Args:
            other (TokenUsage): The other usage to add.

        Returns:
            TokenUsage: Combined usage.
        """

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
        )


@dataclass(frozen=True)
class LLMResponse:
    """
    The result of a single LLM API call.

    Attributes:
        content (list[TextBlock | ToolUseBlock]): Response content blocks.
        stop_reason (StopReason): Why the model stopped generating.
        response_id (str | None): Provider-assigned response ID for
            conversation continuity (e.g. OpenAI Responses API). ``None``
            when the provider does not support server-side chaining.
        usage (TokenUsage | None): Token usage for this call. ``None``
            when the provider does not report usage.
    """

    content: list[TextBlock | ToolUseBlock]
    stop_reason: StopReason
    response_id: str | None = None
    usage: TokenUsage | None = None
