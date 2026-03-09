# type: ignore
import builtins
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.llm.provider import MissingProviderError

pytest.importorskip("anthropic")

from nominal_code.llm.anthropic import (
    AnthropicProvider,
    _to_api_messages,
    _to_api_tools,
    _to_llm_response,
)
from nominal_code.llm.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.llm.provider import (
    ContextLengthError,
    ProviderError,
    RateLimitError,
)


class TestTokenUsageAdd:
    def test_sums_base_fields(self):
        first = TokenUsage(input_tokens=100, output_tokens=50)
        second = TokenUsage(input_tokens=200, output_tokens=100)

        result = first + second

        assert result.input_tokens == 300
        assert result.output_tokens == 150
        assert type(result) is TokenUsage

    def test_sums_cache_fields(self):
        first = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=10,
        )
        second = TokenUsage(
            input_tokens=200,
            output_tokens=100,
            cache_creation_input_tokens=30,
            cache_read_input_tokens=40,
        )

        result = first + second

        assert result.input_tokens == 300
        assert result.output_tokens == 150
        assert result.cache_creation_input_tokens == 50
        assert result.cache_read_input_tokens == 50

    def test_cache_defaults_to_zero(self):
        with_cache = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=10,
        )
        without_cache = TokenUsage(input_tokens=200, output_tokens=100)

        result = with_cache + without_cache

        assert result.input_tokens == 300
        assert result.output_tokens == 150
        assert result.cache_creation_input_tokens == 20
        assert result.cache_read_input_tokens == 10


class TestToApiMessages:
    def test_user_text_message(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hello")]),
        ]

        result = _to_api_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][0]["text"] == "hello"

    def test_assistant_text_message(self):
        messages = [
            Message(role="assistant", content=[TextBlock(text="hi there")]),
        ]

        result = _to_api_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["type"] == "text"

    def test_assistant_tool_use_message(self):
        messages = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="t1",
                        name="Read",
                        input={"file_path": "test.py"},
                    ),
                ],
            ),
        ]

        result = _to_api_messages(messages)

        assert result[0]["content"][0]["type"] == "tool_use"
        assert result[0]["content"][0]["id"] == "t1"
        assert result[0]["content"][0]["name"] == "Read"

    def test_user_tool_result_message(self):
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="t1",
                        content="file contents here",
                        is_error=False,
                    ),
                ],
            ),
        ]

        result = _to_api_messages(messages)

        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["type"] == "tool_result"
        assert result[0]["content"][0]["tool_use_id"] == "t1"

    def test_round_trip_conversation(self):
        messages = [
            Message(role="user", content=[TextBlock(text="read test.py")]),
            Message(
                role="assistant",
                content=[
                    TextBlock(text="Reading..."),
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "test.py"}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="t1",
                        content="print('hello')",
                    ),
                ],
            ),
            Message(
                role="assistant",
                content=[TextBlock(text="The file prints hello.")],
            ),
        ]

        result = _to_api_messages(messages)

        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "assistant"


class TestToApiTools:
    def test_convert_tool_definitions(self):
        tools: list[ToolDefinition] = [
            {
                "name": "Read",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
            },
        ]

        result = _to_api_tools(tools)

        assert len(result) == 1
        assert result[0]["name"] == "Read"
        assert result[0]["description"] == "Read a file"
        assert result[0]["input_schema"]["type"] == "object"


class TestToLlmResponse:
    def test_text_response(self):
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello!"
        mock_response.content = [text_block]
        mock_response.stop_reason = "end_turn"

        result = _to_llm_response(mock_response)

        assert isinstance(result, LLMResponse)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "Hello!"
        assert result.stop_reason == StopReason.END_TURN

    def test_tool_use_response(self):
        mock_response = MagicMock()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "t1"
        tool_block.name = "Read"
        tool_block.input = {"file_path": "test.py"}
        mock_response.content = [tool_block]
        mock_response.stop_reason = "tool_use"

        result = _to_llm_response(mock_response)

        assert len(result.content) == 1
        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].name == "Read"
        assert result.stop_reason == StopReason.TOOL_USE

    def test_max_tokens_stop_reason(self):
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.stop_reason = "max_tokens"

        result = _to_llm_response(mock_response)

        assert result.stop_reason == StopReason.MAX_TOKENS

    def test_unknown_stop_reason_defaults_to_end_turn(self):
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.stop_reason = "something_else"

        result = _to_llm_response(mock_response)

        assert result.stop_reason == StopReason.END_TURN


