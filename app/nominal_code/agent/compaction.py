from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from nominal_code.llm.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock

COMPACTION_MARKER: str = "[context compacted]"

CONTINUATION_PREAMBLE: str = (
    "This session is being continued from a previous conversation that ran out "
    "of context. The summary below covers the earlier portion of the conversation.\n\n"
)

COMPACT_RECENT_MESSAGES_NOTE: str = "Recent messages are preserved verbatim."

COMPACT_DIRECT_RESUME_INSTRUCTION: str = (
    "Continue the conversation from where it left off without asking the user "
    "any further questions. Resume directly \u2014 do not acknowledge the summary, "
    "do not recap what was happening, and do not preface with continuation text."
)

FILE_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"[\w./-]+\.(?:py|ts|tsx|js|jsx|json|md|yml|yaml|toml|cfg|sql|sh|rs|go|java)",
)

MAX_FILES_IN_SUMMARY: int = 8
MAX_RECENT_REQUESTS: int = 3
CURRENT_WORK_MAX_CHARS: int = 200
LINE_MAX_CHARS: int = 160
PRESERVE_RECENT_MESSAGES: int = 4


@dataclass(frozen=True)
class CompactionResult:
    """
    Result of a compaction attempt.

    Args:
        messages (list[Message]): The compacted (or unchanged) message list.
        summary_text (str): The generated summary text, empty if no compaction.
    """

    messages: list[Message] = field(default_factory=list)
    summary_text: str = ""


def _compacted_summary_prefix_len(messages: list[Message]) -> int:
    """
    Return 1 if the first message is an existing compaction summary, 0 otherwise.

    Allows ``compact_messages`` to skip a prior compaction message when
    deciding whether to re-compact.

    Args:
        messages (list[Message]): Current message history.

    Returns:
        int: 1 if the first message contains the compaction marker, 0 otherwise.
    """

    if not messages:
        return 0

    for block in messages[0].content:
        if isinstance(block, TextBlock) and COMPACTION_MARKER in block.text:
            return 1

    return 0


def compact_messages(messages: list[Message]) -> CompactionResult:
    """
    Compact a message list by summarising older messages.

    Preserves the most recent ``PRESERVE_RECENT_MESSAGES`` messages
    verbatim and replaces the older portion with a deterministic, rule-based
    summary. No LLM call is made.

    The caller is responsible for deciding *when* to compact (e.g. after
    cumulative input tokens exceed a threshold). This function only checks
    whether there are enough messages to split.

    Args:
        messages (list[Message]): Current message history.

    Returns:
        CompactionResult: The compacted result.
    """

    prefix_len: int = _compacted_summary_prefix_len(messages)
    compactable: int = len(messages) - prefix_len

    if compactable <= PRESERVE_RECENT_MESSAGES:
        return CompactionResult(messages=messages)

    existing_summary: str | None = _extract_prior_summary(messages)

    preserved: list[Message] = messages[-PRESERVE_RECENT_MESSAGES:]
    removed: list[Message] = messages[prefix_len:-PRESERVE_RECENT_MESSAGES]

    new_summary: str = _build_summary(removed)

    if existing_summary:
        summary_text: str = _merge_summaries(existing_summary, new_summary)
    else:
        summary_text = new_summary

    continuation_text: str = (
        f"{COMPACTION_MARKER}\n{CONTINUATION_PREAMBLE}{summary_text}"
    )

    if preserved:
        continuation_text += f"\n\n{COMPACT_RECENT_MESSAGES_NOTE}"

    continuation_text += f"\n{COMPACT_DIRECT_RESUME_INSTRUCTION}"

    continuation_message: Message = Message(
        role="system",
        content=[TextBlock(text=continuation_text)],
    )

    compacted: list[Message] = [continuation_message, *preserved]

    return CompactionResult(
        messages=compacted,
        summary_text=summary_text,
    )


def _extract_prior_summary(messages: list[Message]) -> str | None:
    """
    Extract a prior compaction summary from the first message.

    If the first message contains the compaction marker, extracts the
    summary text that follows the continuation preamble, stripping any
    appended continuation instructions.

    Args:
        messages (list[Message]): The current message history.

    Returns:
        str | None: The prior summary text, or None if not found.
    """

    if not messages:
        return None

    first: Message = messages[0]

    for block in first.content:
        if not isinstance(block, TextBlock):
            continue

        if COMPACTION_MARKER not in block.text:
            continue

        preamble_end: int = block.text.find(CONTINUATION_PREAMBLE)

        if preamble_end >= 0:
            start: int = preamble_end + len(CONTINUATION_PREAMBLE)
            summary: str = block.text[start:]
        else:
            marker_end: int = block.text.find(COMPACTION_MARKER) + len(
                COMPACTION_MARKER
            )
            summary = block.text[marker_end:]

        summary = summary.split(f"\n\n{COMPACT_RECENT_MESSAGES_NOTE}")[0]
        summary = summary.split(f"\n{COMPACT_DIRECT_RESUME_INSTRUCTION}")[0]

        return summary.strip()

    return None


