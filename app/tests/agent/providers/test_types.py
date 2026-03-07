# type: ignore
import pytest

from nominal_code.agent.providers.types import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


class TestTextBlock:
    def test_create_text_block(self):
        block = TextBlock(text="hello")

        assert block.text == "hello"

    def test_text_block_is_frozen(self):
        block = TextBlock(text="hello")

        with pytest.raises(AttributeError):
            block.text = "changed"


class TestToolUseBlock:
    def test_create_tool_use_block(self):
        block = ToolUseBlock(
            id="tool-1",
            name="Read",
            input={"file_path": "test.py"},
        )

        assert block.id == "tool-1"
        assert block.name == "Read"
        assert block.input == {"file_path": "test.py"}


class TestToolResultBlock:
    def test_create_tool_result_block(self):
        block = ToolResultBlock(
            tool_use_id="tool-1",
            content="file contents",
        )

        assert block.tool_use_id == "tool-1"
        assert block.content == "file contents"
        assert block.is_error is False

    def test_create_tool_result_block_with_error(self):
        block = ToolResultBlock(
            tool_use_id="tool-1",
            content="File not found",
            is_error=True,
        )

        assert block.is_error is True


class TestMessage:
    def test_create_user_message(self):
        message = Message(
            role="user",
            content=[TextBlock(text="hello")],
        )

        assert message.role == "user"
        assert len(message.content) == 1

    def test_create_assistant_message_with_tool_use(self):
        message = Message(
            role="assistant",
            content=[
                TextBlock(text="I'll read that file"),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "a.py"}),
            ],
        )

        assert message.role == "assistant"
        assert len(message.content) == 2

    def test_default_content_is_empty(self):
        message = Message(role="user")

        assert message.content == []


class TestToolDefinition:
    def test_create_tool_definition(self):
        tool: ToolDefinition = {
            "name": "Read",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            },
        }

        assert tool["name"] == "Read"
        assert tool["input_schema"]["type"] == "object"


class TestLLMResponse:
    def test_create_response_with_text(self):
        response = LLMResponse(
            content=[TextBlock(text="done")],
            stop_reason=StopReason.END_TURN,
        )

        assert len(response.content) == 1
        assert response.stop_reason == StopReason.END_TURN

    def test_create_response_with_tool_use(self):
        response = LLMResponse(
            content=[
                TextBlock(text="reading"),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "a.py"}),
            ],
            stop_reason=StopReason.TOOL_USE,
        )

        assert len(response.content) == 2
        assert response.stop_reason == StopReason.TOOL_USE


class TestStopReason:
    def test_stop_reason_values(self):
        assert StopReason.END_TURN.value == "end_turn"
        assert StopReason.TOOL_USE.value == "tool_use"
        assert StopReason.MAX_TOKENS.value == "max_tokens"
