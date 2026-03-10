# type: ignore
from nominal_code.conversation.base import truncate_messages
from nominal_code.llm.messages import Message, TextBlock, ToolResultBlock


class TestTruncateMessages:
    def test_empty_list(self):
        assert truncate_messages(messages=[]) == []

    def test_within_budget_unchanged(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hi")]),
            Message(role="assistant", content=[TextBlock(text="hello")]),
        ]
        result = truncate_messages(messages=messages, max_chars=100_000)

        assert result == messages

    def test_drops_oldest_pair(self):
        messages = [
            Message(role="user", content=[TextBlock(text="a" * 100)]),
            Message(role="assistant", content=[TextBlock(text="b" * 100)]),
            Message(role="user", content=[TextBlock(text="c" * 100)]),
            Message(role="assistant", content=[TextBlock(text="d" * 100)]),
        ]
        result = truncate_messages(messages=messages, max_chars=250)

        assert len(result) == 2
        assert result[0].content[0].text == "c" * 100
        assert result[1].content[0].text == "d" * 100

    def test_preserves_last_message_even_over_budget(self):
        messages = [
            Message(role="user", content=[TextBlock(text="x" * 1000)]),
        ]
        result = truncate_messages(messages=messages, max_chars=10)

        assert len(result) == 1

    def test_tool_result_blocks_use_fixed_estimate(self):
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok", is_error=False),
                ],
            ),
        ]
        result = truncate_messages(messages=messages, max_chars=500_000)

        assert result == messages