def _build_summary(removed: list[Message]) -> str:
    """
    Build a rule-based summary from removed messages.

    Extracts scope, tools used, recent user requests, files referenced,
    current work status, and a key timeline from the message history.

    Args:
        removed (list[Message]): Messages being removed.

    Returns:
        str: The summary text.
    """

    user_count: int = sum(1 for msg in removed if msg.role == "user")
    assistant_count: int = sum(1 for msg in removed if msg.role == "assistant")

    sections: list[str] = []

    scope: str = (
        f"Compacted {len(removed)} messages "
        f"({user_count} user, {assistant_count} assistant)."
    )

    sections.append(f"- Scope: {scope}")

    tools: set[str] = set()
    files: set[str] = set()

    for message in removed:
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tools.add(block.name)
                _extract_files_from_tool_input(block, files)
            elif isinstance(block, TextBlock):
                _extract_files_from_text(block.text, files)

    if tools:
        sections.append(f"- Tools used: {', '.join(sorted(tools))}")

    recent_requests: list[str] = _extract_recent_requests(removed)

    if recent_requests:
        sections.append("- Recent requests:")

        for request in recent_requests:
            sections.append(f"  - {request}")

    if files:
        sorted_files: list[str] = sorted(files)[:MAX_FILES_IN_SUMMARY]
        sections.append(f"- Key files: {', '.join(sorted_files)}")

    current_work: str = _extract_current_work(removed)

    if current_work:
        sections.append(f"- Current work: {current_work}")

    timeline: list[str] = _build_timeline(removed)

    if timeline:
        sections.append("- Key timeline:")

        for entry in timeline:
            sections.append(f"  - {entry}")

    return "\n".join(sections)


def _extract_files_from_tool_input(block: ToolUseBlock, files: set[str]) -> None:
    """
    Extract file paths from a tool use block's input parameters.

    Args:
        block (ToolUseBlock): The tool use block.
        files (set[str]): Accumulator for discovered file paths.
    """

    file_path: Any = block.input.get("file_path")

    if isinstance(file_path, str) and file_path:
        files.add(file_path)

    pattern: Any = block.input.get("pattern")

    if isinstance(pattern, str) and "/" in pattern:
        for match in FILE_PATH_PATTERN.findall(pattern):
            files.add(match)

    command: Any = block.input.get("command")

    if isinstance(command, str):
        for match in FILE_PATH_PATTERN.findall(command):
            files.add(match)


def _extract_files_from_text(text: str, files: set[str]) -> None:
    """
    Extract file paths from free text using regex.

    Args:
        text (str): The text to scan.
        files (set[str]): Accumulator for discovered file paths.
    """

    for match in FILE_PATH_PATTERN.findall(text):
        if "/" in match:
            files.add(match)


def _extract_recent_requests(removed: list[Message]) -> list[str]:
    """
    Extract the most recent user text requests from removed messages.

    Skips messages that contain only tool result blocks.

    Args:
        removed (list[Message]): Messages being removed.

    Returns:
        list[str]: Up to MAX_RECENT_REQUESTS truncated request strings.
    """

    requests: list[str] = []

    for message in reversed(removed):
        if message.role != "user":
            continue

        text_parts: list[str] = [
            block.text
            for block in message.content
            if isinstance(block, TextBlock) and block.text.strip()
        ]

        if not text_parts:
            continue

        combined: str = " ".join(text_parts).strip()

        if COMPACTION_MARKER in combined:
            continue

        requests.append(_truncate(combined, LINE_MAX_CHARS))

        if len(requests) >= MAX_RECENT_REQUESTS:
            break

    requests.reverse()

    return requests


def _extract_current_work(removed: list[Message]) -> str:
    """
    Extract the most recent non-empty text as current work.

    Searches all message roles, matching the Rust reference
    ``infer_current_work`` which iterates without role filtering.

    Args:
        removed (list[Message]): Messages being removed.

    Returns:
        str: Truncated current work description, or empty string.
    """

    for message in reversed(removed):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                return _truncate(block.text.strip(), CURRENT_WORK_MAX_CHARS)

    return ""


