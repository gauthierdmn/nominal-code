from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path

from nominal_code.agent.api.runner import run_api_agent
from nominal_code.agent.sub_agents.planner import plan_exploration_groups
from nominal_code.agent.sub_agents.prompts import load_explore_system_prompt
from nominal_code.agent.sub_agents.result import (
    AggregatedMetrics,
    ExploreGroup,
    ParallelExploreResult,
    SubAgentResult,
)
from nominal_code.agent.sub_agents.types import (
    AGENT_TYPE_TOOLS,
    DEFAULT_MAX_TURNS_PER_SUB_AGENT,
    SUB_AGENT_SYSTEM_SUFFIX,
    AgentType,
)
from nominal_code.llm.provider import LLMProvider
from nominal_code.models import ProviderName

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_FILE_THRESHOLD: int = 8
MAX_COMBINED_NOTES_SIZE: int = 100_000

NOTES_HEADER_TEMPLATE: str = (
    "# Exploration Notes: {label}\n\n**Files:** {files}\n\n---\n\n"
)


async def run_explore(
    groups: list[ExploreGroup],
    cwd: Path,
    provider: LLMProvider,
    model: str,
    provider_name: ProviderName,
    system_prompt: str = "",
    max_turns: int = 0,
    enable_compaction: bool = False,
) -> ParallelExploreResult:
    """
    Run concurrent exploration sub-agents for the given groups.

    Each group is assigned its own sub-agent with isolated tool
    restrictions and turn budget. Sub-agents execute concurrently
    via ``asyncio.gather``. A single failed sub-agent does not
    crash the others.

    The provider instance is shared across all sub-agents and is
    NOT closed by this function.

    Args:
        groups (list[ExploreGroup]): Exploration groups (typically
            from ``plan_exploration_groups``).
        cwd (Path): Working directory for tool execution.
        provider (LLMProvider): Shared LLM provider instance.
        model (str): Model identifier for sub-agents.
        provider_name (ProviderName): Provider identifier for cost
            tracking.
        system_prompt (str): Base system prompt for sub-agents. The
            sub-agent suffix is appended automatically. Uses the
            bundled explore prompt when empty.
        max_turns (int): Total turn budget. Divided across groups
            with a minimum of 4 per group. When 0, each group gets
            ``DEFAULT_MAX_TURNS_PER_SUB_AGENT``.
        enable_compaction (bool): When True, enables session-level
            compaction of older messages to reduce token costs.

    Returns:
        ParallelExploreResult: Aggregated result with per-sub-agent
            results and metrics.
    """

    if not groups:
        return ParallelExploreResult()

    if not system_prompt:
        system_prompt = load_explore_system_prompt()

    full_system_prompt: str = (
        system_prompt
        + "\n\n"
        + SUB_AGENT_SYSTEM_SUFFIX.format(
            agent_type=AgentType.EXPLORE,
        )
    )

    per_group_turns: int = allocate_turns(max_turns, len(groups))

    logger.info(
        "Parallel explore: %d groups, %d turns each (%s)",
        len(groups),
        per_group_turns,
        ", ".join(group.label for group in groups),
    )

    start_ms: int = _now_ms()
    notes_dir: Path = Path(tempfile.mkdtemp(prefix="nominal-notes-"))

    try:
        coroutines = [
            _run_single_sub_agent(
                group=group,
                cwd=cwd,
                provider=provider,
                model=model,
                provider_name=provider_name,
                system_prompt=full_system_prompt,
                max_turns=per_group_turns,
                agent_type=AgentType.EXPLORE,
                enable_compaction=enable_compaction,
                notes_dir=notes_dir,
            )
            for group in groups
        ]

        raw_results: list[SubAgentResult | BaseException] = await asyncio.gather(
            *coroutines,
            return_exceptions=True,
        )
    finally:
        shutil.rmtree(notes_dir, ignore_errors=True)

    successful: list[SubAgentResult] = []

    for group, result in zip(groups, raw_results, strict=True):
        if isinstance(result, BaseException):
            logger.error("Sub-agent '%s' failed: %s", group.label, result)

            continue

        successful.append(result)

    logger.info(
        "Parallel explore: %d/%d agents succeeded",
        len(successful),
        len(groups),
    )

    duration_ms: int = _now_ms() - start_ms
    metrics: AggregatedMetrics = aggregate_metrics(successful, duration_ms)

    return ParallelExploreResult(
        sub_results=tuple(successful),
        metrics=metrics,
    )


