# type: ignore
import pytest

from nominal_code.agent.compaction import (
    COMPACTION_MARKER,
    CompactionConfig,
    CompactionResult,
    _build_summary,
    _compress_summary,
    _truncate,
    compact_messages,
    estimate_message_tokens,
    should_compact,
)
from nominal_code.llm.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock


def _user_text(text):
    return Message(role="user", content=[TextBlock(text=text)])


def _assistant_text(text):
    return Message(role="assistant", content=[TextBlock(text=text)])


def _tool_use_message(tool_id, name, tool_input):
    return Message(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=name, input=tool_input)],
    )


def _tool_result_message(tool_use_id, content, is_error=False):
    return Message(
        role="user",
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=content,
                is_error=is_error,
            ),
        ],
    )


class TestEstimateMessageTokens:
    def test_empty_list_returns_zero(self):
        assert estimate_message_tokens([]) == 0

    def test_text_block(self):
        messages = [_user_text("hello world")]
        tokens = estimate_message_tokens(messages)

        assert tokens == len("hello world") // 4 + 1

    def test_tool_use_block(self):
        messages = [
            _tool_use_message("id1", "Read", {"file_path": "/tmp/test.py"}),
        ]
        tokens = estimate_message_tokens(messages)

        assert tokens > 0

    def test_tool_result_block(self):
        messages = [_tool_result_message("id1", "file contents here")]
        tokens = estimate_message_tokens(messages)

        assert tokens == (len("id1") + len("file contents here")) // 4 + 1

    def test_multiple_messages(self):
        messages = [
            _user_text("prompt"),
            _assistant_text("response"),
            _user_text("followup"),
        ]
        tokens = estimate_message_tokens(messages)

        expected = (
            len("prompt") // 4 + 1
            + len("response") // 4 + 1
            + len("followup") // 4 + 1
        )

        assert tokens == expected

    def test_large_tool_result_produces_many_tokens(self):
        large_content = "x" * 40_000
        messages = [_tool_result_message("id1", large_content)]
        tokens = estimate_message_tokens(messages)

        assert tokens > 10_000


