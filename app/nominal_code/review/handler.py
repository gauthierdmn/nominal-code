from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from nominal_code.agent.cli.tracking import run_and_track_session
from nominal_code.agent.errors import handle_agent_errors
from nominal_code.agent.prompts import resolve_system_prompt
from nominal_code.models import (
    AgentReview,
    BotType,
    ChangedFile,
    DiffSide,
    ReviewFinding,
)
from nominal_code.platforms.base import (
    CommentReply,
    ExistingComment,
    PullRequestEvent,
)
from nominal_code.workspace.setup import create_workspace, resolve_branch

if TYPE_CHECKING:
    from nominal_code.agent.cli.session import SessionStore
    from nominal_code.agent.result import AgentResult
    from nominal_code.config import Config
    from nominal_code.platforms.base import ReviewerPlatform
    from nominal_code.workspace.git import GitWorkspace

MAX_REVIEW_RETRIES: int = 2
MAX_EXISTING_COMMENTS: int = 50
REVIEWER_ALLOWED_TOOLS: list[str] = [
    "Read",
    "Glob",
    "Grep",
    "Bash(git clone*)",
]
HUNK_HEADER_PATTERN: re.Pattern[str] = re.compile(
    r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@",
)

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewResult:
    """
    Result of executing a code review without side effects.

    Attributes:
        agent_review (AgentReview | None): Parsed review, or None if parsing
            failed after retries.
        valid_findings (list[ReviewFinding]): Findings on lines within the diff.
        rejected_findings (list[ReviewFinding]): Findings on lines outside the diff.
        effective_summary (str): Summary with rejected findings appended.
        raw_output (str): The raw agent output text.
    """

    agent_review: AgentReview | None
    valid_findings: list[ReviewFinding]
    rejected_findings: list[ReviewFinding]
    effective_summary: str
    raw_output: str


