from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.agent_runner import AgentResult, run_agent
from nominal_code.bot_type import (
    BotType,
    ChangedFile,
    ReviewFinding,
    ReviewResult,
)
from nominal_code.git_workspace import GitWorkspace
from nominal_code.handlers.shared import (
    build_system_prompt,
    resolve_branch,
    resolve_guidelines,
)
from nominal_code.platforms.base import (
    CommentReply,
    ExistingComment,
    ReviewComment,
)

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import ReviewerPlatform
    from nominal_code.session import SessionStore

MAX_REVIEW_RETRIES: int = 2
MAX_EXISTING_COMMENTS: int = 50

REVIEWER_ALLOWED_TOOLS: list[str] = [
    "Read",
    "Glob",
    "Grep",
    "Bash(git clone*)",
]

logger: logging.Logger = logging.getLogger(__name__)


async def process_comment(
    comment: ReviewComment,
    prompt: str,
    config: Config,
    platform: ReviewerPlatform,
    session_store: SessionStore,
) -> None:
    """
    Process a comment using the reviewer bot: fetch diff, run agent, submit review.

    Args:
        comment (ReviewComment): The parsed review comment.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client with reviewer capabilities.
        session_store (SessionStore): Agent session store.
    """

    effective_comment: ReviewComment | None = await resolve_branch(comment, platform)

    if effective_comment is None:
        return

    assert config.reviewer is not None
    bot_username: str = config.reviewer.bot_username

    reviewer_clone_url: str = platform.build_reviewer_clone_url(
        comment.repo_full_name,
    )
    effective_comment = replace(effective_comment, clone_url=reviewer_clone_url)

    workspace: GitWorkspace = GitWorkspace(
        base_dir=config.workspace_base_dir,
        repo_full_name=effective_comment.repo_full_name,
        pr_number=effective_comment.pr_number,
        clone_url=effective_comment.clone_url,
        branch=effective_comment.pr_branch,
    )

    try:
        results: tuple[
            list[ChangedFile], list[ExistingComment], None
        ] = await asyncio.gather(
            platform.fetch_pr_diff(
                comment.repo_full_name,
                comment.pr_number,
            ),
            platform.fetch_pr_comments(
                comment.repo_full_name,
                comment.pr_number,
            ),
            workspace.ensure_ready(),
        )
        workspace.ensure_deps_dir()
    except RuntimeError:
        logger.exception("Failed to set up workspace")

        await platform.post_reply(
            comment,
            CommentReply(body="Failed to set up the git workspace."),
        )

        return

    changed_files: list[ChangedFile] = results[0]
    all_comments: list[ExistingComment] = results[1]

    existing_comments: list[ExistingComment] = [
        existing for existing in all_comments if existing.author != bot_username
    ][-MAX_EXISTING_COMMENTS:]

    full_prompt: str = build_reviewer_prompt(
        effective_comment,
        prompt,
        changed_files,
        deps_path=workspace.deps_path,
        existing_comments=existing_comments,
    )
    existing_session: str | None = session_store.get(
        comment.platform,
        comment.repo_full_name,
        comment.pr_number,
        BotType.REVIEWER.value,
    )

    try:
        file_paths: list[str] = [changed.file_path for changed in changed_files]

        effective_guidelines: str = resolve_guidelines(
            workspace.repo_path,
            config.coding_guidelines,
            config.language_guidelines,
            file_paths,
        )

        combined_system_prompt: str = build_system_prompt(
            config.reviewer.system_prompt,
            effective_guidelines,
        )

        result: AgentResult = await run_agent(
            prompt=full_prompt,
            cwd=workspace.repo_path,
            model=config.agent_model,
            max_turns=config.agent_max_turns,
            cli_path=config.agent_cli_path,
            session_id=existing_session or "",
            system_prompt=combined_system_prompt,
            permission_mode="bypassPermissions",
            allowed_tools=REVIEWER_ALLOWED_TOOLS,
        )

        if result.session_id:
            session_store.set(
                comment.platform,
                comment.repo_full_name,
                comment.pr_number,
                BotType.REVIEWER.value,
                result.session_id,
            )

        review_result: ReviewResult | None = parse_review_output(result.output)

        retry_count: int = 0

        while review_result is None and retry_count < MAX_REVIEW_RETRIES:
            retry_count += 1
            retry_prompt: str = build_retry_prompt(result.output)

            logger.warning(
                "Reviewer JSON parse failed for %s#%d, retry %d/%d",
                comment.repo_full_name,
                comment.pr_number,
                retry_count,
                MAX_REVIEW_RETRIES,
            )

            result = await run_agent(
                prompt=retry_prompt,
                cwd=workspace.repo_path,
                model=config.agent_model,
                max_turns=config.agent_max_turns,
                cli_path=config.agent_cli_path,
                session_id=result.session_id,
                system_prompt=combined_system_prompt,
                permission_mode="bypassPermissions",
                allowed_tools=REVIEWER_ALLOWED_TOOLS,
            )

            if result.session_id:
                session_store.set(
                    comment.platform,
                    comment.repo_full_name,
                    comment.pr_number,
                    BotType.REVIEWER.value,
                    result.session_id,
                )

            review_result = parse_review_output(result.output)

        if review_result is None:
            logger.warning(
                "Reviewer JSON still invalid after %d retries for %s#%d, "
                "falling back to plain comment",
                MAX_REVIEW_RETRIES,
                comment.repo_full_name,
                comment.pr_number,
            )

            await platform.post_reply(
                comment,
                CommentReply(body=result.output),
            )

            return

        valid_findings, rejected_findings = filter_findings(
            review_result.findings,
            changed_files,
        )

        if rejected_findings:
            logger.warning(
                "Filtered %d findings outside the diff for %s#%d",
                len(rejected_findings),
                comment.repo_full_name,
                comment.pr_number,
            )

        effective_summary: str = build_effective_summary(
            review_result.summary,
            rejected_findings,
        )

        if valid_findings:
            await platform.submit_review(
                repo_full_name=comment.repo_full_name,
                pr_number=comment.pr_number,
                findings=valid_findings,
                summary=effective_summary,
                comment=comment,
            )
        else:
            await platform.post_reply(
                comment,
                CommentReply(body=effective_summary),
            )

        logger.info(
            "Reviewer finished for %s#%d (findings=%d, turns=%d, duration=%dms)",
            comment.repo_full_name,
            comment.pr_number,
            len(review_result.findings),
            result.num_turns,
            result.duration_ms,
        )
    except Exception:
        logger.exception("Error running agent (reviewer)")

        await platform.post_reply(
            comment,
            CommentReply(body="An unexpected error occurred while running the agent."),
        )