async def run_explore_with_planner(
    changed_files: list[str],
    diffs: dict[str, str],
    cwd: Path,
    provider: LLMProvider,
    model: str,
    provider_name: ProviderName,
    system_prompt: str = "",
    planner_model: str = "",
    max_turns: int = 0,
    file_threshold: int = DEFAULT_FILE_THRESHOLD,
    enable_compaction: bool = False,
) -> ParallelExploreResult:
    """
    Run codebase exploration with automatic planning and parallel execution.

    When the number of changed files meets or exceeds ``file_threshold``,
    an LLM planner partitions them into groups and parallel sub-agents
    explore each group concurrently. Below the threshold, a single
    agent explores all files.

    The provider instance is shared and is NOT closed by this function.

    Args:
        changed_files (list[str]): Changed file paths relative to the
            repo root.
        diffs (dict[str, str]): Unified diffs per file.
        cwd (Path): Working directory (repository root) for tool
            execution.
        provider (LLMProvider): Shared LLM provider instance.
        model (str): Model identifier for explore sub-agents.
        provider_name (ProviderName): Provider identifier for cost
            tracking.
        system_prompt (str): Base system prompt. Uses the bundled
            explore prompt when empty.
        planner_model (str): Model for the planner call. Defaults to
            ``model`` when empty.
        max_turns (int): Total turn budget for exploration.
        file_threshold (int): Minimum changed files to trigger
            parallel mode.
        enable_compaction (bool): When True, enables session-level
            compaction of older messages to reduce token costs.

    Returns:
        ParallelExploreResult: Aggregated result with per-sub-agent
            results and metrics.
    """

    if not changed_files:
        return ParallelExploreResult()

    groups: list[ExploreGroup] | None = None

    if len(changed_files) >= file_threshold:
        effective_planner_model: str = planner_model or model

        groups = await plan_exploration_groups(
            changed_files=changed_files,
            diffs=diffs,
            provider=provider,
            model=effective_planner_model,
        )

    if groups is None or len(groups) < 2:
        groups = [
            ExploreGroup(
                label="all-files",
                files=list(changed_files),
                prompt="Explore all changed files.",
            ),
        ]

    return await run_explore(
        groups=groups,
        cwd=cwd,
        provider=provider,
        model=model,
        provider_name=provider_name,
        system_prompt=system_prompt,
        max_turns=max_turns,
        enable_compaction=enable_compaction,
    )


