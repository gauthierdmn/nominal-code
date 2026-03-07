# type: ignore
import json
from unittest.mock import AsyncMock

import pytest

from nominal_code.agent.api.runner import run_agent_api
from nominal_code.agent.providers.types import (
    LLMResponse,
    StopReason,
    TextBlock,
    ToolUseBlock,
)
from nominal_code.agent.result import AgentResult


def _make_text_response(text):
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
    )


def _make_tool_use_response(tool_id, name, tool_input):
    return LLMResponse(
        content=[
            ToolUseBlock(id=tool_id, name=name, input=tool_input),
        ],
        stop_reason=StopReason.TOOL_USE,
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
