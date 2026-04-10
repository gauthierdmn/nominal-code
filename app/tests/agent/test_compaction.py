# type: ignore
from nominal_code.agent.compaction import (
    COMPACT_DIRECT_RESUME_INSTRUCTION,
    COMPACT_RECENT_MESSAGES_NOTE,
    COMPACTION_MARKER,
    _build_summary,
    _compacted_summary_prefix_len,
    _extract_summary_highlights,
    _extract_summary_timeline,
    _merge_summaries,
    _truncate,
    compact_messages,
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


class TestCompactMessages:
    def test_noop_when_too_few_messages(self):
        messages = [_user_text("hello"), _assistant_text("world")]
        result = compact_messages(messages)

        assert result.messages is messages
        assert result.summary_text == ""

    def test_noop_when_only_compaction_prefix_and_recent(self):
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
        result = compact_messages(messages)

        assert result.messages is messages

    def test_preserves_recent_messages(self):
        recent_user = _user_text("recent_user")
        recent_assistant = _assistant_text("recent_assistant")
        messages = [
            _user_text("old content"),
            _assistant_text("old response"),
            _tool_use_message("t1", "Read", {"file_path": "/tmp/foo.py"}),
            _tool_result_message("t1", "file contents"),
            _user_text("old2"),
            _assistant_text("old2"),
            recent_user,
            recent_assistant,
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        result = compact_messages(messages)

        assert len(result.messages) == 5
        assert result.messages[-4] is recent_user
        assert result.messages[-3] is recent_assistant

    def test_continuation_message_has_marker(self):
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
        result = compact_messages(messages)

        continuation = result.messages[0]

        assert continuation.role == "system"
        assert len(continuation.content) == 1
        assert isinstance(continuation.content[0], TextBlock)
        assert COMPACTION_MARKER in continuation.content[0].text

    def test_continuation_message_has_instructions(self):
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
        result = compact_messages(messages)

        continuation_text = result.messages[0].content[0].text

        assert COMPACT_RECENT_MESSAGES_NOTE in continuation_text
        assert COMPACT_DIRECT_RESUME_INSTRUCTION in continuation_text

    def test_summary_contains_scope(self):
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
        result = compact_messages(messages)

        assert "Compacted 4 messages" in result.summary_text

    def test_summary_contains_tools(self):
        messages = [
            _user_text("explore the code"),
            _tool_use_message("t1", "Read", {"file_path": "/tmp/foo.py"}),
            _tool_result_message("t1", "file contents"),
            _tool_use_message("t2", "Grep", {"pattern": "def main"}),
            _tool_result_message("t2", "grep output"),
            _user_text("recent1"),
            _assistant_text("recent2"),
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        result = compact_messages(messages)

        assert "Read" in result.summary_text
        assert "Grep" in result.summary_text

    def test_summary_contains_file_paths(self):
        messages = [
            _user_text("check the file"),
            _tool_use_message("t1", "Read", {"file_path": "src/main.py"}),
            _tool_result_message("t1", "file contents"),
            _user_text("recent1"),
            _assistant_text("recent2"),
            _user_text("recent3"),
            _assistant_text("recent4"),
        ]
        result = compact_messages(messages)

        assert "src/main.py" in result.summary_text

    def test_recompaction_merges_prior_summary(self):
        first_messages = [
            _user_text("old content"),
            _assistant_text("old response"),
            _user_text("old2"),
            _assistant_text("old2"),
            _user_text("middle1"),
            _assistant_text("middle2"),
            _user_text("middle3"),
            _assistant_text("middle4"),
        ]
        first_result = compact_messages(first_messages)

        extended = [
            *first_result.messages,
            _user_text("new content"),
            _assistant_text("new response"),
            _user_text("new2"),
            _assistant_text("new2"),
            _user_text("latest1"),
            _assistant_text("latest2"),
            _user_text("latest3"),
            _assistant_text("latest4"),
        ]
        second_result = compact_messages(extended)

        assert "Previously compacted context" in second_result.summary_text
        assert "Newly compacted context" in second_result.summary_text


class TestBuildSummary:
    def test_extracts_tools_from_removed(self):
        removed = [
            _tool_use_message("t1", "Read", {"file_path": "/a.py"}),
            _tool_result_message("t1", "content"),
            _tool_use_message("t2", "Bash", {"command": "git show HEAD"}),
            _tool_result_message("t2", "output"),
        ]
        summary = _build_summary(removed)

        assert "Bash" in summary
        assert "Read" in summary

    def test_extracts_recent_user_requests(self):
        removed = [
            _user_text("Please investigate the auth module"),
            _assistant_text("I'll look into it"),
            _user_text("Also check the tests"),
            _assistant_text("Checking now"),
        ]
        summary = _build_summary(removed)

        assert "investigate the auth module" in summary
        assert "check the tests" in summary

    def test_extracts_current_work(self):
        removed = [
            _user_text("prompt"),
            _assistant_text("I am analyzing the service layer for bugs"),
        ]
        summary = _build_summary(removed)

        assert "analyzing the service layer" in summary

    def test_current_work_searches_all_roles(self):
        removed = [
            _assistant_text("older assistant text"),
            _user_text("latest user text"),
        ]
        summary = _build_summary(removed)

        assert "latest user text" in summary

    def test_file_paths_from_tool_input(self):
        removed = [
            _tool_use_message("t1", "Read", {"file_path": "lib/utils.py"}),
            _tool_result_message("t1", "content"),
        ]
        summary = _build_summary(removed)

        assert "lib/utils.py" in summary

    def test_skips_compaction_marker_in_requests(self):
        removed = [
            _user_text(f"{COMPACTION_MARKER}\nOld summary text"),
            _assistant_text("response"),
            _user_text("real user request"),
            _assistant_text("real response"),
        ]
        summary = _build_summary(removed)

        assert "Recent requests" in summary
        assert "real user request" in summary


class TestExtractSummaryParts:
    def test_highlights_excludes_timeline(self):
        summary = (
            "- Scope: 4 messages\n"
            "- Tools used: Read\n"
            "- Key timeline:\n"
            "  - user: hello\n"
            "  - assistant: world"
        )
        highlights = _extract_summary_highlights(summary)

        assert "- Scope: 4 messages" in highlights
        assert "- Tools used: Read" in highlights
        assert not any("user:" in line for line in highlights)
        assert not any("assistant:" in line for line in highlights)

    def test_timeline_extraction(self):
        summary = (
            "- Scope: 4 messages\n"
            "- Key timeline:\n"
            "  - user: hello\n"
            "  - assistant: world"
        )
        timeline = _extract_summary_timeline(summary)

        assert len(timeline) == 2
        assert "user: hello" in timeline[0]
        assert "assistant: world" in timeline[1]

    def test_timeline_stops_at_blank_line(self):
        summary = "- Key timeline:\n  - user: hello\n\n- Other section:"
        timeline = _extract_summary_timeline(summary)

        assert len(timeline) == 1

    def test_highlights_empty_when_only_timeline(self):
        summary = "- Key timeline:\n  - user: hello"
        highlights = _extract_summary_highlights(summary)

        assert highlights == []


class TestMergeSummaries:
    def test_creates_three_sections(self):
        existing = (
            "- Scope: Compacted 4 messages\n"
            "- Tools used: Read\n"
            "- Key timeline:\n"
            "  - user: hello"
        )
        new = (
            "- Scope: Compacted 2 messages\n"
            "- Current work: analyzing\n"
            "- Key timeline:\n"
            "  - user: world"
        )
        merged = _merge_summaries(existing, new)

        assert "Previously compacted context" in merged
        assert "Newly compacted context" in merged
        assert "Key timeline" in merged

    def test_timeline_comes_from_new_summary_only(self):
        existing = "- Scope: old\n- Key timeline:\n  - user: old event"
        new = "- Scope: new\n- Key timeline:\n  - user: new event"
        merged = _merge_summaries(existing, new)

        assert "new event" in merged
        timeline_start = merged.index("- Key timeline:")
        timeline_section = merged[timeline_start:]

        assert "old event" not in timeline_section

    def test_excludes_timeline_from_highlights(self):
        existing = "- Scope: old scope\n- Key timeline:\n  - user: old event"
        new = "- Scope: new scope"
        merged = _merge_summaries(existing, new)

        prev_section_start = merged.index("- Previously compacted context:")
        prev_section_end = merged.index("- Newly compacted context:")
        prev_section = merged[prev_section_start:prev_section_end]

        assert "old event" not in prev_section
        assert "old scope" in prev_section


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
