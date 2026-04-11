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
from nominal_code.agent.prompts import (
    TAG_BRANCH_NAME,
    TAG_FILE_PATH,
    TAG_REPO_GUIDELINES,
    TAG_UNTRUSTED_COMMENT,
    TAG_UNTRUSTED_DIFF,
    TAG_UNTRUSTED_REQUEST,
    resolve_guidelines,
    resolve_system_prompt,
    wrap_tag,
)
from nominal_code.agent.sandbox import sanitize_output
from nominal_code.config import ApiAgentConfig
from nominal_code.llm.messages import ToolChoice
from nominal_code.models import (
    ChangedFile,
    ReviewFinding,
)
from nominal_code.platforms.base import (
    CommentReply,
    ExistingComment,
    PullRequestEvent,
)
from nominal_code.review.diff import (
    annotate_diff,
    build_effective_summary,
    filter_findings,
)
from nominal_code.review.output import (
    build_fallback_comment,
    parse_review_output,
    repair_review_output,
)
from nominal_code.workspace.setup import create_workspace

if TYPE_CHECKING:
    from nominal_code.agent.result import AgentResult
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.llm.cost import CostSummary
    from nominal_code.llm.messages import Message
    from nominal_code.models import AgentReview
    from nominal_code.platforms.base import Platform
    from nominal_code.workspace.git import GitWorkspace


MAX_EXISTING_COMMENTS: int = 50

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
        num_turns (int): Number of agentic turns taken by the LLM.
        messages (tuple[Message, ...]): Full LLM conversation transcript.
        input_prompt (str): Full prompt sent to the LLM.
    """

    agent_review: AgentReview | None
    valid_findings: list[ReviewFinding]
    rejected_findings: list[ReviewFinding]
    effective_summary: str
    raw_output: str
    cost: CostSummary | None = None
    num_turns: int = 0
    messages: tuple[Message, ...] = ()
    input_prompt: str = ""


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
    platform: Platform,
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
        platform (Platform): The platform client.
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
    platform: Platform,
    bot_username: str = "",
    workspace_path: str = "",
    conversation_store: ConversationStore | None = None,
    namespace: str = "",
    context: str = "",
) -> ReviewResult:
    """
    Run the core review logic without posting results to the platform.

    Resolves the branch, clones the repo, fetches the diff and existing
    comments, runs the agent, parses the output, and filters findings.

    Args:
        event (PullRequestEvent): The parsed event that triggered the review.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (Platform): The platform client with reviewer capabilities.
        bot_username (str): Bot username to filter from existing comments.
        workspace_path (str): Pre-existing workspace path (skips cloning). Used
            in CI mode where the repo is already checked out.
        conversation_store (ConversationStore | None): Conversation store for
            conversation continuity.
        namespace (str): Logical namespace for conversation key isolation.
        context (str): Pre-review context to include in the user message.
            Inserted verbatim before the review instruction. Typically
            the output from codebase exploration sub-agents.

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

    if config.reviewer is None:
        raise ValueError("ReviewerConfig is required but not configured")

    reviewer_config = config.reviewer

    full_prompt: str = _build_reviewer_prompt(
        event=event,
        user_prompt=prompt,
        changed_files=ctx.changed_files,
        existing_comments=ctx.existing_comments,
        inline_suggestions=bool(reviewer_config.suggestions_prompt),
        context=context,
    )
    base_system_prompt: str = reviewer_config.system_prompt

    if reviewer_config.suggestions_prompt:
        base_system_prompt = (
            base_system_prompt + "\n\n" + reviewer_config.suggestions_prompt
        )

    file_paths: list[Path] = [Path(changed.file_path) for changed in ctx.changed_files]

    if ctx.workspace is None:
        effective_guidelines: str = resolve_guidelines(
            repo_path=ctx.repo_path,
            default_guidelines=config.prompts.coding_guidelines,
            language_guidelines=config.prompts.language_guidelines,
            file_paths=file_paths,
        )
        if effective_guidelines:
            combined_system_prompt: str = (
                base_system_prompt
                + "\n\n"
                + wrap_tag(TAG_REPO_GUIDELINES, effective_guidelines)
            )
        else:
            combined_system_prompt = base_system_prompt
    else:
        combined_system_prompt = resolve_system_prompt(
            workspace=ctx.workspace,
            config=config,
            bot_system_prompt=base_system_prompt,
            file_paths=file_paths,
        )

    effective_allowed_tools: list[str]

    effective_tool_choice: ToolChoice | None = None

    if isinstance(config.agent, ApiAgentConfig):
        effective_allowed_tools = [SUBMIT_REVIEW_TOOL_NAME]
        effective_max_turns: int = 1
        effective_tool_choice = ToolChoice.REQUIRED
    else:
        effective_allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "Bash(git clone*)",
        ]
        effective_max_turns = 0

    conversation_id, prior_messages = prepare_conversation(
        event=event,
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
        max_turns=effective_max_turns,
        tool_choice=effective_tool_choice,
    )

    save_conversation(
        event=event,
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
            cost=result.cost,
            num_turns=result.num_turns,
            messages=result.messages,
            input_prompt=full_prompt,
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
        num_turns=result.num_turns,
        messages=result.messages,
        input_prompt=full_prompt,
    )


