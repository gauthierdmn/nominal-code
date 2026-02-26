from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BotType(StrEnum):
    """
    Identifiers for the two bot personalities.
    """

    WORKER = "worker"
    REVIEWER = "reviewer"


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


@dataclass(frozen=True)
class ReviewFinding:
    """
    A single inline comment produced by the reviewer bot.

    Attributes:
        file_path (str): File path relative to repository root.
        line (int): Line number in the new version of the file.
        body (str): The review comment text.
        side (DiffSide): Which side of the diff the comment applies to.
    """

    file_path: str
    line: int
    body: str
    side: DiffSide = DiffSide.RIGHT


@dataclass(frozen=True)
class ReviewResult:
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