class TestToLlmResponseUsage:
    def test_extracts_anthropic_usage(self):
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello!"
        mock_response.content = [text_block]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.usage.cache_creation_input_tokens = 20
        mock_response.usage.cache_read_input_tokens = 10

        result = _to_llm_response(mock_response)

        assert result.usage is not None
        assert isinstance(result.usage, TokenUsage)
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50
        assert result.usage.cache_creation_input_tokens == 20
        assert result.usage.cache_read_input_tokens == 10

    def test_extracts_usage_with_null_cache_fields(self):
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.usage.cache_creation_input_tokens = None
        mock_response.usage.cache_read_input_tokens = None

        result = _to_llm_response(mock_response)

        assert result.usage is not None
        assert isinstance(result.usage, TokenUsage)
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50
        assert result.usage.cache_creation_input_tokens == 0
        assert result.usage.cache_read_input_tokens == 0

    def test_no_cache_fields_defaults_to_zero(self):
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 25
        mock_response.usage.cache_creation_input_tokens = None
        mock_response.usage.cache_read_input_tokens = None

        result = _to_llm_response(mock_response)

        assert result.usage is not None
        assert isinstance(result.usage, TokenUsage)
        assert result.usage.input_tokens == 50
        assert result.usage.output_tokens == 25
        assert result.usage.cache_creation_input_tokens == 0
        assert result.usage.cache_read_input_tokens == 0


class TestAnthropicProviderSend:
    @pytest.mark.asyncio
    async def test_send_returns_llm_response(self):
        provider = AnthropicProvider()

        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "result"
        mock_response.content = [text_block]
        mock_response.stop_reason = "end_turn"

        provider._client = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=mock_response)

        messages = [Message(role="user", content=[TextBlock(text="test")])]
        tools: list[ToolDefinition] = []

        result = await provider.send(
            messages=messages,
            system_prompt="Be helpful.",
            tools=tools,
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
        )

        assert isinstance(result, LLMResponse)
        assert result.content[0].text == "result"

    @pytest.mark.asyncio
    async def test_send_wraps_rate_limit_error(self):
        import anthropic

        provider = AnthropicProvider()
        provider._client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}

        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            ),
        )

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        with pytest.raises(RateLimitError):
            await provider.send(
                messages=messages,
                system_prompt="",
                tools=[],
                model="test",
                max_tokens=1024,
            )

    @pytest.mark.asyncio
    async def test_send_wraps_api_error(self):
        import anthropic

        provider = AnthropicProvider()
        provider._client = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {}

        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                message="server error",
                request=MagicMock(),
                body=None,
            ),
        )

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        with pytest.raises(ProviderError):
            await provider.send(
                messages=messages,
                system_prompt="",
                tools=[],
                model="test",
                max_tokens=1024,
            )

    @pytest.mark.asyncio
    async def test_send_wraps_context_length_error(self):
        import anthropic

        provider = AnthropicProvider()
        provider._client = MagicMock()

        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                message="context length exceeded",
                request=MagicMock(),
                body=None,
            ),
        )

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        with pytest.raises(ContextLengthError):
            await provider.send(
                messages=messages,
                system_prompt="",
                tools=[],
                model="test",
                max_tokens=1024,
            )


class TestAnthropicProviderMissingSdk:
    def test_init_raises_missing_provider_error_when_sdk_absent(self):
        real_import = builtins.__import__

        def _block_anthropic(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        cached_modules = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "anthropic" or key.startswith("anthropic.")
        }

        try:
            with patch("builtins.__import__", side_effect=_block_anthropic):
                with pytest.raises(MissingProviderError, match="anthropic"):
                    AnthropicProvider()
        finally:
            sys.modules.update(cached_modules)

    def test_missing_provider_error_includes_install_instructions(self):
        error = MissingProviderError(
            provider="anthropic",
            library="anthropic",
            instruction='pip install "nominal-code[anthropic]"',
        )

        assert "nominal-code[anthropic]" in str(error)
