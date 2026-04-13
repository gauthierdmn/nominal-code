from __future__ import annotations

from nominal_code.agent.prompts import (
    TAG_BRANCH_NAME,
    TAG_FILE_PATH,
    TAG_UNTRUSTED_COMMENT,
    TAG_UNTRUSTED_DIFF,
    TAG_UNTRUSTED_REQUEST,
    wrap_tag,
)
from nominal_code.models import ChangedFile
from nominal_code.platforms.base import ExistingComment, PullRequestEvent
from nominal_code.review.diff import annotate_diff


def build_reviewer_prompt(
    event: PullRequestEvent,
    user_prompt: str,
    changed_files: list[ChangedFile],
    existing_comments: list[ExistingComment] | None = None,
    inline_suggestions: bool = True,
    context: str = "",
) -> str:
    """
    Build a prompt for the reviewer agent.

    Diffs are always line-annotated so the agent can reference exact
    line numbers without needing to read files. Exploration notes
    (when available) are inserted before the review instruction.

    Args:
        event (PullRequestEvent): The event with PR context.
        user_prompt (str): The user's extracted prompt text.
        changed_files (list[ChangedFile]): Files changed in the PR.
        existing_comments (list[ExistingComment] | None): Existing PR
            comments to include as context.
        inline_suggestions (bool): Whether to instruct the agent to
            produce one-click-apply code suggestions.
        context (str): Pre-review exploration notes. Inserted verbatim
            when non-empty.

    Returns:
        str: The full prompt to send to the agent.
    """

    branch_info: str = (
        f"Branch: <{TAG_BRANCH_NAME}>{event.pr_branch}</{TAG_BRANCH_NAME}>"
        f" (PR #{event.pr_number} on {event.repo_full_name})"
    )

    if event.base_branch:
        branch_info += f"\nBase branch: {event.base_branch}"

    parts: list[str] = [branch_info]

    if user_prompt:
        parts.append(
            f"Additional instructions:\n{wrap_tag(TAG_UNTRUSTED_REQUEST, user_prompt)}"
        )

    parts.append("## Changed files\n")

    for changed_file in changed_files:
        file_header: str = (
            f"### <{TAG_FILE_PATH}>{changed_file.file_path}</{TAG_FILE_PATH}>"
            f" ({changed_file.status})"
        )

        if changed_file.patch:
            parts.append(
                f"{file_header}\n"
                f"{wrap_tag(TAG_UNTRUSTED_DIFF, annotate_diff(changed_file.patch))}",
            )
        else:
            parts.append(f"{file_header}\n_(no patch available)_")

    if existing_comments:
        parts.append(format_existing_comments(existing_comments))

    if context:
        parts.append(context)

    review_instruction: str = (
        "Review the above changes. Each diff line is annotated with its "
        "actual line number — use these directly. Call the submit_review "
        "tool with your complete review.\n\n"
        "For comments on deleted lines (prefixed with `-` in the diff), "
        'set `"side": "LEFT"`. For additions (`+`) and context lines '
        'omit `side` or use `"RIGHT"`.'
    )

    if inline_suggestions:
        review_instruction += (
            "\n\nFor every issue where you can provide a concrete fix, "
            "you MUST include a `suggestion` field with the exact "
            "replacement code. The annotated diff shows the precise "
            "indentation — match it exactly in your suggestion."
        )

    parts.append(review_instruction)

    return "\n\n".join(parts)


def format_existing_comments(comments: list[ExistingComment]) -> str:
    """
    Format existing comments into a prompt section.

    Args:
        comments (list[ExistingComment]): The comments to format.

    Returns:
        str: Markdown-formatted existing discussions section.
    """

    lines: list[str] = [
        "## Existing discussions\n",
        "The following comments have already been posted on this PR. "
        "Do not raise issues that are already covered below.\n",
    ]

    for existing in comments:
        location: str = ""

        if existing.file_path:
            location = f" on `<{TAG_FILE_PATH}>{existing.file_path}</{TAG_FILE_PATH}>"

            if existing.line:
                location += f":{existing.line}"

            location += "`"

        resolved_tag: str = " (resolved)" if existing.is_resolved else ""
        header: str = f"**@{existing.author}**{location}{resolved_tag}"
        lines.append(f"{header}\n{wrap_tag(TAG_UNTRUSTED_COMMENT, existing.body)}")

    return "\n\n".join(lines)


def build_fallback_review_prompt(
    notes: str,
    original_prompt: str,
) -> str:
    """
    Build a fallback prompt for when the reviewer exhausted its turns.

    Combines the reviewer's accumulated notes with the original prompt
    so a single-turn review call can produce the final output.

    Args:
        notes (str): Content from the reviewer's notes file.
        original_prompt (str): The original reviewer user prompt with
            diffs and context.

    Returns:
        str: The fallback prompt.
    """

    parts: list[str] = [original_prompt]

    if notes.strip():
        parts.append(
            "\n\n## Investigation Notes\n\n"
            "The following notes were gathered during investigation "
            "before the turn limit was reached:\n\n" + notes,
        )

    parts.append(
        "\n\nYou ran out of investigation turns. Based on the diffs "
        "and any notes above, call submit_review with your review now.",
    )

    return "\n".join(parts)
