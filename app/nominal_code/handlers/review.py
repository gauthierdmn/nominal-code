from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nominal_code.agent.api.tools import SUBMIT_REVIEW_TOOL_NAME
from nominal_code.agent.invoke import (
    invoke_agent,
    prepare_conversation,
    save_conversation,
)
from nominal_code.agent.prompts import resolve_guidelines, resolve_system_prompt
from nominal_code.config import ApiAgentConfig
from nominal_code.handlers.diff import build_effective_summary, filter_findings
from nominal_code.handlers.output import (
    build_fallback_comment,
    parse_review_output,
    repair_review_output,
)
from nominal_code.models import (
    BotType,
    ChangedFile,
    ReviewFinding,
)
from nominal_code.platforms.base import (
    CommentReply,
    ExistingComment,
    PullRequestEvent,
)
from nominal_code.workspace.setup import create_workspace

if TYPE_CHECKING:
    from nominal_code.agent.result import AgentResult
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.llm.cost import CostSummary
    from nominal_code.models import AgentReview
    from nominal_code.platforms.base import ReviewerPlatform
    from nominal_code.workspace.git import GitWorkspace


MAX_EXISTING_COMMENTS: int = 50
REVIEWER_ALLOWED_TOOLS: list[str] = [
    "Read",
    "Glob",
    "Grep",
    "Bash(git clone*)",
]

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
        cost (CostSummary | None): Cost information from the agent invocation.
    """

    agent_review: AgentReview | None
    valid_findings: list[ReviewFinding]
    rejected_findings: list[ReviewFinding]
    effective_summary: str
    raw_output: str
    cost: CostSummary | None = None


@dataclass(frozen=True)
class ReviewContext:
    """
    Prepared workspace and PR data needed to run a review.

    Attributes:
        repo_path (Path): Path to the repository checkout.
        deps_path (Path | None): Path to the shared deps directory, or None
            in CI mode.
        changed_files (list[ChangedFile]): Files changed in the PR.
        existing_comments (list[ExistingComment]): Filtered existing PR
            comments (excluding bot comments, capped).
        workspace (GitWorkspace | None): The git workspace, or None in CI
            mode (workspace_path was provided).
    """

    repo_path: Path
    deps_path: Path | None
    changed_files: list[ChangedFile]
    existing_comments: list[ExistingComment]
    workspace: GitWorkspace | None = None


async def _prepare_review_context(
    event: PullRequestEvent,
    config: Config,
    platform: ReviewerPlatform,
    workspace_path: str,
    bot_username: str,
) -> ReviewContext:
    """
    Set up workspace and fetch PR data for a review.

    Handles both CI mode (pre-existing workspace) and clone mode. Fetches
    the diff and existing comments in parallel with workspace setup.

    Args:
        event (PullRequestEvent): The parsed event that triggered the review.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client.
        workspace_path (str): Pre-existing workspace path (skips cloning).
        bot_username (str): Bot username to filter from existing comments.

    Returns:
        ReviewContext: The prepared workspace and PR data.
    """

    if workspace_path:
        repo_path: Path = Path(workspace_path)

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

        existing_comments: list[ExistingComment] = [
            existing
            for existing in all_comments_result
            if not bot_username or existing.author != bot_username
        ][-MAX_EXISTING_COMMENTS:]

        return ReviewContext(
            repo_path=repo_path,
            deps_path=None,
            changed_files=changed_files_result,
            existing_comments=existing_comments,
        )

    workspace: GitWorkspace = create_workspace(
        event=event,
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

    all_comments: list[ExistingComment] = results[1]

    existing_comments = [
        existing
        for existing in all_comments
        if not bot_username or existing.author != bot_username
    ][-MAX_EXISTING_COMMENTS:]

    return ReviewContext(
        repo_path=workspace.repo_path,
        deps_path=workspace.deps_path,
        changed_files=results[0],
        existing_comments=existing_comments,
        workspace=workspace,
    )


async def review(
    event: PullRequestEvent,
    prompt: str,
    config: Config,
    platform: ReviewerPlatform,
    bot_username: str = "",
    workspace_path: str = "",
    conversation_store: ConversationStore | None = None,
    namespace: str = "",
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
        namespace (str): Logical namespace for conversation key isolation.

    Returns:
        ReviewResult: The review result with findings and summary.

    Raises:
        RuntimeError: If workspace setup fails.
    """

    ctx: ReviewContext = await _prepare_review_context(
        event=event,
        config=config,
        platform=platform,
        workspace_path=workspace_path,
        bot_username=bot_username,
    )

    full_prompt: str = _build_reviewer_prompt(
        event=event,
        user_prompt=prompt,
        changed_files=ctx.changed_files,
        deps_path=ctx.deps_path,
        existing_comments=ctx.existing_comments,
    )

    if config.reviewer is None:
        raise RuntimeError("Reviewer config is required but not configured")

    file_paths: list[Path] = [Path(changed.file_path) for changed in ctx.changed_files]

    if ctx.workspace is None:
        effective_guidelines: str = resolve_guidelines(
            repo_path=ctx.repo_path,
            default_guidelines=config.coding_guidelines,
            language_guidelines=config.language_guidelines,
            file_paths=file_paths,
        )
        combined_system_prompt: str = (
            config.reviewer.system_prompt + "\n\n" + effective_guidelines
        )
    else:
        combined_system_prompt = resolve_system_prompt(
            workspace=ctx.workspace,
            config=config,
            bot_system_prompt=config.reviewer.system_prompt,
            file_paths=file_paths,
        )

    effective_allowed_tools: list[str] = list(REVIEWER_ALLOWED_TOOLS)

    if isinstance(config.agent, ApiAgentConfig):
        effective_allowed_tools.append(SUBMIT_REVIEW_TOOL_NAME)

    conversation_id, prior_messages = prepare_conversation(
        event=event,
        bot_type=BotType.REVIEWER,
        agent_config=config.agent,
        conversation_store=conversation_store,
        namespace=namespace,
    )

    result: AgentResult = await invoke_agent(
        prompt=full_prompt,
        cwd=ctx.repo_path,
        system_prompt=combined_system_prompt,
        allowed_tools=effective_allowed_tools,
        agent_config=config.agent,
        conversation_id=conversation_id,
        prior_messages=prior_messages,
    )

    save_conversation(
        event=event,
        bot_type=BotType.REVIEWER,
        result=result,
        agent_config=config.agent,
        conversation_store=conversation_store,
        namespace=namespace,
    )

    review_result: AgentReview | None = parse_review_output(output=result.output)

    if review_result is None:
        logger.warning(
            "Reviewer JSON parse failed for %s#%d, attempting repair",
            event.repo_full_name,
            event.pr_number,
        )

        review_result = await repair_review_output(
            broken_output=result.output,
            config=config,
            cwd=ctx.repo_path,
        )

    if review_result is None:
        logger.warning(
            "Reviewer JSON repair failed for %s#%d, falling back to plain comment",
            event.repo_full_name,
            event.pr_number,
        )

        fallback_comment: str = build_fallback_comment(raw_output=result.output)

        return ReviewResult(
            agent_review=None,
            valid_findings=[],
            rejected_findings=[],
            effective_summary="",
            raw_output=fallback_comment,
        )

    valid_findings, rejected_findings = filter_findings(
        findings=review_result.findings,
        changed_files=ctx.changed_files,
    )

    if rejected_findings:
        logger.warning(
            "Filtered %d findings outside the diff for %s#%d",
            len(rejected_findings),
            event.repo_full_name,
            event.pr_number,
        )

    effective_summary: str = build_effective_summary(
        summary=review_result.summary,
        rejected_findings=rejected_findings,
    )

    cost_str: str = ""

    if result.cost is not None and result.cost.total_cost_usd is not None:
        cost_usd: float = result.cost.total_cost_usd
        tokens_in: int = result.cost.total_input_tokens
        tokens_out: int = result.cost.total_output_tokens
        cost_str = (
            f", cost=${cost_usd:.4f}, tokens_in={tokens_in}, tokens_out={tokens_out}"
        )

    logger.info(
        "Reviewer finished for %s#%d (findings=%d, turns=%d, duration=%dms%s)",
        event.repo_full_name,
        event.pr_number,
        len(review_result.findings),
        result.num_turns,
        result.duration_ms,
        cost_str,
    )

    return ReviewResult(
        agent_review=review_result,
        valid_findings=valid_findings,
        rejected_findings=rejected_findings,
        effective_summary=effective_summary,
        raw_output=result.output,
        cost=result.cost,
    )


