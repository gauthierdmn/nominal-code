from __future__ import annotations

from dataclasses import dataclass, field

from nominal_code.llm.messages import Message, TextBlock

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

PRESERVE_RECENT_MESSAGES: int = 4


@dataclass(frozen=True)
class CompactionResult:
    """
    Result of a compaction attempt.

    Args:
        messages (list[Message]): The compacted (or unchanged) message list.
        summary_text (str): The notes content used as the summary, empty
            if no compaction occurred.
    """

    messages: list[Message] = field(default_factory=list)
    summary_text: str = ""


def _compacted_summary_prefix_len(messages: list[Message]) -> int:
    """
    Return 1 if the first message is an existing compaction summary, 0 otherwise.

    Allows ``compact_with_notes`` to skip a prior compaction message when
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


def compact_with_notes(
    messages: list[Message],
    notes_content: str,
) -> CompactionResult:
    """
    Compact a message list using pre-built notes as the summary.

    When ``notes_content`` is non-empty, replaces older messages with a
    continuation message containing the notes. The most recent
    ``PRESERVE_RECENT_MESSAGES`` messages are kept verbatim.

    When ``notes_content`` is empty (the agent has not written any notes
    yet), returns the messages unchanged so the caller can retry on the
    next turn.

    Args:
        messages (list[Message]): Current message history.
        notes_content (str): Structured notes written by the agent via
            the WriteNotes tool. Empty string means no notes available.

    Returns:
        CompactionResult: The compacted result.
    """

    if not notes_content.strip():
        return CompactionResult(messages=messages)

    prefix_len: int = _compacted_summary_prefix_len(messages)
    compactable: int = len(messages) - prefix_len

    if compactable <= PRESERVE_RECENT_MESSAGES:
        return CompactionResult(messages=messages)

    preserved: list[Message] = messages[-PRESERVE_RECENT_MESSAGES:]

    continuation_text: str = (
        f"{COMPACTION_MARKER}\n{CONTINUATION_PREAMBLE}{notes_content}"
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
        summary_text=notes_content,
    )
