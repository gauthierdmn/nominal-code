# type: ignore
import builtins
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.providers.base import MissingProviderError

pytest.importorskip("google.genai")

from nominal_code.agent.providers.base import (
    ContextLengthError,
    ProviderError,
    RateLimitError,
)
from nominal_code.agent.providers.google import (
    GoogleProvider,
    _to_api_contents,
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


class TestToApiContents:
    def test_user_text_message(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hello")]),
        ]

        result = _to_api_contents(messages)

        assert len(result) == 1
        assert result[0].role == "user"
        assert result[0].parts[0].text == "hello"

    def test_assistant_text_message(self):
        messages = [
            Message(role="assistant", content=[TextBlock(text="hi there")]),
        ]

        result = _to_api_contents(messages)

        assert len(result) == 1
        assert result[0].role == "model"
        assert result[0].parts[0].text == "hi there"

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

        result = _to_api_contents(messages)

        assert result[0].role == "model"
        assert result[0].parts[0].function_call.name == "Read"
        assert result[0].parts[0].function_call.args == {"file_path": "test.py"}

    def test_user_tool_result_message(self):
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

        result = _to_api_contents(messages)

        assert result[1].role == "user"
        assert result[1].parts[0].function_response.name == "Read"
        assert result[1].parts[0].function_response.response == {
            "result": "file contents here"
        }

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

        result = _to_api_contents(messages)

        assert len(result) == 4
        assert result[0].role == "user"
        assert result[1].role == "model"
        assert result[2].role == "user"
        assert result[3].role == "model"


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

        assert result is not None
        assert len(result.function_declarations) == 1
        assert result.function_declarations[0].name == "Read"
        assert result.function_declarations[0].description == "Read a file"

    def test_empty_tools_returns_none(self):
        result = _to_api_tools([])

        assert result is None


class TestToLlmResponse:
    def test_text_response(self):
        mock_response = MagicMock()
        part = MagicMock()
        part.function_call = None
        part.text = "Hello!"
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "STOP"
        mock_response.candidates = [candidate]

        result = _to_llm_response(mock_response)

        assert isinstance(result, LLMResponse)
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "Hello!"
        assert result.stop_reason == StopReason.END_TURN

    def test_tool_use_response(self):
        mock_response = MagicMock()
        part = MagicMock()
        part.function_call.name = "Read"
        part.function_call.args = {"file_path": "test.py"}
        part.text = None
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "STOP"
        mock_response.candidates = [candidate]

        result = _to_llm_response(mock_response)

        assert len(result.content) == 1
        assert isinstance(result.content[0], ToolUseBlock)
        assert result.content[0].name == "Read"
        assert result.stop_reason == StopReason.TOOL_USE

    def test_max_tokens_stop_reason(self):
        mock_response = MagicMock()
        part = MagicMock()
        part.function_call = None
        part.text = "partial"
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "MAX_TOKENS"
        mock_response.candidates = [candidate]

        result = _to_llm_response(mock_response)

        assert result.stop_reason == StopReason.MAX_TOKENS

    def test_unknown_stop_reason_defaults_to_end_turn(self):
        mock_response = MagicMock()
        part = MagicMock()
        part.function_call = None
        part.text = "done"
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "SOMETHING_ELSE"
        mock_response.candidates = [candidate]

        result = _to_llm_response(mock_response)

        assert result.stop_reason == StopReason.END_TURN

    def test_empty_candidates(self):
        mock_response = MagicMock()
        mock_response.candidates = []

        result = _to_llm_response(mock_response)

        assert result.content == []
        assert result.stop_reason == StopReason.END_TURN

    def test_synthetic_tool_call_ids(self):
        mock_response = MagicMock()
        part0 = MagicMock()
        part0.function_call.name = "Read"
        part0.function_call.args = {"file_path": "a.py"}
        part0.text = None
        part1 = MagicMock()
        part1.function_call.name = "Glob"
        part1.function_call.args = {"pattern": "*.py"}
        part1.text = None
        candidate = MagicMock()
        candidate.content.parts = [part0, part1]
        candidate.finish_reason = "STOP"
        mock_response.candidates = [candidate]

        result = _to_llm_response(mock_response)

        assert result.content[0].id == "call_0"
        assert result.content[1].id == "call_1"


class TestToLlmResponseUsage:
    def test_extracts_usage_metadata(self):
        mock_response = MagicMock()
        part = MagicMock()
        part.function_call = None
        part.text = "Hello!"
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "STOP"
        mock_response.candidates = [candidate]
        mock_response.usage_metadata.prompt_token_count = 150
        mock_response.usage_metadata.candidates_token_count = 75

        result = _to_llm_response(mock_response)

        assert result.usage is not None
        assert result.usage.input_tokens == 150
        assert result.usage.output_tokens == 75

    def test_null_usage_metadata(self):
        mock_response = MagicMock()
        part = MagicMock()
        part.function_call = None
        part.text = "Hello!"
        candidate = MagicMock()
        candidate.content.parts = [part]
        candidate.finish_reason = "STOP"
        mock_response.candidates = [candidate]
        mock_response.usage_metadata = None

        result = _to_llm_response(mock_response)

        assert result.usage is None


def _make_provider():
    with patch("google.genai.Client"):
        return GoogleProvider()


class TestGoogleProviderSend:
    @pytest.mark.asyncio
    async def test_send_returns_llm_response(self):
        provider = _make_provider()

        mock_part = MagicMock()
        mock_part.function_call = None
        mock_part.text = "result"
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]
        mock_candidate.finish_reason = "STOP"
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        provider._client = MagicMock()
        provider._client.aio.models.generate_content = AsyncMock(
            return_value=mock_response,
        )

        messages = [Message(role="user", content=[TextBlock(text="test")])]

        result = await provider.send(
            messages=messages,
            system_prompt="Be helpful.",
            tools=[],
            model="gemini-2.5-flash",
            max_tokens=1024,
        )

        assert isinstance(result, LLMResponse)
        assert result.content[0].text == "result"
        assert result.response_id is None

    @pytest.mark.asyncio
    async def test_send_wraps_rate_limit_error(self):
        from google.genai import errors as genai_errors

        provider = _make_provider()
        provider._client = MagicMock()

        exc = genai_errors.ClientError(429, {"error": {"message": "rate limited"}})

        provider._client.aio.models.generate_content = AsyncMock(
            side_effect=exc,
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
        from google.genai import errors as genai_errors

        provider = _make_provider()
        provider._client = MagicMock()

        exc = genai_errors.ServerError(500, {"error": {"message": "server error"}})

        provider._client.aio.models.generate_content = AsyncMock(
            side_effect=exc,
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
        from google.genai import errors as genai_errors

        provider = _make_provider()
        provider._client = MagicMock()

        exc = genai_errors.ClientError(
            400,
            {"error": {"message": "token limit exceeded"}},
        )

        provider._client.aio.models.generate_content = AsyncMock(
            side_effect=exc,
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


class TestGoogleProviderMissingSdk:
    def test_init_raises_missing_provider_error_when_sdk_absent(self):
        real_import = builtins.__import__

        def _block_google(name, *args, **kwargs):
            if name == "google.genai" or name == "google":
                raise ImportError("No module named 'google.genai'")
            return real_import(name, *args, **kwargs)

        cached_modules = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "google" or key.startswith("google.")
        }

        try:
            with patch("builtins.__import__", side_effect=_block_google):
                with pytest.raises(MissingProviderError, match="google"):
                    GoogleProvider()
        finally:
            sys.modules.update(cached_modules)

    def test_missing_provider_error_includes_install_instructions(self):
        error = MissingProviderError(
            "google",
            "google-genai",
            'pip install "nominal-code[google]"',
        )

        assert "nominal-code[google]" in str(error)
