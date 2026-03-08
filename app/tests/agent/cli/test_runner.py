# type: ignore
from unittest.mock import MagicMock, patch

from claude_agent_sdk import SystemMessage
from claude_agent_sdk._errors import MessageParseError

from nominal_code.agent.cli.runner import _patched_parse_message


class TestPatchedParseMessage:
    def test_passes_through_valid_message(self):
        mock_message = MagicMock()

        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            return_value=mock_message,
        ):
            result = _patched_parse_message({"type": "assistant"})

        assert result is mock_message

    def test_returns_system_message_on_parse_error(self):
        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=MessageParseError("Unknown type"),
        ):
            result = _patched_parse_message({"type": "rate_limit_event"})

        assert isinstance(result, SystemMessage)
        assert result.subtype == "rate_limit_event"

    def test_returns_unknown_subtype_for_non_dict(self):
        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=MessageParseError("Unknown type"),
        ):
            result = _patched_parse_message("not a dict")

        assert isinstance(result, SystemMessage)
        assert result.subtype == "unknown"
        assert result.data == {}