async def post_review_result(
    event: PullRequestEvent,
    result: ReviewResult,
    platform: ReviewerPlatform,
) -> None:
    """
    Post a ReviewResult to the platform.

    Handles three cases: raw output fallback (when JSON parsing failed),
    findings as a native review, or a summary-only reply.

    Args:
        event (PullRequestEvent): The event to post results against.
        result (ReviewResult): The review result to post.
        platform (ReviewerPlatform): The platform client.
    """

    if result.agent_review is None:
        await platform.post_reply(
            event=event,
            reply=CommentReply(body=result.raw_output),
        )

        return

    if result.valid_findings:
        await platform.submit_review(
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            findings=result.valid_findings,
            summary=result.effective_summary,
            event=event,
        )
    else:
        await platform.post_reply(
            event=event,
            reply=CommentReply(body=result.effective_summary),
        )


async def run_and_post_review(
    event: PullRequestEvent,
    prompt: str,
    config: Config,
    platform: ReviewerPlatform,
    workspace_path: str = "",
    conversation_store: ConversationStore | None = None,
    namespace: str = "",
) -> ReviewResult:
    """
    Run a review and post the results to the platform.

    Combines ``review()`` and ``post_review_result()`` into a single call.
    Callers are responsible for their own error handling (e.g. wrapping
    with ``handle_agent_errors`` or try/except).

    Args:
        event (PullRequestEvent): The parsed event that triggered the review.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (ReviewerPlatform): The platform client with reviewer capabilities.
        workspace_path (str): Pre-existing workspace path (skips cloning).
        conversation_store (ConversationStore | None): Conversation store for
            conversation continuity.
        namespace (str): Logical namespace for conversation key isolation.

    Returns:
        ReviewResult: The review result with findings and summary.
    """

    if config.reviewer is None:
        raise RuntimeError("Reviewer config is required but not configured")

    bot_username: str = config.reviewer.bot_username

    review_result: ReviewResult = await review(
        event=event,
        prompt=prompt,
        config=config,
        platform=platform,
        bot_username=bot_username,
        workspace_path=workspace_path,
        conversation_store=conversation_store,
        namespace=namespace,
    )

    await post_review_result(
        event=event,
        result=review_result,
        platform=platform,
    )

    return review_result


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
