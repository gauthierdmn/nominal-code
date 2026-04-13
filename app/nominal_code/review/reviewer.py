from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
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
    TAG_REPO_GUIDELINES,
    resolve_guidelines,
    resolve_system_prompt,
    wrap_tag,
)
from nominal_code.agent.sandbox import sanitize_output
from nominal_code.agent.sub_agent import DEFAULT_MAX_TURNS_PER_SUB_AGENT, SubAgentConfig
from nominal_code.config import ApiAgentConfig
from nominal_code.llm.messages import ToolChoice
from nominal_code.llm.registry import create_provider
from nominal_code.models import (
    ChangedFile,
    ReviewFinding,
)
from nominal_code.platforms.base import (
    CommentReply,
    ExistingComment,
    PullRequestEvent,
)
from nominal_code.prompts import load_prompt
from nominal_code.review.diff import (
    build_effective_summary,
    filter_findings,
)
from nominal_code.review.output import (
    build_fallback_comment,
    parse_review_output,
    repair_review_output,
)
from nominal_code.review.prompts import (
    build_fallback_review_prompt,
    build_reviewer_prompt,
)
from nominal_code.workspace.setup import create_workspace

if TYPE_CHECKING:
    from nominal_code.agent.result import AgentResult
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.llm.cost import CostSummary
    from nominal_code.llm.messages import Message
    from nominal_code.llm.provider import LLMProvider
    from nominal_code.models import AgentReview
    from nominal_code.platforms.base import Platform
    from nominal_code.workspace.git import GitWorkspace


