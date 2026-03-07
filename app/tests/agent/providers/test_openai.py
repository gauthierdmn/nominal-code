# type: ignore
import builtins
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.providers.base import MissingProviderError

pytest.importorskip("openai")

from nominal_code.agent.providers.openai import (
    OpenAIProvider,
    _to_api_messages,
    _to_api_tools,
    _to_llm_response,
)
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


class TestToApiMessages:
    def test_system_prompt_prepended(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hello")]),
        ]

        result = _to_api_messages(messages, "Be helpful.")

        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Be helpful."
        assert result[1]["role"] == "user"

    def test_no_system_prompt_when_empty(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hello")]),
        ]

        result = _to_api_messages(messages, "")

        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_user_text_message(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hello")]),
        ]

        result = _to_api_messages(messages, "")

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"

    def test_assistant_with_tool_calls(self):
        messages = [
            Message(
                role="assistant",
                content=[
                    TextBlock(text="Reading..."),
                    ToolUseBlock(
                        id="call_1",
                        name="Read",
                        input={"file_path": "test.py"},
                    ),
                ],
            ),
        ]

        result = _to_api_messages(messages, "")

        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Reading..."
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "call_1"
        assert result[0]["tool_calls"][0]["function"]["name"] == "Read"

        parsed_args = json.loads(
            result[0]["tool_calls"][0]["function"]["arguments"],
        )

        assert parsed_args == {"file_path": "test.py"}

    def test_tool_result_messages(self):
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call_1",
                        content="file contents",
                    ),
                ],
            ),
        ]

        result = _to_api_messages(messages, "")

        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == "file contents"


class TestToApiTools:
    def test_convert_to_function_tools(self):
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
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "Read"
        assert result[0]["function"]["description"] == "Read a file"
        assert result[0]["function"]["parameters"]["type"] == "object"

    def test_empty_tools(self):
        result = _to_api_tools([])

        assert result == []


class TestToLlmResponse:
    def test_text_response(self):
        choice = MagicMock()
        choice.message.content = "Hello!"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"

        response = MagicMock()
        response.choices = [choice]

        result = _to_llm_response(response)

        assert isinstance(result, LLMResponse)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "Hello!"
        assert result.stop_reason == StopReason.END_TURN

    def test_tool_calls_response(self):
        from openai.types.chat import ChatCompletionMessageToolCall
        from openai.types.chat.chat_completion_message_tool_call import Function

        tool_call = ChatCompletionMessageToolCall(
            id="call_1",
            type="function",
            function=Function(
                name="Read",
                arguments='{"file_path": "test.py"}',
            ),
        )

        choice = MagicMock()
        choice.message.content = None
        choice.message.tool_calls = [tool_call]
        choice.finish_reason = "tool_calls"

        response = MagicMock()
        response.choices = [choice]

        result = _to_llm_response(response)

        assert len(result.content) == 1
        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].name == "Read"
        assert result.content[0].input == {"file_path": "test.py"}
        assert result.stop_reason == StopReason.TOOL_USE

    def test_length_stop_reason(self):
        choice = MagicMock()
        choice.message.content = "truncated"
        choice.message.tool_calls = None
        choice.finish_reason = "length"

        response = MagicMock()
        response.choices = [choice]

        result = _to_llm_response(response)

        assert result.stop_reason == StopReason.MAX_TOKENS

    def test_invalid_tool_call_arguments(self):
        from openai.types.chat import ChatCompletionMessageToolCall
        from openai.types.chat.chat_completion_message_tool_call import Function

        tool_call = ChatCompletionMessageToolCall(
            id="call_1",
            type="function",
            function=Function(
                name="Read",
                arguments="not valid json",
            ),
        )

        choice = MagicMock()
        choice.message.content = None
        choice.message.tool_calls = [tool_call]
        choice.finish_reason = "tool_calls"

        response = MagicMock()
        response.choices = [choice]

        result = _to_llm_response(response)

        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].input == {}


class TestOpenAIProviderSend:
    @pytest.mark.asyncio
    async def test_send_returns_llm_response(self):
        provider = OpenAIProvider(
            api_key="test-key",
            provider_name=ProviderName.DEEPSEEK,
        )

        choice = MagicMock()
        choice.message.content = "result"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [choice]

        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(
            return_value=mock_response,
        )

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        result = await provider.send(
            messages=messages,
            system_prompt="Be helpful.",
            tools=[],
            model="gpt-4.1",
            max_tokens=1024,
        )

        assert isinstance(result, LLMResponse)
        assert result.content[0].text == "result"

    @pytest.mark.asyncio
    async def test_send_passes_tools_when_present(self):
        provider = OpenAIProvider(
            api_key="test-key",
            provider_name=ProviderName.DEEPSEEK,
        )

        choice = MagicMock()
        choice.message.content = "ok"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [choice]

        mock_create = AsyncMock(return_value=mock_response)
        provider._client = MagicMock()
        provider._client.chat.completions.create = mock_create

        tools: list[ToolDefinition] = [
            {
                "name": "Read",
                "description": "Read a file",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        await provider.send(
            messages=messages,
            system_prompt="",
            tools=tools,
            model="gpt-4.1",
            max_tokens=1024,
        )

        call_kwargs = mock_create.call_args[1]

        assert "tools" in call_kwargs

    @pytest.mark.asyncio
    async def test_send_omits_tools_when_empty(self):
        provider = OpenAIProvider(
            api_key="test-key",
            provider_name=ProviderName.DEEPSEEK,
        )

        choice = MagicMock()
        choice.message.content = "ok"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [choice]

        mock_create = AsyncMock(return_value=mock_response)
        provider._client = MagicMock()
        provider._client.chat.completions.create = mock_create

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        await provider.send(
            messages=messages,
            system_prompt="",
            tools=[],
            model="gpt-4.1",
            max_tokens=1024,
        )

        call_kwargs = mock_create.call_args[1]

        assert "tools" not in call_kwargs


class TestOpenAIProviderMissingSdk:
    def test_init_raises_missing_provider_error_when_sdk_absent(self):
        real_import = builtins.__import__

        def _block_openai(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        cached_modules = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "openai" or key.startswith("openai.")
        }

        try:
            with patch("builtins.__import__", side_effect=_block_openai):
                with pytest.raises(MissingProviderError, match="openai"):
                    OpenAIProvider(api_key="test")
        finally:
            sys.modules.update(cached_modules)

    def test_missing_provider_error_includes_install_instructions(self):
        error = MissingProviderError(
            "openai",
            "openai",
            'pip install "nominal-code[openai]"',
        )

        assert "nominal-code[openai]" in str(error)