async def review(
    event: PullRequestEvent,
    prompt: str,
    config: Config,
    platform: ReviewerPlatform,
    session_store: SessionStore | None = None,
    bot_username: str = "",
    workspace_path: str = "",
) -> ReviewResult:
    """
    Run the core review logic without posting results to the platform.

    Resolves the branch, clones the repo, fetches the diff and existing
    comments, runs the agent, parses the output, and filters findings.

    Args:
        event (PullRequestEvent): The parsed event that triggered the review.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client with reviewer capabilities.
        session_store (SessionStore | None): Agent session store (optional for CLI).
        bot_username (str): Bot username to filter from existing comments.
        workspace_path (str): Pre-existing workspace path (skips cloning). Used
            in CI mode where the repo is already checked out.

    Returns:
        ReviewResult: The review result with findings and summary.

    Raises:
        RuntimeError: If workspace setup fails.
    """

    if workspace_path:
        repo_path: Path = Path(workspace_path)
        deps_path: Path | None = None

        changed_files_result: list[ChangedFile]
        all_comments_result: list[ExistingComment]

        changed_files_result, all_comments_result = await asyncio.gather(
            platform.fetch_pr_diff(
                event.repo_full_name,
                event.pr_number,
            ),
            platform.fetch_pr_comments(
                event.repo_full_name,
                event.pr_number,
            ),
        )
    else:
        reviewer_clone_url: str = platform.build_reviewer_clone_url(
            event.repo_full_name,
        )
        effective_event: PullRequestEvent = replace(
            event,
            clone_url=reviewer_clone_url,
        )

        workspace: GitWorkspace = create_workspace(effective_event, config)

        results: tuple[
            list[ChangedFile], list[ExistingComment], None
        ] = await asyncio.gather(
            platform.fetch_pr_diff(
                event.repo_full_name,
                event.pr_number,
            ),
            platform.fetch_pr_comments(
                event.repo_full_name,
                event.pr_number,
            ),
            workspace.ensure_ready(),
        )
        workspace.maybe_create_deps_dir()

        changed_files_result = results[0]
        all_comments_result = results[1]
        repo_path = workspace.repo_path
        deps_path = workspace.deps_path

    changed_files: list[ChangedFile] = changed_files_result
    all_comments: list[ExistingComment] = all_comments_result

    existing_comments: list[ExistingComment] = [
        existing for existing in all_comments if existing.author != bot_username
    ][-MAX_EXISTING_COMMENTS:]

    full_prompt: str = _build_reviewer_prompt(
        event=event,
        user_prompt=prompt,
        changed_files=changed_files,
        deps_path=deps_path,
        existing_comments=existing_comments,
    )

    if config.reviewer is None:
        raise RuntimeError("Reviewer config is required but not configured")

    file_paths: list[Path] = [Path(changed.file_path) for changed in changed_files]

    if workspace_path:
        from nominal_code.agent.prompts import resolve_guidelines

        effective_guidelines: str = resolve_guidelines(
            repo_path=repo_path,
            default_guidelines=config.coding_guidelines,
            language_guidelines=config.language_guidelines,
            file_paths=file_paths,
        )
        combined_system_prompt: str = (
            config.reviewer.system_prompt + "\n\n" + effective_guidelines
        )
    else:
        combined_system_prompt = resolve_system_prompt(
            workspace=workspace,
            config=config,
            bot_system_prompt=config.reviewer.system_prompt,
            file_paths=file_paths,
        )

    result: AgentResult = await run_and_track_session(
        event=event,
        bot_type=BotType.REVIEWER,
        session_store=session_store,
        system_prompt=combined_system_prompt,
        prompt=full_prompt,
        cwd=repo_path,
        config=config,
        allowed_tools=REVIEWER_ALLOWED_TOOLS,
    )

    review_result: AgentReview | None = parse_review_output(result.output)

    retry_count: int = 0

    while review_result is None and retry_count < MAX_REVIEW_RETRIES:
        retry_count += 1
        retry_prompt: str = _build_retry_prompt(result.output)

        logger.warning(
            "Reviewer JSON parse failed for %s#%d, retry %d/%d",
            event.repo_full_name,
            event.pr_number,
            retry_count,
            MAX_REVIEW_RETRIES,
        )

        result = await run_and_track_session(
            event=event,
            bot_type=BotType.REVIEWER,
            session_store=session_store,
            system_prompt=combined_system_prompt,
            prompt=retry_prompt,
            cwd=repo_path,
            config=config,
            allowed_tools=REVIEWER_ALLOWED_TOOLS,
            session_id_override=result.session_id,
        )

        review_result = parse_review_output(result.output)

    if review_result is None:
        logger.warning(
            "Reviewer JSON still invalid after %d retries for %s#%d, "
            "falling back to plain comment",
            MAX_REVIEW_RETRIES,
            event.repo_full_name,
            event.pr_number,
        )

        return ReviewResult(
            agent_review=None,
            valid_findings=[],
            rejected_findings=[],
            effective_summary="",
            raw_output=result.output,
        )

    valid_findings, rejected_findings = _filter_findings(
        findings=review_result.findings,
        changed_files=changed_files,
    )

    if rejected_findings:
        logger.warning(
            "Filtered %d findings outside the diff for %s#%d",
            len(rejected_findings),
            event.repo_full_name,
            event.pr_number,
        )

    effective_summary: str = _build_effective_summary(
        summary=review_result.summary,
        rejected_findings=rejected_findings,
    )

    logger.info(
        "Reviewer finished for %s#%d (findings=%d, turns=%d, duration=%dms)",
        event.repo_full_name,
        event.pr_number,
        len(review_result.findings),
        result.num_turns,
        result.duration_ms,
    )

    return ReviewResult(
        agent_review=review_result,
        valid_findings=valid_findings,
        rejected_findings=rejected_findings,
        effective_summary=effective_summary,
        raw_output=result.output,
    )


async def review_and_post(
    event: PullRequestEvent,
    prompt: str,
    config: Config,
    platform: ReviewerPlatform,
    session_store: SessionStore,
) -> None:
    """
    Run a review and post the results to the platform.

    Args:
        event (PullRequestEvent): The parsed event that triggered the review.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client with reviewer capabilities.
        session_store (SessionStore): Agent session store.
    """

    effective_event: PullRequestEvent | None = await resolve_branch(
        event=event,
        platform=platform,
    )

    if effective_event is None:
        return

    if config.reviewer is None:
        raise RuntimeError("Reviewer config is required but not configured")

    bot_username: str = config.reviewer.bot_username

    async with handle_agent_errors(event, platform, "reviewer"):
        review_result: ReviewResult = await review(
            event=effective_event,
            prompt=prompt,
            config=config,
            platform=platform,
            session_store=session_store,
            bot_username=bot_username,
        )

        if review_result.agent_review is None:
            await platform.post_reply(
                event=event,
                reply=CommentReply(body=review_result.raw_output),
            )

            return

        if review_result.valid_findings:
            await platform.submit_review(
                repo_full_name=event.repo_full_name,
                pr_number=event.pr_number,
                findings=review_result.valid_findings,
                summary=review_result.effective_summary,
                event=event,
            )
        else:
            await platform.post_reply(
                event=event,
                reply=CommentReply(body=review_result.effective_summary),
            )