def build_reviewer_prompt(
    comment: ReviewComment,
    user_prompt: str,
    changed_files: list[ChangedFile],
    deps_path: str = "",
    existing_comments: list[ExistingComment] | None = None,
) -> str:
    """
    Build a prompt for the reviewer bot including the full PR diff.

    Args:
        comment (ReviewComment): The review comment with context.
        user_prompt (str): The user's extracted prompt text.
        changed_files (list[ChangedFile]): Files changed in the PR.
        deps_path (str): Path to the shared dependencies directory.
        existing_comments (list[ExistingComment] | None): Existing PR
            comments to include as context.

    Returns:
        str: The full prompt to send to the agent.
    """

    parts: list[str] = [
        f"Branch: {comment.pr_branch} "
        f"(PR #{comment.pr_number} on {comment.repo_full_name})",
    ]

    if user_prompt:
        parts.append(f"Additional instructions: {user_prompt}")

    parts.append("## Changed files\n")

    for changed_file in changed_files:
        file_header: str = f"### {changed_file.file_path} ({changed_file.status})"

        if changed_file.patch:
            parts.append(
                f"{file_header}\n```diff\n{changed_file.patch}\n```",
            )
        else:
            parts.append(f"{file_header}\n_(no patch available)_")

    if existing_comments:
        parts.append(_format_existing_comments(existing_comments))

    parts.append(
        "Review the above changes and output your review as JSON "
        "following the format described in your system prompt.",
    )

    if deps_path:
        parts.append(
            f"Dependencies directory: {deps_path}\n"
            "If you need to understand a private dependency that is not available on\n"
            "PyPI, you can `git clone` it into this directory. Clone with `--depth=1`\n"
            "to minimize download time. Dependencies cloned here are shared across\n"
            "PRs for this repository.",
        )

    return "\n\n".join(parts)


