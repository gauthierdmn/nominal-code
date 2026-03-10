# type: ignore
import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.llm.messages import Message, TextBlock


class TestAgentResult:
    def test_create_minimal(self):
        result = AgentResult(
            output="done",
            is_error=False,
            num_turns=3,
            duration_ms=1500,
        )

        assert result.output == "done"
        assert result.is_error is False
        assert result.num_turns == 3
        assert result.duration_ms == 1500

    def test_defaults(self):
        result = AgentResult(
            output="done",
            is_error=False,
            num_turns=1,
            duration_ms=100,
        )

        assert result.conversation_id is None
        assert result.messages == ()
        assert result.cost is None

    def test_with_conversation_id(self):
        result = AgentResult(
            output="done",
            is_error=False,
            num_turns=1,
            duration_ms=100,
            conversation_id="conv-123",
        )

        assert result.conversation_id == "conv-123"

    def test_with_messages(self):
        messages = (
            Message(role="user", content=[TextBlock(text="hello")]),
            Message(role="assistant", content=[TextBlock(text="hi")]),
        )

        result = AgentResult(
            output="done",
            is_error=False,
            num_turns=1,
            duration_ms=100,
            messages=messages,
        )

        assert len(result.messages) == 2
        assert result.messages[0].role == "user"

    def test_is_frozen(self):
        result = AgentResult(
            output="done",
            is_error=False,
            num_turns=1,
            duration_ms=100,
        )

        with pytest.raises(AttributeError):
            result.output = "changed"

    def test_error_result(self):
        result = AgentResult(
            output="Agent crashed",
            is_error=True,
            num_turns=0,
            duration_ms=0,
        )

        assert result.is_error is True
        assert result.output == "Agent crashed"