MAX_EXISTING_COMMENTS: int = 50
REVIEWER_TEMP_DIR_PREFIX: str = "nominal-reviewer-"
REVIEWER_NOTES_FILENAME: str = "notes.md"
REVIEWER_NOTES_HEADER: str = "# Review Notes\n\n"
EXPLORE_ALLOWED_TOOLS: list[str] = ["Read", "Glob", "Grep", "Bash", "WriteNotes"]
EXPLORE_SYSTEM_SUFFIX: str = load_prompt("explore/suffix.md")

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
        sub_agent_costs (tuple[CostSummary, ...]): Cost summaries from
            sub-agents spawned during the review.
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
    sub_agent_costs: tuple[CostSummary, ...] = ()


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

    full_prompt: str = build_reviewer_prompt(
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
    sub_agent_configs: dict[str, SubAgentConfig] | None = None
    notes_file_path: Path | None = None
    explore_provider: LLMProvider | None = None

    if isinstance(config.agent, ApiAgentConfig):
        agent_config: ApiAgentConfig = config.agent

        effective_allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "Bash",
            "WriteNotes",
            SUBMIT_REVIEW_TOOL_NAME,
        ]
        effective_max_turns: int = agent_config.reviewer_max_turns

        explore_provider = create_provider(name=agent_config.explorer.name)

        explore_system_prompt: str = (
            load_prompt("explore/explorer.md")
            + "\n\n"
            + EXPLORE_SYSTEM_SUFFIX.format(agent_type="explore")
        )

        sub_agent_configs = {
            "explore": SubAgentConfig(
                provider=explore_provider,
                model=agent_config.explorer.model,
                provider_name=agent_config.explorer.name,
                system_prompt=explore_system_prompt,
                max_turns=DEFAULT_MAX_TURNS_PER_SUB_AGENT,
                allowed_tools=EXPLORE_ALLOWED_TOOLS,
                description=(
                    "Fast codebase explorer. Use for deep investigation: "
                    "finding callers, checking test coverage, tracing type "
                    "hierarchies. For simple lookups use Read/Grep directly."
                ),
            ),
        }

        notes_dir: Path = Path(
            tempfile.mkdtemp(prefix=REVIEWER_TEMP_DIR_PREFIX),
        )
        notes_file_path = notes_dir / REVIEWER_NOTES_FILENAME
        notes_file_path.write_text(REVIEWER_NOTES_HEADER, encoding="utf-8")
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

    logger.info(
        "Reviewer system prompt for %s#%d (%d chars):\n%s",
        event.repo_full_name,
        event.pr_number,
        len(combined_system_prompt),
        combined_system_prompt,
    )
    logger.info(
        "Reviewer user prompt for %s#%d (%d chars):\n%s",
        event.repo_full_name,
        event.pr_number,
        len(full_prompt),
        full_prompt,
    )

    try:
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
            notes_file_path=notes_file_path,
            sub_agent_configs=sub_agent_configs,
        )

        if result.exhausted_without_review:
            logger.warning(
                "Reviewer exhausted turns for %s#%d, "
                "falling back to notes-based review",
                event.repo_full_name,
                event.pr_number,
            )

            notes_content: str = ""

            if notes_file_path is not None and notes_file_path.exists():
                notes_content = notes_file_path.read_text(encoding="utf-8")

            fallback_prompt: str = build_fallback_review_prompt(
                notes=notes_content,
                original_prompt=full_prompt,
            )

            result = await invoke_agent(
                prompt=fallback_prompt,
                cwd=ctx.repo_path,
                system_prompt=combined_system_prompt,
                allowed_tools=[SUBMIT_REVIEW_TOOL_NAME],
                agent_config=config.agent,
                max_turns=1,
                tool_choice=ToolChoice.REQUIRED,
            )
    finally:
        if explore_provider is not None:
            await explore_provider.close()

        if notes_file_path is not None:
            shutil.rmtree(notes_file_path.parent, ignore_errors=True)

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
            sub_agent_costs=result.sub_agent_costs,
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

    _log_review_costs(
        event=event,
        reviewer_cost=result.cost,
        sub_agent_costs=result.sub_agent_costs,
        findings_count=len(review_result.findings),
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
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
        sub_agent_costs=result.sub_agent_costs,
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


def _log_review_costs(
    event: PullRequestEvent,
    reviewer_cost: CostSummary | None,
    sub_agent_costs: tuple[CostSummary, ...],
    findings_count: int,
    num_turns: int,
    duration_ms: int,
) -> None:
    """
    Log the reviewer step cost and the aggregated total.

    Args:
        event (PullRequestEvent): The PR event for log context.
        reviewer_cost (CostSummary | None): Cost from the reviewer step.
        sub_agent_costs (tuple[CostSummary, ...]): Cost summaries from
            sub-agents spawned via the Agent tool.
        findings_count (int): Number of findings produced.
        num_turns (int): Number of reviewer turns.
        duration_ms (int): Wall-clock duration of the reviewer step.
    """

    pr_ref: str = f"{event.repo_full_name}#{event.pr_number}"

    reviewer_tokens_in: int = 0
    reviewer_tokens_out: int = 0
    reviewer_cost_usd: float = 0.0

    if reviewer_cost is not None:
        reviewer_tokens_in = reviewer_cost.total_input_tokens
        reviewer_tokens_out = reviewer_cost.total_output_tokens
        reviewer_cost_usd = reviewer_cost.total_cost_usd or 0.0

    logger.info(
        "Step cost [reviewer] for %s: tokens_in=%d, tokens_out=%d, "
        "api_calls=%d, cost=$%.4f",
        pr_ref,
        reviewer_tokens_in,
        reviewer_tokens_out,
        reviewer_cost.num_api_calls if reviewer_cost else 0,
        reviewer_cost_usd,
    )

    total_tokens_in: int = reviewer_tokens_in
    total_tokens_out: int = reviewer_tokens_out
    total_cost: float = reviewer_cost_usd

    for cost in sub_agent_costs:
        total_tokens_in += cost.total_input_tokens
        total_tokens_out += cost.total_output_tokens
        total_cost += cost.total_cost_usd or 0.0

    logger.info(
        "Review complete for %s (findings=%d, turns=%d, duration=%dms, "
        "total_tokens_in=%d, total_tokens_out=%d, total_cost=$%.4f)",
        pr_ref,
        findings_count,
        num_turns,
        duration_ms,
        total_tokens_in,
        total_tokens_out,
        total_cost,
    )