def _format_existing_comments(comments: list[ExistingComment]) -> str:
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
            location = f" on `{existing.file_path}"

            if existing.line:
                location += f":{existing.line}"

            location += "`"

        resolved_tag: str = " (resolved)" if existing.is_resolved else ""
        header: str = f"**@{existing.author}**{location}{resolved_tag}"
        quoted_body: str = "\n".join(
            f"> {body_line}" for body_line in existing.body.splitlines()
        )
        lines.append(f"{header}\n{quoted_body}")

    return "\n\n".join(lines)


def parse_review_output(output: str) -> ReviewResult | None:
    """
    Parse the agent's JSON output into a ReviewResult.

    Returns None if the output is not valid JSON or does not match
    the expected structure.

    Args:
        output (str): Raw text output from the agent.

    Returns:
        ReviewResult | None: Parsed result, or None on failure.
    """

    stripped: str = output.strip()

    if stripped.startswith("```"):
        lines: list[str] = stripped.split("\n")
        lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        stripped = "\n".join(lines).strip()

    try:
        data: dict[str, object] = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    summary: object = data.get("summary")

    if not isinstance(summary, str) or not summary:
        return None

    raw_comments: object = data.get("comments", [])

    if not isinstance(raw_comments, list):
        return None

    findings: list[ReviewFinding] = []

    for item in raw_comments:
        if not isinstance(item, dict):
            return None

        path: object = item.get("path")
        line: object = item.get("line")
        body: object = item.get("body")

        if not isinstance(path, str) or not path:
            return None

        if not isinstance(line, int) or line <= 0:
            return None

        if not isinstance(body, str) or not body:
            return None

        findings.append(ReviewFinding(file_path=path, line=line, body=body))

    return ReviewResult(summary=summary, findings=findings)


HUNK_HEADER_PATTERN: re.Pattern[str] = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@",
)


def _parse_diff_lines(patch: str) -> set[int]:
    """
    Extract the set of new-side line numbers present in a unified diff.

    Parses hunk headers and walks the diff lines to collect every line
    number on the RIGHT side (additions and context lines).

    Args:
        patch (str): Unified diff text for a single file.

    Returns:
        set[int]: Line numbers that appear on the new side of the diff.
    """

    lines: set[int] = set()
    current_line: int = 0

    for raw_line in patch.splitlines():
        header_match: re.Match[str] | None = HUNK_HEADER_PATTERN.match(raw_line)

        if header_match:
            current_line = int(header_match.group(1))
            continue

        if current_line == 0:
            continue

        if raw_line.startswith("-"):
            continue

        lines.add(current_line)
        current_line += 1

    return lines


def _build_diff_index(
    changed_files: list[ChangedFile],
) -> dict[str, set[int]]:
    """
    Build a mapping from file path to the set of valid diff line numbers.

    Args:
        changed_files (list[ChangedFile]): Files changed in the PR.

    Returns:
        dict[str, set[int]]: File paths mapped to their valid new-side lines.
    """

    index: dict[str, set[int]] = {}

    for changed_file in changed_files:
        if changed_file.patch:
            index[changed_file.file_path] = _parse_diff_lines(changed_file.patch)

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

    diff_index: dict[str, set[int]] = _build_diff_index(changed_files)
    valid: list[ReviewFinding] = []
    rejected: list[ReviewFinding] = []

    for finding in findings:
        valid_lines: set[int] | None = diff_index.get(finding.file_path)

        if valid_lines is not None and finding.line in valid_lines:
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


def build_retry_prompt(previous_output: str) -> str:
    """
    Build a prompt asking the agent to fix its malformed JSON output.

    Args:
        previous_output (str): The previous invalid output from the agent.

    Returns:
        str: The retry prompt.
    """

    return (
        "Your previous output was not valid JSON. "
        "Please output ONLY valid JSON matching the required format:\n"
        '{"summary": "...", "comments": '
        '[{"path": "...", "line": N, "body": "..."}]}\n\n'
        f"Your previous output was:\n{previous_output}"
    )
