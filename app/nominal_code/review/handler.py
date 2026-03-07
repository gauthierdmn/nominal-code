from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from json_repair import loads as json_repair_loads

from nominal_code.agent.api.tools import SUBMIT_REVIEW_TOOL_NAME
from nominal_code.agent.cli.tracking import run_and_track_conversation
from nominal_code.agent.errors import handle_agent_errors
from nominal_code.agent.prompts import resolve_system_prompt
from nominal_code.agent.runner import run_agent
from nominal_code.config import ApiAgentConfig
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
    from nominal_code.agent.memory import ConversationStore
    from nominal_code.agent.result import AgentResult
    from nominal_code.config import Config
    from nominal_code.platforms.base import ReviewerPlatform
    from nominal_code.workspace.git import GitWorkspace

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
SUMMARY_PATTERN: re.Pattern[str] = re.compile(
    r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"',
)
FALLBACK_MESSAGE: str = (
    "I completed my analysis but failed to produce a structured review. "
    "You can re-trigger the review by mentioning me again. "
    "If the issue persists, contact your administrator."
)
JSON_FIX_SYSTEM_PROMPT: str = (
    "You are a JSON repair tool. You receive malformed JSON and output "
    "ONLY the corrected, valid JSON. Do not add commentary, markdown "
    "fences, or explanations. Preserve all content and structure — fix "
    "only syntax errors."
)
JSON_FIX_PROMPT: str = (
    "The following text is malformed JSON. Common issues include "
    "unescaped double quotes inside string values, trailing commas, "
    "and missing commas. Fix the syntax errors and output ONLY the "
    "corrected JSON.\n\n{broken_json}"
)
JSON_FIX_RETRY_PROMPT: str = (
    "The following JSON has syntax errors. Pay special attention to:\n"
    '- Double quotes inside string values MUST be escaped as \\"\n'
    "- The `suggestion` fields often contain code with double-quoted strings "
    "that need escaping\n"
    "- No trailing commas after the last element in arrays or objects\n\n"
    "The expected structure is:\n"
    '{{"summary": "...", "comments": [{{"path": "...", "line": N, '
    '"body": "...", "suggestion": "optional code"}}]}}\n\n'
    "Fix this JSON and output ONLY valid JSON:\n\n{broken_json}"
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
    bot_username: str = "",
    workspace_path: str = "",
    conversation_store: ConversationStore | None = None,
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
        bot_username (str): Bot username to filter from existing comments.
        workspace_path (str): Pre-existing workspace path (skips cloning). Used
            in CI mode where the repo is already checked out.
        conversation_store (ConversationStore | None): Conversation store for
            conversation continuity.

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
                repo_full_name=event.repo_full_name,
                pr_number=event.pr_number,
            ),
            platform.fetch_pr_comments(
                repo_full_name=event.repo_full_name,
                pr_number=event.pr_number,
            ),
        )
    else:
        reviewer_clone_url: str = platform.build_reviewer_clone_url(
            repo_full_name=event.repo_full_name,
        )
        effective_event: PullRequestEvent = replace(
            event,
            clone_url=reviewer_clone_url,
        )

        workspace: GitWorkspace = create_workspace(
            event=effective_event,
            config=config,
        )

        results: tuple[
            list[ChangedFile], list[ExistingComment], None
        ] = await asyncio.gather(
            platform.fetch_pr_diff(
                repo_full_name=event.repo_full_name,
                pr_number=event.pr_number,
            ),
            platform.fetch_pr_comments(
                repo_full_name=event.repo_full_name,
                pr_number=event.pr_number,
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
        existing
        for existing in all_comments
        if not bot_username or existing.author != bot_username
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

    effective_allowed_tools: list[str] = list(REVIEWER_ALLOWED_TOOLS)

    if isinstance(config.agent, ApiAgentConfig):
        effective_allowed_tools.append(SUBMIT_REVIEW_TOOL_NAME)

    result: AgentResult = await run_and_track_conversation(
        event=event,
        bot_type=BotType.REVIEWER,
        system_prompt=combined_system_prompt,
        prompt=full_prompt,
        cwd=repo_path,
        config=config,
        allowed_tools=effective_allowed_tools,
        conversation_store=conversation_store,
    )

    review_result: AgentReview | None = parse_review_output(result.output)

    if review_result is None:
        logger.warning(
            "Reviewer JSON parse failed for %s#%d, attempting repair",
            event.repo_full_name,
            event.pr_number,
        )

        review_result = await _repair_review_output(
            broken_output=result.output,
            config=config,
            cwd=repo_path,
        )

    if review_result is None:
        logger.warning(
            "Reviewer JSON repair failed for %s#%d, falling back to plain comment",
            event.repo_full_name,
            event.pr_number,
        )

        fallback_comment: str = _build_fallback_comment(result.output)

        return ReviewResult(
            agent_review=None,
            valid_findings=[],
            rejected_findings=[],
            effective_summary="",
            raw_output=fallback_comment,
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
    conversation_store: ConversationStore | None = None,
) -> None:
    """
    Run a review and post the results to the platform.

    Args:
        event (PullRequestEvent): The parsed event that triggered the review.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client with reviewer capabilities.
        conversation_store (ConversationStore | None): Conversation store for
            conversation continuity.
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
            bot_username=bot_username,
            conversation_store=conversation_store,
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

    if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
        raise ValueError("invalid line")

    if not isinstance(body, str) or not body:
        raise ValueError("invalid body")

    side_raw: object = item.get("side", DiffSide.RIGHT.value)

    if not isinstance(side_raw, str) or side_raw not in (DiffSide.LEFT, DiffSide.RIGHT):
        raise ValueError("invalid side")

    side: DiffSide = DiffSide(side_raw)

    suggestion_raw: object = item.get("suggestion")

    if suggestion_raw is not None:
        if not isinstance(suggestion_raw, str) or not suggestion_raw:
            raise ValueError("invalid suggestion")

        if side == DiffSide.LEFT:
            raise ValueError("suggestion not allowed on LEFT side")

    suggestion: str | None = suggestion_raw if isinstance(suggestion_raw, str) else None

    start_line_raw: object = item.get("start_line")

    if start_line_raw is not None:
        if (
            isinstance(start_line_raw, bool)
            or not isinstance(start_line_raw, int)
            or start_line_raw <= 0
        ):
            raise ValueError("invalid start_line")

        if start_line_raw > line:
            raise ValueError("start_line must be <= line")

    start_line: int | None = start_line_raw if isinstance(start_line_raw, int) else None

    return ReviewFinding(
        file_path=path,
        line=line,
        body=body,
        side=side,
        suggestion=suggestion,
        start_line=start_line,
    )


def parse_review_output(output: str) -> AgentReview | None:
    """
    Parse the agent's JSON output into an AgentReview.

    Extracts the JSON object from the output (stripping prose and code
    fences), then uses ``json_repair.loads`` which both validates and
    repairs common JSON syntax errors (unescaped quotes, trailing commas).

    Returns None if the output cannot be parsed into the expected structure.

    Args:
        output (str): Raw text output from the agent.

    Returns:
        AgentReview | None: Parsed result, or None on failure.
    """

    try:
        extracted: str = _extract_json_substring(output.strip())
        data: object = json_repair_loads(extracted)

        if not isinstance(data, dict):
            return None

        summary: object = data.get("summary")

        if not isinstance(summary, str) or not summary:
            return None

        raw_comments: object = data.get("comments", [])

        if not isinstance(raw_comments, list):
            return None

        findings: list[ReviewFinding] = [_parse_finding(item) for item in raw_comments]

    except ValueError:
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


def _extract_json_substring(text: str) -> str:
    """
    Extract the outermost JSON object from text that may contain prose.

    Finds the first ``{`` and last ``}`` and returns that substring.
    Falls back to the original text if no braces are found.

    Args:
        text (str): Raw text potentially containing a JSON object.

    Returns:
        str: The extracted JSON substring, or the original text.
    """

    first_brace: int = text.find("{")
    last_brace: int = text.rfind("}")

    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return text

    return text[first_brace : last_brace + 1]


def _build_fallback_comment(raw_output: str) -> str:
    """
    Build a user-facing comment when the review JSON cannot be parsed.

    Attempts to extract the ``summary`` field from the broken JSON via
    regex. If found, posts the summary with a note that inline comments
    could not be produced. Otherwise, posts a generic retry message.

    Args:
        raw_output (str): The raw agent output that failed parsing.

    Returns:
        str: The fallback comment to post on the PR.
    """

    match: re.Match[str] | None = SUMMARY_PATTERN.search(raw_output)

    if match:
        summary: str = match.group(1).replace('\\"', '"')

        return (
            f"{summary}\n\n"
            "_I was unable to produce inline review comments for this PR. "
            "You can re-trigger the review by mentioning me again. "
            "If the issue persists, contact your administrator._"
        )

    return FALLBACK_MESSAGE


async def _repair_review_output(
    broken_output: str,
    config: Config,
    cwd: Path,
) -> AgentReview | None:
    """
    Attempt to repair malformed review JSON via LLM-based repair.

    Since ``parse_review_output`` already applies extraction and
    ``json_repair.loads``, each LLM attempt gets the full repair
    pipeline for free. Tries two LLM prompts with increasing specificity.

    Args:
        broken_output (str): The raw agent output that failed JSON parsing.
        config (Config): Application configuration (for agent settings).
        cwd (Path): Working directory for the agent.

    Returns:
        AgentReview | None: The parsed review if any strategy succeeds,
            or None if all fail.
    """

    current_json: str = _extract_json_substring(broken_output)

    for attempt, prompt_template in enumerate(
        [JSON_FIX_PROMPT, JSON_FIX_RETRY_PROMPT],
        start=1,
    ):
        prompt: str = prompt_template.format(broken_json=current_json)

        logger.info("Attempting LLM JSON repair (attempt %d/2)", attempt)

        fix_result: AgentResult = await run_agent(
            prompt=prompt,
            cwd=cwd,
            system_prompt=JSON_FIX_SYSTEM_PROMPT,
            allowed_tools=[],
            agent_config=config.agent,
        )

        parsed: AgentReview | None = parse_review_output(fix_result.output)

        if parsed is not None:
            logger.info("LLM JSON repair succeeded on attempt %d", attempt)

            return parsed

        current_json = _extract_json_substring(fix_result.output)

    logger.warning("All JSON repair strategies failed")

    return None


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
