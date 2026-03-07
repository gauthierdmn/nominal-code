from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypedDict


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
        role (Literal["user", "assistant"]): The message sender role.
        content (list[ContentBlock]): The content blocks in the message.
    """

    role: Literal["user", "assistant"]
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
class LLMResponse:
    """
    The result of a single LLM API call.

    Attributes:
        content (list[TextBlock | ToolUseBlock]): Response content blocks.
        stop_reason (StopReason): Why the model stopped generating.
        response_id (str | None): Provider-assigned response ID for
            conversation continuity (e.g. OpenAI Responses API). ``None``
            when the provider does not support server-side chaining.
    """

    content: list[TextBlock | ToolUseBlock]
    stop_reason: StopReason
    response_id: str | None = None
