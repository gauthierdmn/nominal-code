from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

PRKey = tuple[str, str, int, str]


class EventType(StrEnum):
    """
    Discriminator for all PR/MR events the bot handles.

    Covers both comment events (triggered by @mentions) and lifecycle
    events (triggered by PR state changes).
    """

    ISSUE_COMMENT = "issue_comment"
    REVIEW_COMMENT = "review_comment"
    REVIEW = "review"
    NOTE = "note"
    PR_OPENED = "pr_opened"
    PR_PUSH = "pr_push"
    PR_REOPENED = "pr_reopened"
    PR_READY_FOR_REVIEW = "pr_ready_for_review"


COMMENT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.ISSUE_COMMENT,
        EventType.REVIEW_COMMENT,
        EventType.REVIEW,
        EventType.NOTE,
    }
)


class ProviderName(StrEnum):
    """
    Supported LLM provider identifiers.
    """

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"
    GROQ = "groq"
    TOGETHER = "together"
    FIREWORKS = "fireworks"


class DiffSide(StrEnum):
    """
    Which side of a diff a review comment applies to.
    """

    LEFT = "LEFT"
    RIGHT = "RIGHT"


class FileStatus(StrEnum):
    """
    Change type for a file in a pull request or merge request.
    """

    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"
    COPIED = "copied"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class ErrorType(StrEnum):
    """
    Classification of an agent / review failure.

    Values:
        PROVIDER_ERROR: LLM provider raised an error.
        RUNTIME_ERROR: Unexpected exception escaped the agent loop
            (tool dispatch failure, parsing crash, etc.).
        PARSE_ERROR: Agent returned successfully but its output could
            not be parsed as a structured review and JSON repair did
            not recover it.
    """

    PROVIDER_ERROR = "provider_error"
    RUNTIME_ERROR = "runtime_error"
    PARSE_ERROR = "parse_error"


@dataclass(frozen=True)
class InvocationError:
    """
    Failure produced by an agent invocation or a review build.

    Attributes:
        type (ErrorType): Classification of the failure.
        message (str): Underlying exception text or short description
            of the failure, preserved verbatim so callers can route it
            to logs / metrics.
    """

    type: ErrorType
    message: str = ""


@dataclass(frozen=True)
class ReviewFinding:
    """
    A single inline comment produced by the reviewer bot.

    Attributes:
        file_path (str): File path relative to repository root.
        line (int): Line number in the new version of the file.
        body (str): The review comment text.
        side (DiffSide): Which side of the diff the comment applies to.
        suggestion (str | None): Replacement code for a suggestion comment.
            When set, the finding is rendered as a one-click-apply suggestion.
        start_line (int | None): First line of a multi-line replacement range.
            ``line`` is the last line. When ``None``, the suggestion targets
            only ``line``.
    """

    file_path: str
    line: int
    body: str
    side: DiffSide = DiffSide.RIGHT
    suggestion: str | None = None
    start_line: int | None = None


@dataclass(frozen=True)
class AgentReview:
    """
    Structured output from a reviewer bot invocation.

    Attributes:
        summary (str): High-level summary of the review.
        findings (list[ReviewFinding]): Inline comments on specific lines.
    """

    summary: str
    findings: list[ReviewFinding] = field(default_factory=list)


@dataclass(frozen=True)
class ChangedFile:
    """
    A file changed in a pull request or merge request.

    Attributes:
        file_path (str): File path relative to repository root.
        status (FileStatus): Change type (added, modified, removed, renamed, etc.).
        patch (str): Unified diff text for the file.
    """

    file_path: str
    status: FileStatus
    patch: str
