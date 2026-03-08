# type: ignore
import json
from unittest.mock import AsyncMock

import pytest

from nominal_code.agent.api.runner import run_agent_api
from nominal_code.agent.providers.types import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from nominal_code.agent.result import AgentResult
from nominal_code.models import ProviderName


def _make_text_response(text, usage=None):
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
        usage=usage,
    )


def _make_tool_use_response(tool_id, name, tool_input, usage=None):
    return LLMResponse(
        content=[
            ToolUseBlock(id=tool_id, name=name, input=tool_input),
        ],
        stop_reason=StopReason.TOOL_USE,
        usage=usage,
    )


class TestRunAgentApi:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("All good!"),
        )

        result = await run_agent_api(
            prompt="Review this code",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert isinstance(result, AgentResult)
        assert result.output == "All good!"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_empty_text_response(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[],
                stop_reason=StopReason.END_TURN,
            ),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.output == "Done, no output."

    @pytest.mark.asyncio
    async def test_tool_use_loop(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')")

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=[
                _make_tool_use_response(
                    "t1",
                    "Read",
                    {"file_path": str(test_file)},
                ),
                _make_text_response("The file prints hello."),
            ],
        )

        result = await run_agent_api(
            prompt="Read hello.py",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.output == "The file prints hello."
        assert result.num_turns == 1
        assert mock_provider.send.call_count == 2

    @pytest.mark.asyncio
    async def test_submit_review_returns_json(self, tmp_path):
        review_input = {
            "summary": "Looks good",
            "comments": [
                {"path": "a.py", "line": 1, "body": "Nice"},
            ],
        }
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[
                    ToolUseBlock(
                        id="t1",
                        name="submit_review",
                        input=review_input,
                    ),
                ],
                stop_reason=StopReason.TOOL_USE,
            ),
        )

        result = await run_agent_api(
            prompt="Review this",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            allowed_tools=["Read", "submit_review"],
        )

        parsed = json.loads(result.output)

        assert parsed["summary"] == "Looks good"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_max_turns_stops_loop(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_tool_use_response(
                "t1",
                "Glob",
                {"pattern": "**/*.py"},
            ),
        )

        result = await run_agent_api(
            prompt="Find files",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            max_turns=2,
        )

        assert result.output == "Max turns reached."
        assert result.num_turns == 2

    @pytest.mark.asyncio
    async def test_provider_error_returns_error_result(self, tmp_path):
        from nominal_code.agent.providers.base import ProviderError

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=ProviderError("API down"),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is True
        assert "API error" in result.output

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_error_result(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is True
        assert "Unexpected error" in result.output

    @pytest.mark.asyncio
    async def test_prior_messages_prepended(self, tmp_path):
        captured_messages = []

        async def capture_send(**kwargs):
            captured_messages.extend(list(kwargs["messages"]))

            return _make_text_response("second reply")

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=capture_send)

        prior = [
            Message(role="user", content=[TextBlock(text="first question")]),
            Message(role="assistant", content=[TextBlock(text="first reply")]),
        ]

        result = await run_agent_api(
            prompt="follow up",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            prior_messages=prior,
        )

        assert result.output == "second reply"
        assert len(captured_messages) == 3
        assert captured_messages[0].content[0].text == "first question"
        assert captured_messages[1].content[0].text == "first reply"
        assert captured_messages[2].content[0].text == "follow up"

    @pytest.mark.asyncio
    async def test_result_includes_messages(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("done"),
        )

        result = await run_agent_api(
            prompt="hello",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert len(result.messages) == 2
        assert result.messages[0].role == "user"
        assert result.messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_error_result_has_no_messages(self, tmp_path):
        from nominal_code.agent.providers.base import ProviderError

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=ProviderError("fail"),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is True
        assert result.messages == ()


class TestRunAgentApiCost:
    @pytest.mark.asyncio
    async def test_cost_summary_present_on_simple_response(self, tmp_path):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("done", usage=usage),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.cost is not None
        assert result.cost.total_input_tokens == 100
        assert result.cost.total_output_tokens == 50
        assert result.cost.provider == "openai"
        assert result.cost.model == "gpt-4.1"
        assert result.cost.num_api_calls == 1

    @pytest.mark.asyncio
    async def test_cost_accumulates_across_turns(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')")

        usage1 = TokenUsage(input_tokens=100, output_tokens=50)
        usage2 = TokenUsage(input_tokens=200, output_tokens=80)

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=[
                _make_tool_use_response(
                    "t1",
                    "Read",
                    {"file_path": str(test_file)},
                    usage=usage1,
                ),
                _make_text_response("The file prints hello.", usage=usage2),
            ],
        )

        result = await run_agent_api(
            prompt="Read hello.py",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.cost is not None
        assert result.cost.total_input_tokens == 300
        assert result.cost.total_output_tokens == 130
        assert result.cost.num_api_calls == 2
        assert result.cost.total_cost_usd is not None

    @pytest.mark.asyncio
    async def test_cost_present_on_error(self, tmp_path):
        from nominal_code.agent.providers.base import ProviderError

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=ProviderError("fail"),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.is_error is True
        assert result.cost is not None
        assert result.cost.num_api_calls == 0

    @pytest.mark.asyncio
    async def test_cost_with_no_usage_from_provider(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[TextBlock(text="done")],
                stop_reason=StopReason.END_TURN,
                usage=None,
            ),
        )

        result = await run_agent_api(
            prompt="test",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.cost is not None
        assert result.cost.total_input_tokens == 0
        assert result.cost.total_cost_usd is None