def _build_timeline(removed: list[Message]) -> list[str]:
    """
    Build a chronological timeline from removed messages.

    Takes the first text block from each message, prefixed by role.

    Args:
        removed (list[Message]): Messages being removed.

    Returns:
        list[str]: Timeline entries.
    """

    timeline: list[str] = []

    for message in removed:
        first_text: str = _first_text_block(message)

        if first_text:
            truncated: str = _truncate(first_text, LINE_MAX_CHARS)
            entry: str = f"{message.role}: {truncated}"
            timeline.append(entry)
            continue

        tool_summary: str = _first_tool_summary(message)

        if tool_summary:
            entry = f"{message.role}: {_truncate(tool_summary, LINE_MAX_CHARS)}"
            timeline.append(entry)

    return timeline


def _first_text_block(message: Message) -> str:
    """
    Get the text from the first TextBlock in a message.

    Args:
        message (Message): The message to inspect.

    Returns:
        str: The text content, or empty string if none found.
    """

    for block in message.content:
        if isinstance(block, TextBlock) and block.text.strip():
            return block.text.strip()

    return ""


def _first_tool_summary(message: Message) -> str:
    """
    Build a short summary from the first tool block in a message.

    Args:
        message (Message): The message to inspect.

    Returns:
        str: A tool summary string, or empty string if none found.
    """

    for block in message.content:
        if isinstance(block, ToolUseBlock):
            return f"tool_use {block.name}"

        if isinstance(block, ToolResultBlock):
            prefix: str = "error " if block.is_error else ""

            return f"tool_result: {prefix}{block.content[:80]}"

    return ""


def _extract_summary_highlights(summary: str) -> list[str]:
    """
    Extract non-timeline highlight lines from a summary.

    Skips the ``- Key timeline:`` header and all indented entries below it.

    Args:
        summary (str): The summary text.

    Returns:
        list[str]: Highlight lines (everything except timeline).
    """

    lines: list[str] = []
    in_timeline: bool = False

    for line in summary.splitlines():
        trimmed: str = line.rstrip()

        if not trimmed:
            continue

        if trimmed == "- Key timeline:":
            in_timeline = True
            continue

        if in_timeline:
            continue

        lines.append(trimmed)

    return lines


def _extract_summary_timeline(summary: str) -> list[str]:
    """
    Extract timeline entries from a summary.

    Returns lines that appear after the ``- Key timeline:`` header.

    Args:
        summary (str): The summary text.

    Returns:
        list[str]: Timeline entry lines.
    """

    lines: list[str] = []
    in_timeline: bool = False

    for line in summary.splitlines():
        trimmed: str = line.rstrip()

        if trimmed == "- Key timeline:":
            in_timeline = True
            continue

        if not in_timeline:
            continue

        if not trimmed:
            break

        lines.append(trimmed)

    return lines


def _merge_summaries(existing_summary: str, new_summary: str) -> str:
    """
    Merge an existing compaction summary with a new one.

    Creates three sections: previously compacted context (highlights from
    the existing summary), newly compacted context (highlights from the
    new summary), and key timeline (from the new summary only).

    Args:
        existing_summary (str): The prior compaction summary text.
        new_summary (str): The newly generated summary text.

    Returns:
        str: The merged summary.
    """

    previous_highlights: list[str] = _extract_summary_highlights(existing_summary)
    new_highlights: list[str] = _extract_summary_highlights(new_summary)
    new_timeline: list[str] = _extract_summary_timeline(new_summary)

    sections: list[str] = []

    if previous_highlights:
        sections.append("- Previously compacted context:")

        for line in previous_highlights:
            sections.append(f"  {line}")

    if new_highlights:
        sections.append("- Newly compacted context:")

        for line in new_highlights:
            sections.append(f"  {line}")

    if new_timeline:
        sections.append("- Key timeline:")

        for line in new_timeline:
            sections.append(f"  {line}")

    return "\n".join(sections)


def _truncate(text: str, max_chars: int) -> str:
    """
    Truncate text to a maximum length, adding an ellipsis if needed.

    Collapses the text to a single line before truncating.

    Args:
        text (str): The text to truncate.
        max_chars (int): Maximum allowed characters.

    Returns:
        str: The truncated text.
    """

    single_line: str = " ".join(text.split())

    if len(single_line) <= max_chars:
        return single_line

    return single_line[: max_chars - 3] + "..."
