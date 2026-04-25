from __future__ import annotations

from nominal_code.agent.prompts import (
    TAG_BRANCH_NAME,
    TAG_FILE_PATH,
    TAG_UNTRUSTED_COMMENT,
    TAG_UNTRUSTED_COMMIT_MESSAGES,
    TAG_UNTRUSTED_DESCRIPTION,
    TAG_UNTRUSTED_DIFF,
    TAG_UNTRUSTED_REQUEST,
    wrap_tag,
)
from nominal_code.models import ChangedFile
from nominal_code.platforms.base import (
    ExistingComment,
    PullRequestEvent,
    PullRequestMetadata,
)
from nominal_code.review.diff import annotate_diff

MAX_DESCRIPTION_CHARS: int = 5_000
MAX_COMMIT_MESSAGES: int = 20


def build_codebase_reviewer_prompt(
    event: PullRequestEvent,
    user_prompt: str,
    context: str = "",
) -> str:
    """
    Build a prompt for a whole-repository review (no diff context).

    Used when ``scope`` is ``ReviewScope.CODEBASE``. Produces a header
    that identifies the repo and branch without referencing a PR number
    or diff. The LLM is expected to explore the workspace via its tools.

    Args:
        event (PullRequestEvent): Event carrying the repo name and branch.
        user_prompt (str): Optional caller-supplied instructions.
        context (str): Pre-review exploration notes to insert before
            the review instruction.

    Returns:
        str: The full prompt to send to the codebase reviewer.
    """

    parts: list[str] = [
        f"## Codebase review: {event.repo_full_name}\n\n"
        f"**Branch**: <{TAG_BRANCH_NAME}>{event.pr_branch}</{TAG_BRANCH_NAME}>"
    ]

    if user_prompt:
        parts.append(
            f"Additional instructions:\n{wrap_tag(TAG_UNTRUSTED_REQUEST, user_prompt)}"
        )

    if context:
        parts.append(context)

    return "\n\n".join(parts)


def build_reviewer_prompt(
    event: PullRequestEvent,
    user_prompt: str,
    changed_files: list[ChangedFile],
    existing_comments: list[ExistingComment] | None = None,
    inline_suggestions: bool = True,
    context: str = "",
    metadata: PullRequestMetadata | None = None,
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
        metadata (PullRequestMetadata | None): PR metadata with title,
            description, and commit messages. When provided, these are
            included at the top of the prompt.

    Returns:
        str: The full prompt to send to the agent.
    """

    parts: list[str] = [_format_pr_header(event, metadata)]

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
        "actual line number — use these directly.\n\n"
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


def _format_pr_header(
    event: PullRequestEvent,
    metadata: PullRequestMetadata | None,
) -> str:
    """
    Format the PR header section with branch info and metadata.

    Includes title, description, and commit messages when metadata
    is available. Description and commits are wrapped in untrusted
    tags for prompt injection defense.

    Args:
        event (PullRequestEvent): The event with branch context.
        metadata (PullRequestMetadata | None): PR metadata from the
            platform API.

    Returns:
        str: The formatted PR header section.
    """

    title: str = ""
    description: str = ""
    commit_messages: tuple[str, ...] = ()

    if metadata is not None:
        title = metadata.title
        description = metadata.description
        commit_messages = metadata.commit_messages

    if not title:
        title = event.pr_title

    header_parts: list[str] = [
        f"## Pull request #{event.pr_number} on {event.repo_full_name}\n",
    ]

    if title:
        header_parts.append(f"**Title**: {title}")

    branch_line: str = (
        f"**Branch**: <{TAG_BRANCH_NAME}>{event.pr_branch}</{TAG_BRANCH_NAME}>"
    )

    if event.base_branch:
        branch_line += f" -> {event.base_branch}"

    header_parts.append(branch_line)

    if description:
        truncated: str = description[:MAX_DESCRIPTION_CHARS]
        header_parts.append(
            f"**Description**:\n{wrap_tag(TAG_UNTRUSTED_DESCRIPTION, truncated)}",
        )

    if commit_messages:
        capped: tuple[str, ...] = commit_messages[:MAX_COMMIT_MESSAGES]
        bullet_list: str = "\n".join(f"- {msg}" for msg in capped)
        header_parts.append(
            f"**Commits**:\n{wrap_tag(TAG_UNTRUSTED_COMMIT_MESSAGES, bullet_list)}",
        )

    return "\n".join(header_parts)


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
