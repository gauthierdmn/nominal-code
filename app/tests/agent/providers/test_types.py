# type: ignore
import pytest

from nominal_code.agent.providers.types import (
    LLMResponse,
    Message,
    ModelPricing,
    StopReason,
    TextBlock,
    TokenUsage,
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


class TestModelPricing:
    def test_create_with_defaults(self):
        pricing = ModelPricing(input_per_token=0.003, output_per_token=0.015)

        assert pricing.input_per_token == 0.003
        assert pricing.output_per_token == 0.015
        assert pricing.cache_write_per_token == 0.0
        assert pricing.cache_read_per_token == 0.0

    def test_create_with_cache_pricing(self):
        pricing = ModelPricing(
            input_per_token=0.003,
            output_per_token=0.015,
            cache_write_per_token=0.00375,
            cache_read_per_token=0.0003,
        )

        assert pricing.cache_write_per_token == 0.00375
        assert pricing.cache_read_per_token == 0.0003

    def test_is_frozen(self):
        pricing = ModelPricing(input_per_token=0.003, output_per_token=0.015)

        with pytest.raises(AttributeError):
            pricing.input_per_token = 0.005


class TestTokenUsageComputeCost:
    def test_base_cost(self):
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
        pricing = ModelPricing(input_per_token=3e-6, output_per_token=15e-6)

        cost = usage.compute_cost(pricing)

        assert cost == pytest.approx(3.0 + 15.0)

    def test_includes_cache_write_cost(self):
        usage = TokenUsage(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
        )
        pricing = ModelPricing(
            input_per_token=0.0,
            output_per_token=0.0,
            cache_write_per_token=3.75e-6,
        )

        cost = usage.compute_cost(pricing)

        assert cost == pytest.approx(3.75)

    def test_includes_cache_read_cost(self):
        usage = TokenUsage(
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
        )
        pricing = ModelPricing(
            input_per_token=0.0,
            output_per_token=0.0,
            cache_read_per_token=0.3e-6,
        )

        cost = usage.compute_cost(pricing)

        assert cost == pytest.approx(0.3)

    def test_zero_usage(self):
        usage = TokenUsage()
        pricing = ModelPricing(input_per_token=3e-6, output_per_token=15e-6)

        cost = usage.compute_cost(pricing)

        assert cost == 0.0

    def test_full_cost_with_all_fields(self):
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
        )
        pricing = ModelPricing(
            input_per_token=3e-6,
            output_per_token=15e-6,
            cache_write_per_token=3.75e-6,
            cache_read_per_token=0.3e-6,
        )

        cost = usage.compute_cost(pricing)

        assert cost == pytest.approx(3.0 + 15.0 + 3.75 + 0.3)