async def post_review_result(
    event: PullRequestEvent,
    result: ReviewResult,
    platform: Platform,
) -> None:
    """
    Post a ReviewResult to the platform.

    Handles three cases: raw output fallback (when JSON parsing failed),
    findings as a native review, or a summary-only reply.

    Args:
        event (PullRequestEvent): The event to post results against.
        result (ReviewResult): The review result to post.
        platform (Platform): The platform client.
    """

    if result.agent_review is None:
        await platform.post_reply(
            event=event,
            reply=CommentReply(body=sanitize_output(result.raw_output)),
        )

        return

    sanitized_findings: list[ReviewFinding] = [
        ReviewFinding(
            file_path=finding.file_path,
            line=finding.line,
            body=sanitize_output(finding.body),
            side=finding.side,
            suggestion=finding.suggestion,
            start_line=finding.start_line,
        )
        for finding in result.valid_findings
    ]
    sanitized_summary: str = sanitize_output(result.effective_summary)

    if sanitized_findings:
        await platform.submit_review(
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            findings=sanitized_findings,
            summary=sanitized_summary,
            event=event,
        )
    else:
        await platform.post_reply(
            event=event,
            reply=CommentReply(body=sanitized_summary),
        )


async def run_and_post_review(
    event: PullRequestEvent,
    prompt: str,
    config: Config,
    platform: Platform,
    workspace_path: str = "",
    conversation_store: ConversationStore | None = None,
    namespace: str = "",
    context: str = "",
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
        platform (Platform): The platform client with reviewer capabilities.
        workspace_path (str): Pre-existing workspace path (skips cloning).
        conversation_store (ConversationStore | None): Conversation store for
            conversation continuity.
        namespace (str): Logical namespace for conversation key isolation.
        context (str): Pre-review context to include in the user message.

    Returns:
        ReviewResult: The review result with findings and summary.
    """

    if config.reviewer is None:
        raise ValueError("ReviewerConfig is required but not configured")

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
        context=context,
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
    existing_comments: list[ExistingComment] | None = None,
    inline_suggestions: bool = True,
    context: str = "",
) -> str:
    """
    Build a prompt for the one-turn reviewer agent.

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

    parts: list[str] = [
        f"Branch: <{TAG_BRANCH_NAME}>{event.pr_branch}</{TAG_BRANCH_NAME}>"
        f" (PR #{event.pr_number} on {event.repo_full_name})",
    ]

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
        parts.append(_format_existing_comments(existing_comments))

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
            location = f" on `<{TAG_FILE_PATH}>{existing.file_path}</{TAG_FILE_PATH}>"

            if existing.line:
                location += f":{existing.line}"

            location += "`"

        resolved_tag: str = " (resolved)" if existing.is_resolved else ""
        header: str = f"**@{existing.author}**{location}{resolved_tag}"
        lines.append(f"{header}\n{wrap_tag(TAG_UNTRUSTED_COMMENT, existing.body)}")

    return "\n\n".join(lines)
