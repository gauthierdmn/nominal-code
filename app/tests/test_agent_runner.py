# type: ignore
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from nominal_code.agent_runner import AgentResult, run_agent


def _make_result_message(
    result="Done",
    is_error=False,
    num_turns=3,
    duration_ms=5000,
    session_id="sess-123",
):
    msg = MagicMock()
    msg.__class__ = MagicMock()
    msg.result = result
    msg.is_error = is_error
    msg.num_turns = num_turns
    msg.duration_ms = duration_ms
    msg.session_id = session_id

    return msg


def _make_system_message(subtype="init", data=None):
    msg = MagicMock()
    msg.subtype = subtype
    msg.data = data or {}

    return msg


class TestRunClaude:
    @pytest.mark.asyncio
    async def test_run_agent_returns_result(self):
        init_msg = MagicMock(spec=SystemMessage)
        init_msg.subtype = "init"
        init_msg.data = {"session_id": "sess-abc"}

        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "All fixed!"
        result_msg.is_error = False
        result_msg.num_turns = 2
        result_msg.duration_ms = 3000
        result_msg.session_id = "sess-abc"

        async def mock_query(*args, **kwargs):
            yield init_msg
            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            result = await run_agent(
                prompt="fix the bug",
                cwd="/tmp/workspace",
            )

        assert isinstance(result, AgentResult)
        assert result.output == "All fixed!"
        assert result.is_error is False
        assert result.session_id == "sess-abc"

    @pytest.mark.asyncio
    async def test_run_agent_no_result_message(self):
        async def mock_query(*args, **kwargs):
            return
            yield

        with patch("nominal_code.agent_runner.query", mock_query):
            result = await run_agent(prompt="test", cwd="/tmp")

        assert result.is_error is True
        assert result.output == "No result received from the agent."

    @pytest.mark.asyncio
    async def test_run_agent_empty_result(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = ""
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 1000
        result_msg.session_id = "sess-xyz"

        async def mock_query(*args, **kwargs):
            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            result = await run_agent(prompt="test", cwd="/tmp")

        assert result.output == "Done, no output."

    @pytest.mark.asyncio
    async def test_run_agent_forwards_system_prompt(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "Done"
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 500
        result_msg.session_id = "sess-sp"

        captured_options = {}

        async def mock_query(*args, **kwargs):
            captured_options.update(kwargs)

            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            await run_agent(
                prompt="fix it",
                cwd="/tmp",
                system_prompt="Be concise.",
            )

        assert captured_options["options"].system_prompt == "Be concise."

    @pytest.mark.asyncio
    async def test_run_agent_omits_system_prompt_when_empty(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "Done"
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 500
        result_msg.session_id = "sess-nsp"

        captured_options = {}

        async def mock_query(*args, **kwargs):
            captured_options.update(kwargs)

            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            await run_agent(prompt="fix it", cwd="/tmp")

        assert captured_options["options"].system_prompt is None

    @pytest.mark.asyncio
    async def test_run_agent_forwards_permission_mode(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "Done"
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 500
        result_msg.session_id = "sess-pm"

        captured_options = {}

        async def mock_query(*args, **kwargs):
            captured_options.update(kwargs)

            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            await run_agent(
                prompt="review it",
                cwd="/tmp",
                permission_mode="plan",
            )

        assert captured_options["options"].permission_mode == "plan"

    @pytest.mark.asyncio
    async def test_run_agent_forwards_allowed_tools(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "Done"
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 500
        result_msg.session_id = "sess-at"

        captured_options = {}

        async def mock_query(*args, **kwargs):
            captured_options.update(kwargs)

            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            await run_agent(
                prompt="review it",
                cwd="/tmp",
                allowed_tools=["Read", "Glob", "Grep"],
            )

        assert captured_options["options"].allowed_tools == [
            "Read",
            "Glob",
            "Grep",
        ]

    @pytest.mark.asyncio
    async def test_run_agent_default_allowed_tools_is_empty(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "Done"
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 500
        result_msg.session_id = "sess-dat"

        captured_options = {}

        async def mock_query(*args, **kwargs):
            captured_options.update(kwargs)

            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            await run_agent(prompt="fix it", cwd="/tmp")

        assert captured_options["options"].allowed_tools == []

    @pytest.mark.asyncio
    async def test_run_agent_default_permission_mode_is_bypass(self):
        result_msg = MagicMock(spec=ResultMessage)
        result_msg.result = "Done"
        result_msg.is_error = False
        result_msg.num_turns = 1
        result_msg.duration_ms = 500
        result_msg.session_id = "sess-dpm"

        captured_options = {}

        async def mock_query(*args, **kwargs):
            captured_options.update(kwargs)

            yield result_msg

        with patch("nominal_code.agent_runner.query", mock_query):
            await run_agent(prompt="fix it", cwd="/tmp")

        assert captured_options["options"].permission_mode == "bypassPermissions"


class TestAgentResult:
    def test_claude_result_is_frozen(self):
        result = AgentResult(
            output="test",
            is_error=False,
            num_turns=1,
            duration_ms=100,
            session_id="s1",
        )

        with pytest.raises(AttributeError):
            result.output = "changed"
