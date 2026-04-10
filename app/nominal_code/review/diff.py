from __future__ import annotations

import re

from nominal_code.models import ChangedFile, DiffSide, ReviewFinding

HUNK_HEADER_PATTERN: re.Pattern[str] = re.compile(
    r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@",
)


def annotate_diff(patch: str) -> str:
    """
    Transform a unified diff into a line-annotated format.

    Each line is prefixed with its actual line number in the file,
    removing the need to count through hunk headers. Context and
    added lines show the new-file line number. Removed lines show
    the old-file line number. Hunk headers are preserved for
    orientation (function/class context).

    Args:
        patch (str): Unified diff text for a single file.

    Returns:
        str: The annotated diff, or empty string if patch is empty.
    """

    if not patch.strip():
        return ""

    output_lines: list[str] = []
    old_line: int = 0
    new_line: int = 0

    for raw_line in patch.splitlines():
        header_match: re.Match[str] | None = HUNK_HEADER_PATTERN.match(raw_line)

        if header_match:
            old_line = int(header_match.group(1))
            new_line = int(header_match.group(2))
            output_lines.append(raw_line)
            continue

        if old_line == 0 and new_line == 0:
            continue

        if raw_line.startswith("-"):
            content: str = raw_line[1:]
            output_lines.append(f"-{old_line}:{content}")
            old_line += 1
        elif raw_line.startswith("+"):
            content = raw_line[1:]
            output_lines.append(f"+{new_line}:{content}")
            new_line += 1
        else:
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            output_lines.append(f" {new_line}:{content}")
            old_line += 1
            new_line += 1

    return "\n".join(output_lines)


def parse_diff_lines(patch: str) -> dict[DiffSide, set[int]]:
    """
    Extract the sets of line numbers present in a unified diff, by side.

    Parses hunk headers and walks the diff lines to collect every line
    number on the LEFT side (deletions and context lines) and the RIGHT
    side (additions and context lines).

    Args:
        patch (str): Unified diff text for a single file.

    Returns:
        dict[DiffSide, set[int]]: Mapping from side to line numbers that
            appear on that side of the diff.
    """

    left_lines: set[int] = set()
    right_lines: set[int] = set()
    current_left: int = 0
    current_right: int = 0

    for raw_line in patch.splitlines():
        header_match: re.Match[str] | None = HUNK_HEADER_PATTERN.match(raw_line)

        if header_match:
            current_left = int(header_match.group(1))
            current_right = int(header_match.group(2))
            continue

        if current_left == 0 and current_right == 0:
            continue

        if raw_line.startswith("-"):
            left_lines.add(current_left)
            current_left += 1
        elif raw_line.startswith("+"):
            right_lines.add(current_right)
            current_right += 1
        else:
            left_lines.add(current_left)
            right_lines.add(current_right)
            current_left += 1
            current_right += 1

    return {DiffSide.LEFT: left_lines, DiffSide.RIGHT: right_lines}


def build_diff_index(
    changed_files: list[ChangedFile],
) -> dict[str, dict[DiffSide, set[int]]]:
    """
    Build a mapping from file path to the set of valid diff line numbers per side.

    Args:
        changed_files (list[ChangedFile]): Files changed in the PR.

    Returns:
        dict[str, dict[DiffSide, set[int]]]: File paths mapped to their
            valid lines keyed by diff side.
    """

    index: dict[str, dict[DiffSide, set[int]]] = {}

    for changed_file in changed_files:
        if changed_file.patch:
            index[changed_file.file_path] = parse_diff_lines(changed_file.patch)

    return index


def filter_findings(
    findings: list[ReviewFinding],
    changed_files: list[ChangedFile],
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    """
    Split findings into those targeting valid diff lines and those that don't.

    Args:
        findings (list[ReviewFinding]): All findings from the agent.
        changed_files (list[ChangedFile]): Files changed in the PR.

    Returns:
        tuple[list[ReviewFinding], list[ReviewFinding]]: A pair of
            (valid, rejected) findings.
    """

    diff_index: dict[str, dict[DiffSide, set[int]]] = build_diff_index(
        changed_files=changed_files,
    )
    valid: list[ReviewFinding] = []
    rejected: list[ReviewFinding] = []

    for finding in findings:
        file_sides: dict[DiffSide, set[int]] | None = diff_index.get(finding.file_path)

        if file_sides is None:
            rejected.append(finding)
            continue

        valid_lines: set[int] = file_sides.get(finding.side, set())

        if finding.start_line is not None:
            required_lines: range = range(finding.start_line, finding.line + 1)

            if all(ln in valid_lines for ln in required_lines):
                valid.append(finding)
            else:
                rejected.append(finding)
        elif finding.line in valid_lines:
            valid.append(finding)
        else:
            rejected.append(finding)

    return valid, rejected


def build_effective_summary(
    summary: str,
    rejected_findings: list[ReviewFinding],
) -> str:
    """
    Append rejected findings to the review summary as additional notes.

    When findings reference files or lines outside the diff, they cannot
    be posted as inline comments. This function folds them into the
    summary text so the information is not lost.

    Args:
        summary (str): The original review summary.
        rejected_findings (list[ReviewFinding]): Findings that could not
            be posted inline.

    Returns:
        str: The summary, potentially extended with additional notes.
    """

    if not rejected_findings:
        return summary

    parts: list[str] = [summary, "\n\n**Additional notes** (not in diff):\n"]

    for finding in rejected_findings:
        parts.append(f"- **{finding.file_path}:{finding.line}** — {finding.body}")

    return "\n".join(parts)