def allocate_turns(total_turns: int, num_groups: int) -> int:
    """
    Calculate per-group turn budget.

    When ``total_turns`` is 0 (unlimited), each group gets
    ``DEFAULT_MAX_TURNS_PER_SUB_AGENT``. Otherwise the budget
    is divided across groups with a minimum of 4 per group.

    Args:
        total_turns (int): Total turn budget. When 0, returns the
            default.
        num_groups (int): Number of groups to divide among.

    Returns:
        int: Turns per group, minimum 4.
    """

    if total_turns <= 0 or num_groups <= 0:
        return DEFAULT_MAX_TURNS_PER_SUB_AGENT

    return max(4, total_turns // num_groups)


def aggregate_metrics(
    sub_results: list[SubAgentResult],
    duration_ms: int,
) -> AggregatedMetrics:
    """
    Aggregate metrics from multiple sub-agent results.

    Sums token counts, API calls, turns, and costs across all agents.
    Duration is wall-clock (not sum, since agents run in parallel).

    Args:
        sub_results (list[SubAgentResult]): Individual sub-agent results.
        duration_ms (int): Wall-clock duration in milliseconds.

    Returns:
        AggregatedMetrics: Aggregated metrics.
    """

    total_turns: int = 0
    total_api_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation: int = 0
    total_cache_read: int = 0
    total_cost: float = 0.0
    has_cost: bool = False

    for sub_result in sub_results:
        total_turns += sub_result.num_turns
        cost = sub_result.cost

        if cost is not None:
            total_api_calls += cost.num_api_calls
            total_input_tokens += cost.total_input_tokens
            total_output_tokens += cost.total_output_tokens
            total_cache_creation += cost.total_cache_creation_tokens
            total_cache_read += cost.total_cache_read_tokens

            if cost.total_cost_usd is not None:
                total_cost += cost.total_cost_usd
                has_cost = True

    return AggregatedMetrics(
        total_turns=total_turns,
        total_api_calls=total_api_calls,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cache_creation_tokens=total_cache_creation,
        total_cache_read_tokens=total_cache_read,
        total_cost_usd=total_cost if has_cost else None,
        duration_ms=duration_ms,
        num_groups=len(sub_results),
        group_labels=tuple(sub_result.group.label for sub_result in sub_results),
    )


async def _run_single_sub_agent(
    group: ExploreGroup,
    cwd: Path,
    provider: LLMProvider,
    model: str,
    provider_name: ProviderName,
    system_prompt: str,
    max_turns: int,
    agent_type: AgentType,
    enable_compaction: bool,
    notes_dir: Path | None = None,
) -> SubAgentResult:
    """
    Run a single sub-agent for one exploration group.

    Builds the user prompt from the group's diffs and exploration
    instructions, then delegates to ``run_api_agent`` with the
    tool restrictions for the given agent type.

    When ``notes_dir`` is provided, creates a notes file for the agent
    to write findings into via the WriteNotes tool. The file content is
    read back after execution and stored in ``SubAgentResult.notes``.

    Args:
        group (ExploreGroup): The exploration group to process.
        cwd (Path): Working directory for tool execution.
        provider (LLMProvider): Shared LLM provider instance.
        model (str): Model identifier.
        provider_name (ProviderName): Provider identifier for cost
            tracking.
        system_prompt (str): Full system prompt (with sub-agent suffix).
        max_turns (int): Maximum agentic turns for this sub-agent.
        agent_type (AgentType): The sub-agent type (determines allowed
            tools).
        enable_compaction (bool): When True, enables compaction.
        notes_dir (Path | None): Directory for notes files. When
            provided, a notes file is created for this sub-agent.

    Returns:
        SubAgentResult: The sub-agent's result.
    """

    allowed_tools: list[str] = AGENT_TYPE_TOOLS[agent_type]
    notes_file_path: Path | None = None

    if notes_dir is not None:
        safe_label: str = re.sub(r"[^a-zA-Z0-9_-]", "_", group.label)
        notes_file_path = notes_dir / f"{safe_label}.md"

        header: str = NOTES_HEADER_TEMPLATE.format(
            label=group.label,
            files=", ".join(group.files),
        )
        notes_file_path.write_text(header, encoding="utf-8")

    logger.info(
        "Starting sub-agent '%s': %d files, %d turns",
        group.label,
        len(group.files),
        max_turns,
    )

    result = await run_api_agent(
        prompt=group.prompt,
        cwd=cwd,
        model=model,
        provider=provider,
        max_turns=max_turns,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        provider_name=provider_name,
        enable_compaction=enable_compaction,
        notes_file_path=notes_file_path,
    )

    notes_content: str = ""

    if notes_file_path is not None and notes_file_path.exists():
        notes_content = notes_file_path.read_text(encoding="utf-8")

    logger.info(
        "Sub-agent '%s' complete: %d turns, %d chars of notes",
        group.label,
        result.num_turns,
        len(notes_content),
    )

    return SubAgentResult(
        group=group,
        output=result.output,
        is_error=result.is_error,
        num_turns=result.num_turns,
        duration_ms=result.duration_ms,
        messages=result.messages,
        cost=result.cost,
        notes=notes_content,
    )


def assemble_notes(
    sub_results: tuple[SubAgentResult, ...],
    max_size: int = MAX_COMBINED_NOTES_SIZE,
) -> str:
    """
    Combine notes from all sub-agents into a single context string.

    Concatenates non-empty notes from each sub-agent with a preamble
    header. Truncates at ``max_size`` characters to prevent the
    analysis prompt from growing too large.

    Args:
        sub_results (tuple[SubAgentResult, ...]): Sub-agent results.
        max_size (int): Maximum combined size in characters.

    Returns:
        str: Combined notes with preamble, or empty string if no notes
            were written.
    """

    parts: list[str] = []
    total_size: int = 0

    for sub_result in sub_results:
        if not sub_result.notes:
            continue

        content: str = sub_result.notes
        remaining: int = max_size - total_size

        if remaining <= 0:
            break

        if len(content) > remaining:
            content = content[:remaining] + "\n\n... (truncated)"

        parts.append(content)
        total_size += len(content)

    if not parts:
        return ""

    combined: str = "\n\n".join(parts)

    return (
        "## Codebase Exploration Notes\n\n"
        "The following findings were gathered by exploration agents that "
        "searched the repository for callers, tests, type definitions, "
        "and knock-on effects related to the changed files.\n\n"
        f"{combined}"
    )


def _now_ms() -> int:
    """
    Return the current time in milliseconds.

    Returns:
        int: Current time as integer milliseconds.
    """

    return int(time.monotonic() * 1000)
