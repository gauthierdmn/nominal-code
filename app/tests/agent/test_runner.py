# type: ignore
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from nominal_code.agent.runner import AgentResult, run_agent


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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
            result = await run_agent(
                prompt="fix the bug",
                cwd="/tmp/workspace",
            )

        assert isinstance(result, AgentResult)
        assert result.output == "All fixed!"
        assert result.is_error is False
        assert result.conversation_id == "sess-abc"

    @pytest.mark.asyncio
    async def test_run_agent_no_result_message(self):
        async def mock_query(*args, **kwargs):
            return
            yield

        with patch("nominal_code.agent.cli.runner.query", mock_query):
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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
            await run_agent(prompt="fix it", cwd="/tmp")

        assert captured_options["options"].system_prompt is None

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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
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

        with patch("nominal_code.agent.cli.runner.query", mock_query):
            await run_agent(prompt="fix it", cwd="/tmp")

        assert captured_options["options"].permission_mode == "bypassPermissions"


class TestAgentResult:
    def test_claude_result_is_frozen(self):
        result = AgentResult(
            output="test",
            is_error=False,
            num_turns=1,
            duration_ms=100,
            conversation_id="s1",
        )

        with pytest.raises(AttributeError):
            result.output = "changed"


class TestPatchedParseMessage:
    def test_patched_parse_message_passes_through_valid_message(self):
        from claude_agent_sdk import SystemMessage

        from nominal_code.agent.cli.runner import (
            _patched_parse_message,
        )

        data = {"type": "system", "subtype": "init", "session_id": "s1"}

        with patch(
            "nominal_code.agent.cli.runner._original_parse_message"
        ) as mock_orig:
            mock_orig.return_value = SystemMessage(subtype="init", data={})
            result = _patched_parse_message(data)

        mock_orig.assert_called_once_with(data)
        assert isinstance(result, SystemMessage)

    def test_patched_parse_message_returns_system_message_on_parse_error(self):
        from claude_agent_sdk import SystemMessage
        from claude_agent_sdk._errors import MessageParseError

        from nominal_code.agent.cli.runner import _patched_parse_message

        data = {"type": "rate_limit_event", "extra": "field"}

        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=lambda d: (_ for _ in ()).throw(MessageParseError("unknown")),
        ):
            result = _patched_parse_message(data)

        assert isinstance(result, SystemMessage)
        assert result.subtype == "rate_limit_event"

    def test_patched_parse_message_unknown_type_when_non_dict(self):
        from claude_agent_sdk import SystemMessage
        from claude_agent_sdk._errors import MessageParseError

        from nominal_code.agent.cli.runner import _patched_parse_message

        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=lambda d: (_ for _ in ()).throw(MessageParseError("unknown")),
        ):
            result = _patched_parse_message("not-a-dict")

        assert isinstance(result, SystemMessage)
        assert result.subtype == "unknown"

    def test_patched_parse_message_preserves_data_on_parse_error(self):
        from claude_agent_sdk import SystemMessage
        from claude_agent_sdk._errors import MessageParseError

        from nominal_code.agent.cli.runner import _patched_parse_message

        data = {"type": "rate_limit_event", "retry_after": 30}

        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=lambda d: (_ for _ in ()).throw(MessageParseError("unknown")),
        ):
            result = _patched_parse_message(data)

        assert isinstance(result, SystemMessage)
        assert result.data == data


class TestRunAgentApiDispatch:
    @pytest.mark.asyncio
    async def test_run_agent_threads_prior_messages_to_api_runner(self):
        from nominal_code.agent.providers.types import Message, TextBlock
        from nominal_code.config import ApiAgentConfig, ProviderConfig
        from nominal_code.models import ProviderName

        prior = [Message(role="user", content=[TextBlock(text="earlier")])]
        captured = {}

        async def mock_run_api(**kwargs):
            captured.update(kwargs)

            return AgentResult(
                output="ok",
                is_error=False,
                num_turns=1,
                duration_ms=100,
            )

        with (
            patch(
                "nominal_code.agent.runner.run_agent_api",
                side_effect=mock_run_api,
            ),
            patch(
                "nominal_code.agent.runner.create_provider",
                return_value=MagicMock(),
            ),
        ):
            await run_agent(
                prompt="test",
                cwd="/tmp",
                agent_config=ApiAgentConfig(
                    provider=ProviderConfig(
                        name=ProviderName.ANTHROPIC,
                        model="test",
                    ),
                ),
                prior_messages=prior,
            )

        assert captured["prior_messages"] == prior


class TestLogMessage:
    def test_log_message_does_nothing_when_debug_disabled(self):
        from unittest.mock import MagicMock

        from nominal_code.agent.cli.runner import _log_message

        message = MagicMock()

        with patch("nominal_code.agent.cli.runner.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = False
            _log_message(message)

        mock_logger.debug.assert_not_called()

    def test_log_message_logs_text_block_for_assistant_message(self):
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        from nominal_code.agent.cli.runner import _log_message

        block = MagicMock(spec=TextBlock)
        block.text = "hello"
        message = MagicMock(spec=AssistantMessage)
        message.content = [block]

        with patch("nominal_code.agent.cli.runner.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            _log_message(message)

        mock_logger.debug.assert_called()

    def test_log_message_logs_tool_result_truncated(self):
        from claude_agent_sdk import UserMessage
        from claude_agent_sdk.types import ToolResultBlock

        from nominal_code.agent.cli.runner import (
            MAX_TOOL_RESULT_LOG_LENGTH,
            _log_message,
        )

        block = MagicMock(spec=ToolResultBlock)
        block.content = "x" * (MAX_TOOL_RESULT_LOG_LENGTH + 100)
        block.tool_use_id = "tu-1"
        block.is_error = False
        message = MagicMock(spec=UserMessage)
        message.content = [block]

        with patch("nominal_code.agent.cli.runner.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            _log_message(message)

        debug_call = mock_logger.debug.call_args_list[-1]
        logged_content = debug_call.args[3]

        assert "truncated" in logged_content

    def test_log_message_does_not_crash_for_other_message_types(self):
        from claude_agent_sdk import ResultMessage

        from nominal_code.agent.cli.runner import _log_message

        message = MagicMock(spec=ResultMessage)

        with patch("nominal_code.agent.cli.runner.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            _log_message(message)
