# type: ignore
from nominal_code.agent.compaction import (
    COMPACT_DIRECT_RESUME_INSTRUCTION,
    COMPACT_RECENT_MESSAGES_NOTE,
    COMPACTION_MARKER,
    _compacted_summary_prefix_len,
    compact_with_notes,
)
from nominal_code.llm.messages import Message, TextBlock


def _user_text(text):
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant_text(text):
    return Message(role="assistant", content=[TextBlock(text=text)])


class TestCompactedSummaryPrefixLen:
    def test_returns_zero_for_empty(self):
        assert _compacted_summary_prefix_len([]) == 0

    def test_returns_zero_for_normal_message(self):
        messages = [_user_text("hello")]

        assert _compacted_summary_prefix_len(messages) == 0

    def test_returns_one_for_compaction_message(self):
        msg = Message(
            role="system",
            content=[TextBlock(text=f"{COMPACTION_MARKER}\nSome summary")],
        )

        assert _compacted_summary_prefix_len([msg]) == 1


class TestCompactWithNotes:
    def test_noop_when_notes_empty(self):
        messages = [
            _user_text("old"),
            _assistant_text("old"),
            _user_text("old2"),
            _assistant_text("old2"),
            _user_text("recent1"),
            _assistant_text("recent2"),
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        result = compact_with_notes(messages, "")

        assert result.messages is messages
        assert result.summary_text == ""

    def test_noop_when_notes_whitespace_only(self):
        messages = [_user_text("a"), _assistant_text("b")] * 5
        result = compact_with_notes(messages, "   \n  ")

        assert result.messages is messages
        assert result.summary_text == ""

    def test_noop_when_too_few_messages(self):
        messages = [_user_text("hello"), _assistant_text("world")]
        result = compact_with_notes(messages, "## Callers\nSome findings.")

        assert result.messages is messages
        assert result.summary_text == ""

    def test_compacts_with_notes(self):
        messages = [
            _user_text("old"),
            _assistant_text("old"),
            _user_text("old2"),
            _assistant_text("old2"),
            _user_text("recent1"),
            _assistant_text("recent2"),
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        notes = "## Callers\nFound caller in handler.py:45"
        result = compact_with_notes(messages, notes)

        assert result.summary_text == notes
        assert len(result.messages) == 5

    def test_preserves_recent_messages(self):
        recent_user = _user_text("recent_user")
        recent_assistant = _assistant_text("recent_assistant")
        messages = [
            _user_text("old"),
            _assistant_text("old"),
            _user_text("old2"),
            _assistant_text("old2"),
            recent_user,
            recent_assistant,
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        result = compact_with_notes(messages, "## Tests\nFound tests.")

        assert result.messages[-4] is recent_user
        assert result.messages[-3] is recent_assistant

    def test_continuation_message_has_marker(self):
        messages = [_user_text("a"), _assistant_text("b")] * 5
        result = compact_with_notes(messages, "Some notes.")

        continuation = result.messages[0]

        assert continuation.role == "system"
        assert COMPACTION_MARKER in continuation.content[0].text

    def test_continuation_message_has_notes_content(self):
        messages = [_user_text("a"), _assistant_text("b")] * 5
        notes = "## Callers\nhandler.py:45 calls review()"
        result = compact_with_notes(messages, notes)

        continuation_text = result.messages[0].content[0].text

        assert notes in continuation_text
        assert COMPACT_RECENT_MESSAGES_NOTE in continuation_text
        assert COMPACT_DIRECT_RESUME_INSTRUCTION in continuation_text

    def test_recompaction_skips_prior_summary(self):
        compaction_msg = Message(
            role="system",
            content=[TextBlock(text=f"{COMPACTION_MARKER}\nOld summary")],
        )
        messages = [
            compaction_msg,
            _user_text("msg1"),
            _assistant_text("msg2"),
            _user_text("msg3"),
            _assistant_text("msg4"),
            _user_text("msg5"),
            _assistant_text("msg6"),
            _user_text("msg7"),
            _assistant_text("msg8"),
        ]
        result = compact_with_notes(messages, "Updated notes.")

        assert result.summary_text == "Updated notes."
        assert len(result.messages) == 5
        assert result.messages[0].role == "system"
        assert "Updated notes." in result.messages[0].content[0].text

    def test_noop_when_compaction_prefix_and_few_messages(self):
        compaction_msg = Message(
            role="system",
            content=[TextBlock(text=f"{COMPACTION_MARKER}\nOld summary")],
        )
        messages = [
            compaction_msg,
            _user_text("recent1"),
            _assistant_text("recent2"),
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        result = compact_with_notes(messages, "Notes content.")

        assert result.messages is messages
        assert result.summary_text == ""