class TestShouldCompact:
    def test_false_when_too_few_messages(self):
        config = CompactionConfig(preserve_recent_messages=4, max_estimated_tokens=100)
        messages = [_user_text("a"), _assistant_text("b")]

        assert should_compact(messages, config) is False

    def test_false_when_below_token_threshold(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=100_000,
        )
        messages = [
            _user_text("short"),
            _assistant_text("short"),
            _user_text("short"),
            _assistant_text("short"),
        ]

        assert should_compact(messages, config) is False

    def test_true_when_above_threshold(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        large_content = "x" * 1000
        messages = [
            _user_text(large_content),
            _assistant_text(large_content),
            _user_text("recent1"),
            _assistant_text("recent2"),
        ]

        assert should_compact(messages, config) is True

    def test_at_threshold_does_not_compact(self):
        config = CompactionConfig(
            preserve_recent_messages=1,
            max_estimated_tokens=10_000,
        )
        # len("x" * 39_996) // 4 + 1 == 10_000 exactly, which is not > threshold
        text = "x" * 39_996
        messages = [_user_text(text), _assistant_text("recent")]

        assert should_compact(messages, config) is False


class TestCompactMessages:
    def test_noop_when_below_threshold(self):
        config = CompactionConfig(
            preserve_recent_messages=4,
            max_estimated_tokens=100_000,
        )
        messages = [_user_text("hello"), _assistant_text("world")]
        result = compact_messages(messages, config)

        assert result.did_compact is False
        assert result.messages is messages
        assert result.removed_count == 0
        assert result.summary_text == ""

    def test_preserves_recent_messages(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        large_content = "x" * 1000
        recent_user = _user_text("recent_user")
        recent_assistant = _assistant_text("recent_assistant")
        messages = [
            _user_text(large_content),
            _assistant_text(large_content),
            _tool_use_message("t1", "Read", {"file_path": "/tmp/foo.py"}),
            _tool_result_message("t1", large_content),
            recent_user,
            recent_assistant,
        ]
        result = compact_messages(messages, config)

        assert result.did_compact is True
        assert result.removed_count == 4
        assert len(result.messages) == 3
        assert result.messages[-2] is recent_user
        assert result.messages[-1] is recent_assistant

    def test_continuation_message_has_marker(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        large_content = "x" * 1000
        messages = [
            _user_text(large_content),
            _assistant_text(large_content),
            _user_text("recent"),
            _assistant_text("recent"),
        ]
        result = compact_messages(messages, config)

        assert result.did_compact is True

        continuation = result.messages[0]

        assert continuation.role == "user"
        assert len(continuation.content) == 1
        assert isinstance(continuation.content[0], TextBlock)
        assert COMPACTION_MARKER in continuation.content[0].text

    def test_summary_contains_scope(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        large_content = "x" * 1000
        messages = [
            _user_text(large_content),
            _assistant_text(large_content),
            _user_text("recent"),
            _assistant_text("recent"),
        ]
        result = compact_messages(messages, config)

        assert "Compacted 2 messages" in result.summary_text

    def test_summary_contains_tools(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        messages = [
            _user_text("explore the code"),
            _tool_use_message("t1", "Read", {"file_path": "/tmp/foo.py"}),
            _tool_result_message("t1", "x" * 1000),
            _tool_use_message("t2", "Grep", {"pattern": "def main"}),
            _tool_result_message("t2", "x" * 1000),
            _user_text("recent"),
            _assistant_text("recent"),
        ]
        result = compact_messages(messages, config)

        assert result.did_compact is True
        assert "Read" in result.summary_text
        assert "Grep" in result.summary_text

    def test_summary_contains_file_paths(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        messages = [
            _user_text("check the file"),
            _tool_use_message("t1", "Read", {"file_path": "src/main.py"}),
            _tool_result_message("t1", "x" * 1000),
            _user_text("recent"),
            _assistant_text("recent"),
        ]
        result = compact_messages(messages, config)

        assert "src/main.py" in result.summary_text

    def test_recompaction_merges_prior_summary(self):
        config = CompactionConfig(
            preserve_recent_messages=2,
            max_estimated_tokens=10,
        )
        large_content = "x" * 1000

        first_messages = [
            _user_text(large_content),
            _assistant_text(large_content),
            _user_text("middle"),
            _assistant_text("middle"),
        ]
        first_result = compact_messages(first_messages, config)

        assert first_result.did_compact is True

        extended = [
            *first_result.messages,
            _user_text(large_content),
            _assistant_text(large_content),
            _user_text("latest"),
            _assistant_text("latest"),
        ]
        second_result = compact_messages(extended, config)

        assert second_result.did_compact is True
        assert "Previously compacted context" in second_result.summary_text


class TestBuildSummary:
    def test_extracts_tools_from_removed(self):
        removed = [
            _tool_use_message("t1", "Read", {"file_path": "/a.py"}),
            _tool_result_message("t1", "content"),
            _tool_use_message("t2", "Bash", {"command": "git show HEAD"}),
            _tool_result_message("t2", "output"),
        ]
        config = CompactionConfig()
        summary = _build_summary(removed, None, config)

        assert "Bash" in summary
        assert "Read" in summary

    def test_extracts_recent_user_requests(self):
        removed = [
            _user_text("Please investigate the auth module"),
            _assistant_text("I'll look into it"),
            _user_text("Also check the tests"),
            _assistant_text("Checking now"),
        ]
        config = CompactionConfig()
        summary = _build_summary(removed, None, config)

        assert "investigate the auth module" in summary
        assert "check the tests" in summary

    def test_extracts_current_work(self):
        removed = [
            _user_text("prompt"),
            _assistant_text("I am analyzing the service layer for bugs"),
        ]
        config = CompactionConfig()
        summary = _build_summary(removed, None, config)

        assert "analyzing the service layer" in summary

    def test_prior_summary_included(self):
        removed = [
            _user_text("x" * 100),
            _assistant_text("y" * 100),
        ]
        config = CompactionConfig()
        prior = "- Scope: Compacted 4 messages\n- Tools used: Read"
        summary = _build_summary(removed, prior, config)

        assert "Previously compacted context" in summary
        assert "Compacted 4 messages" in summary

    def test_file_paths_from_tool_input(self):
        removed = [
            _tool_use_message("t1", "Read", {"file_path": "lib/utils.py"}),
            _tool_result_message("t1", "content"),
        ]
        config = CompactionConfig()
        summary = _build_summary(removed, None, config)

        assert "lib/utils.py" in summary

    def test_skips_compaction_marker_in_requests(self):
        removed = [
            _user_text(f"{COMPACTION_MARKER}\nOld summary text"),
            _assistant_text("response"),
            _user_text("real user request"),
            _assistant_text("real response"),
        ]
        config = CompactionConfig()
        summary = _build_summary(removed, None, config)

        assert "Recent requests" in summary
        assert "real user request" in summary


class TestCompressSummary:
    def test_deduplicates_lines(self):
        raw = "- Scope: 4 messages\n- Scope: 4 messages\n- Tools: Read"
        config = CompactionConfig()
        compressed = _compress_summary(raw, config)
        count = compressed.count("Scope: 4 messages")

        assert count == 1

    def test_enforces_line_limit(self):
        lines = "\n".join(f"- Line {idx}" for idx in range(50))
        config = CompactionConfig(summary_max_lines=10)
        compressed = _compress_summary(lines, config)
        line_count = len(compressed.strip().splitlines())

        assert line_count <= 11

    def test_enforces_char_limit(self):
        lines = "\n".join(f"- {'x' * 100} {idx}" for idx in range(50))
        config = CompactionConfig(summary_max_chars=500)
        compressed = _compress_summary(lines, config)

        assert len(compressed) <= 600

    def test_truncates_long_lines(self):
        long_line = "- Scope: " + "x" * 300
        config = CompactionConfig(line_max_chars=160)
        compressed = _compress_summary(long_line, config)

        for line in compressed.splitlines():
            assert len(line) <= 163

    def test_preserves_priority_lines(self):
        raw = (
            "- Key timeline:\n"
            "  - user: something\n"
            "  - assistant: something\n"
            "- Scope: 4 messages\n"
            "- Tools used: Read, Grep\n"
            "- Current work: analyzing\n"
        )
        config = CompactionConfig(summary_max_lines=4)
        compressed = _compress_summary(raw, config)

        assert "Scope:" in compressed
        assert "Tools used:" in compressed


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated_with_ellipsis(self):
        result = _truncate("a" * 200, 50)

        assert len(result) == 50
        assert result.endswith("...")

    def test_multiline_collapsed(self):
        result = _truncate("hello\nworld\nfoo", 100)

        assert "\n" not in result
        assert result == "hello world foo"
