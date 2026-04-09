from __future__ import annotations

import json
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

PENDING_KEYWORDS: re.Pattern[str] = re.compile(
    r"\b(?:todo|next|pending|follow\s*up|remaining)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompactionConfig:
    """
    Configuration for session-level message compaction.

    Args:
        preserve_recent_messages (int): Number of recent messages to keep
            verbatim after compaction.
        max_estimated_tokens (int): Token threshold above which compaction
            triggers for the removable (older) portion of messages.
        summary_max_chars (int): Maximum characters in the compressed summary.
        summary_max_lines (int): Maximum lines in the compressed summary.
        line_max_chars (int): Maximum characters per summary line.
    """

    preserve_recent_messages: int = 4
    max_estimated_tokens: int = 10_000
    summary_max_chars: int = 1_200
    summary_max_lines: int = 24
    line_max_chars: int = 160


@dataclass(frozen=True)
class CompactionResult:
    """
    Result of a compaction attempt.

    Args:
        messages (list[Message]): The compacted (or unchanged) message list.
        did_compact (bool): Whether compaction actually occurred.
        removed_count (int): Number of messages removed.
        summary_text (str): The generated summary text, empty if no compaction.
    """

    messages: list[Message] = field(default_factory=list)
    did_compact: bool = False
    removed_count: int = 0
    summary_text: str = ""


def _compacted_summary_prefix_len(messages: list[Message]) -> int:
    """
    Return 1 if the first message is an existing compaction summary, 0 otherwise.

    Allows ``should_compact`` and ``compact_messages`` to skip a prior
    compaction message when deciding whether to re-compact.

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


def estimate_message_tokens(messages: list[Message]) -> int:
    """
    Estimate token count for a list of messages using a character heuristic.

    Uses ``len(text) // 4 + 1`` per content block, matching the approach
    used in claw-code's compaction system.

    Args:
        messages (list[Message]): Messages to estimate.

    Returns:
        int: Estimated token count.
    """

    total: int = 0

    for message in messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                total += len(block.text) // 4 + 1
            elif isinstance(block, ToolUseBlock):
                serialized: str = json.dumps(block.input, separators=(",", ":"))
                total += (len(block.name) + len(serialized)) // 4 + 1
            elif isinstance(block, ToolResultBlock):
                total += (len(block.tool_use_id) + len(block.content)) // 4 + 1

    return total


def should_compact(
    messages: list[Message],
    config: CompactionConfig,
) -> bool:
    """
    Decide whether the message list should be compacted.

    Returns ``True`` when there are enough messages to preserve the recent
    tail and the older portion exceeds the token threshold.

    Args:
        messages (list[Message]): Current message history.
        config (CompactionConfig): Compaction settings.

    Returns:
        bool: Whether compaction should fire.
    """

    start: int = _compacted_summary_prefix_len(messages)
    compactable: list[Message] = messages[start:]

    if len(compactable) <= config.preserve_recent_messages:
        return False

    older: list[Message] = compactable[: -config.preserve_recent_messages]

    return estimate_message_tokens(older) >= config.max_estimated_tokens


def compact_messages(
    messages: list[Message],
    config: CompactionConfig,
) -> CompactionResult:
    """
    Compact a message list by summarising older messages.

    Preserves the most recent ``config.preserve_recent_messages`` messages
    verbatim and replaces the older portion with a deterministic, rule-based
    summary. No LLM call is made.

    If the messages do not meet the compaction threshold, returns a no-op
    result with the original messages unchanged.

    Args:
        messages (list[Message]): Current message history.
        config (CompactionConfig): Compaction settings.

    Returns:
        CompactionResult: The compacted result.
    """

    if not should_compact(messages, config):
        return CompactionResult(messages=messages)

    existing_summary: str | None = _extract_prior_summary(messages)
    prefix_len: int = _compacted_summary_prefix_len(messages)

    preserved: list[Message] = messages[-config.preserve_recent_messages :]
    removed: list[Message] = messages[prefix_len : -config.preserve_recent_messages]

    new_summary: str = _build_summary(removed, config)

    if existing_summary:
        raw_summary: str = _merge_summaries(existing_summary, new_summary)
    else:
        raw_summary = new_summary

    compressed: str = _compress_summary(raw_summary, config)

    continuation_text: str = f"{COMPACTION_MARKER}\n{CONTINUATION_PREAMBLE}{compressed}"

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
        did_compact=True,
        removed_count=len(removed),
        summary_text=compressed,
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


def _build_summary(
    removed: list[Message],
    config: CompactionConfig,
) -> str:
    """
    Build a rule-based summary from removed messages.

    Extracts scope, tools used, recent user requests, pending work,
    files referenced, current work status, and a key timeline from
    the message history.

    Args:
        removed (list[Message]): Messages being removed.
        config (CompactionConfig): Compaction settings.

    Returns:
        str: The raw summary text (before compression).
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

    recent_requests: list[str] = _extract_recent_requests(removed, config)

    if recent_requests:
        sections.append("- Recent requests:")

        for request in recent_requests:
            sections.append(f"  - {request}")

    pending: list[str] = _extract_pending_work(removed, config)

    if pending:
        sections.append("- Pending work:")

        for item in pending:
            sections.append(f"  - {item}")

    if files:
        sorted_files: list[str] = sorted(files)[:MAX_FILES_IN_SUMMARY]
        sections.append(f"- Key files: {', '.join(sorted_files)}")

    current_work: str = _extract_current_work(removed)

    if current_work:
        sections.append(f"- Current work: {current_work}")

    timeline: list[str] = _build_timeline(removed, config)

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


def _extract_recent_requests(
    removed: list[Message],
    config: CompactionConfig,
) -> list[str]:
    """
    Extract the most recent user text requests from removed messages.

    Skips messages that contain only tool result blocks.

    Args:
        removed (list[Message]): Messages being removed.
        config (CompactionConfig): Compaction settings.

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

        requests.append(_truncate(combined, config.line_max_chars))

        if len(requests) >= MAX_RECENT_REQUESTS:
            break

    requests.reverse()

    return requests


def _extract_pending_work(
    removed: list[Message],
    config: CompactionConfig,
) -> list[str]:
    """
    Extract lines mentioning pending or future work from removed messages.

    Scans text blocks for keywords like "todo", "next", "pending", etc.

    Args:
        removed (list[Message]): Messages being removed.
        config (CompactionConfig): Compaction settings.

    Returns:
        list[str]: Unique pending work items, truncated.
    """

    seen: set[str] = set()
    pending: list[str] = []

    for message in reversed(removed):
        for block in message.content:
            if not isinstance(block, TextBlock):
                continue

            for line in block.text.splitlines():
                stripped: str = line.strip()

                if not stripped:
                    continue

                if not PENDING_KEYWORDS.search(stripped):
                    continue

                dedup_key: str = stripped.lower()

                if dedup_key in seen:
                    continue

                seen.add(dedup_key)
                pending.append(_truncate(stripped, config.line_max_chars))

                if len(pending) >= MAX_RECENT_REQUESTS:
                    return pending

    return pending


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


def _build_timeline(
    removed: list[Message],
    config: CompactionConfig,
) -> list[str]:
    """
    Build a chronological timeline from removed messages.

    Takes the first text block from each message, prefixed by role.

    Args:
        removed (list[Message]): Messages being removed.
        config (CompactionConfig): Compaction settings.

    Returns:
        list[str]: Timeline entries.
    """

    timeline: list[str] = []

    for message in removed:
        first_text: str = _first_text_block(message)

        if first_text:
            truncated: str = _truncate(first_text, config.line_max_chars)
            entry: str = f"{message.role}: {truncated}"
            timeline.append(entry)
            continue

        tool_summary: str = _first_tool_summary(message)

        if tool_summary:
            entry = f"{message.role}: {_truncate(tool_summary, config.line_max_chars)}"
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


def _compress_summary(raw_summary: str, config: CompactionConfig) -> str:
    """
    Compress a raw summary by deduplicating and enforcing budget limits.

    Applies line-level deduplication (case-insensitive), truncates lines,
    and selects lines by priority to fit within character and line budgets.

    Args:
        raw_summary (str): The uncompressed summary text.
        config (CompactionConfig): Compaction settings.

    Returns:
        str: The compressed summary.
    """

    seen: set[str] = set()
    unique_lines: list[str] = []

    for raw_line in raw_summary.splitlines():
        normalized: str = " ".join(raw_line.split())

        if not normalized:
            continue

        truncated: str = _truncate(normalized, config.line_max_chars)
        dedup_key: str = truncated.lower().strip()

        if dedup_key in seen:
            continue

        seen.add(dedup_key)
        unique_lines.append(truncated)

    selected: list[str] = _select_lines_by_priority(
        unique_lines,
        config.summary_max_lines,
        config.summary_max_chars,
    )

    omitted_count: int = len(unique_lines) - len(selected)

    if omitted_count > 0:
        selected.append(f"- ... {omitted_count} additional line(s) omitted.")

    return "\n".join(selected)


_PRIORITY_PREFIXES: list[list[str]] = [
    ["- Scope:", "- Tools used:", "- Current work:"],
    ["- Recent requests:", "- Pending work:", "- Key files:"],
    [
        "- Previously compacted context:",
        "- Newly compacted context:",
        "- Key timeline:",
    ],
]


def _select_lines_by_priority(
    lines: list[str],
    max_lines: int,
    max_chars: int,
) -> list[str]:
    """
    Select lines by priority tier to fit within budget constraints.

    Priority 0 (highest): scope, tools, current work.
    Priority 1: key files, pending work, recent requests.
    Priority 2: previously compacted context, key timeline.
    Priority 3 (lowest): everything else (sub-items, etc.).

    Args:
        lines (list[str]): Deduplicated summary lines.
        max_lines (int): Maximum number of lines to include.
        max_chars (int): Maximum total characters.

    Returns:
        list[str]: Selected lines within budget.
    """

    selected: list[str] = []
    total_chars: int = 0
    used_indexes: set[int] = set()

    for priority_prefixes in _PRIORITY_PREFIXES:
        for index, line in enumerate(lines):
            if index in used_indexes:
                continue

            if not any(line.startswith(prefix) for prefix in priority_prefixes):
                continue

            if len(selected) >= max_lines:
                break

            if total_chars + len(line) + 1 > max_chars:
                break

            selected.append(line)
            total_chars += len(line) + 1
            used_indexes.add(index)

            _add_sub_items(
                lines,
                index,
                used_indexes,
                selected,
                max_lines,
                max_chars,
                total_chars,
            )
            total_chars = sum(len(item) + 1 for item in selected)

    for index, line in enumerate(lines):
        if index in used_indexes:
            continue

        if len(selected) >= max_lines:
            break

        if total_chars + len(line) + 1 > max_chars:
            break

        selected.append(line)
        total_chars += len(line) + 1
        used_indexes.add(index)

    return selected


def _add_sub_items(
    lines: list[str],
    parent_index: int,
    used_indexes: set[int],
    selected: list[str],
    max_lines: int,
    max_chars: int,
    total_chars: int,
) -> None:
    """
    Add indented sub-items that follow a parent line.

    Args:
        lines (list[str]): All summary lines.
        parent_index (int): Index of the parent line.
        used_indexes (set[int]): Already selected indexes.
        selected (list[str]): Accumulator of selected lines.
        max_lines (int): Maximum number of lines.
        max_chars (int): Maximum total characters.
        total_chars (int): Current total character count.
    """

    for sub_index in range(parent_index + 1, len(lines)):
        sub_line: str = lines[sub_index]

        if not sub_line.startswith("  "):
            break

        if sub_index in used_indexes:
            continue

        if len(selected) >= max_lines:
            break

        if total_chars + len(sub_line) + 1 > max_chars:
            break

        selected.append(sub_line)
        total_chars += len(sub_line) + 1
        used_indexes.add(sub_index)


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