def _build_reviewer_prompt(
    event: PullRequestEvent,
    user_prompt: str,
    changed_files: list[ChangedFile],
    deps_path: Path | None = None,
    existing_comments: list[ExistingComment] | None = None,
) -> str:
    """
    Build a prompt for the reviewer bot including the full PR diff.

    Args:
        event (PullRequestEvent): The event with PR context.
        user_prompt (str): The user's extracted prompt text.
        changed_files (list[ChangedFile]): Files changed in the PR.
        deps_path (Path | None): Path to the shared dependencies directory.
        existing_comments (list[ExistingComment] | None): Existing PR
            comments to include as context.

    Returns:
        str: The full prompt to send to the agent.
    """

    parts: list[str] = [
        f"Branch: {event.pr_branch} (PR #{event.pr_number} on {event.repo_full_name})",
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
        "following the format described in your system prompt. "
        "For comments on deleted lines (lines starting with `-` in the diff), "
        'set `"side": "LEFT"`. For additions (`+`) and context lines omit '
        '`side` or use `"RIGHT"`.',
    )

    if deps_path is not None:
        parts.append(
            f"Dependencies directory: {deps_path}\n"
            "If you need to understand a private dependency that is not available on\n"
            "PyPI, you can `git clone` it into this directory. Clone with `--depth=1`\n"
            "to minimize download time. Dependencies cloned here are shared across\n"
            "PRs for this repository.",
        )

    return "\n\n".join(parts)


def _strip_fences(text: str) -> str:
    """
    Remove markdown code fences from a string if present.

    Args:
        text (str): Text that may be wrapped in a code fence.

    Returns:
        str: The text with the opening fence line and closing fence removed.
    """

    # LLMs often wrap JSON in markdown code fences even when instructed not to.
    if not text.startswith("```"):
        return text

    lines: list[str] = text.split("\n")[1:]

    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def _parse_finding(item: object) -> ReviewFinding:
    """
    Parse a single comment dict into a ReviewFinding.

    Args:
        item (object): A raw comment entry from the agent's JSON output.

    Returns:
        ReviewFinding: The parsed finding.

    Raises:
        ValueError: If the item is missing required fields or has invalid types.
    """

    if not isinstance(item, dict):
        raise ValueError("comment is not a dict")

    path: object = item.get("path")
    line: object = item.get("line")
    body: object = item.get("body")

    if not isinstance(path, str) or not path:
        raise ValueError("invalid path")

    if not isinstance(line, int) or line <= 0:
        raise ValueError("invalid line")

    if not isinstance(body, str) or not body:
        raise ValueError("invalid body")

    side_raw: object = item.get("side", DiffSide.RIGHT.value)

    if not isinstance(side_raw, str) or side_raw not in (DiffSide.LEFT, DiffSide.RIGHT):
        raise ValueError("invalid side")

    side: DiffSide = DiffSide(side_raw)

    return ReviewFinding(file_path=path, line=line, body=body, side=side)


def parse_review_output(output: str) -> AgentReview | None:
    """
    Parse the agent's JSON output into an AgentReview.

    Returns None if the output is not valid JSON or does not match
    the expected structure.

    Args:
        output (str): Raw text output from the agent.

    Returns:
        AgentReview | None: Parsed result, or None on failure.
    """

    try:
        data: object = json.loads(_strip_fences(output.strip()))

        if not isinstance(data, dict):
            return None

        summary: object = data.get("summary")

        if not isinstance(summary, str) or not summary:
            return None

        raw_comments: object = data.get("comments", [])

        if not isinstance(raw_comments, list):
            return None

        findings: list[ReviewFinding] = [_parse_finding(item) for item in raw_comments]

    except (json.JSONDecodeError, ValueError):
        return None

    return AgentReview(summary=summary, findings=findings)


def _filter_findings(
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

    diff_index: dict[str, dict[DiffSide, set[int]]] = _build_diff_index(changed_files)
    valid: list[ReviewFinding] = []
    rejected: list[ReviewFinding] = []

    for finding in findings:
        file_sides: dict[DiffSide, set[int]] | None = diff_index.get(finding.file_path)

        if file_sides is None:
            rejected.append(finding)
            continue

        valid_lines: set[int] = file_sides.get(finding.side, set())

        if finding.line in valid_lines:
            valid.append(finding)
        else:
            rejected.append(finding)

    return valid, rejected


def _build_effective_summary(
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


def _build_retry_prompt(previous_output: str) -> str:
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


def _parse_diff_lines(patch: str) -> dict[DiffSide, set[int]]:
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


def _build_diff_index(
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
            index[changed_file.file_path] = _parse_diff_lines(changed_file.patch)

    return index


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
