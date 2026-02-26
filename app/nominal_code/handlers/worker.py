from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nominal_code.agent_runner import AgentResult, run_agent
from nominal_code.bot_type import BotType
from nominal_code.git_workspace import GitWorkspace
from nominal_code.handlers.common import (
    build_system_prompt,
    resolve_branch,
    resolve_guidelines,
)
from nominal_code.platforms.base import CommentReply, PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform
    from nominal_code.session import SessionStore

logger: logging.Logger = logging.getLogger(__name__)


async def review_and_fix(
    event: PullRequestEvent,
    prompt: str,
    config: Config,
    platform: Platform,
    session_store: SessionStore,
) -> None:
    """
    Review and fix code using the worker bot: clone, run agent, post reply.

    Args:
        event (PullRequestEvent): The parsed event that triggered the worker.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (Platform): The platform client.
        session_store (SessionStore): Agent session store.
    """

    effective_event: PullRequestEvent | None = await resolve_branch(event, platform)

    if effective_event is None:
        return

    workspace: GitWorkspace = GitWorkspace(
        base_dir=config.workspace_base_dir,
        repo_full_name=effective_event.repo_full_name,
        pr_number=effective_event.pr_number,
        clone_url=effective_event.clone_url,
        branch=effective_event.pr_branch,
    )

    try:
        await workspace.ensure_ready()
    except RuntimeError:
        logger.exception("Failed to set up workspace")

        await platform.post_reply(
            event,
            CommentReply(body="Failed to set up the git workspace."),
        )

        return

    workspace.ensure_deps_dir()

    full_prompt: str = build_prompt(
        effective_event,
        prompt,
        deps_path=workspace.deps_path,
    )
    existing_session: str | None = session_store.get(
        event.platform,
        event.repo_full_name,
        event.pr_number,
        BotType.WORKER.value,
    )

    try:
        if config.worker is None:
            raise RuntimeError("Worker config is required but not configured")

        file_paths: list[str] = (
            [effective_event.file_path] if effective_event.file_path else []
        )

        effective_guidelines: str = resolve_guidelines(
            workspace.repo_path,
            config.coding_guidelines,
            config.language_guidelines,
            file_paths,
        )

        combined_system_prompt: str = build_system_prompt(
            config.worker.system_prompt,
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
        )

        if result.session_id:
            session_store.set(
                event.platform,
                event.repo_full_name,
                event.pr_number,
                BotType.WORKER.value,
                result.session_id,
            )

        reply: CommentReply = CommentReply(body=result.output)

        await platform.post_reply(event, reply)

        logger.info(
            "Worker finished for %s#%d (turns=%d, duration=%dms)",
            event.repo_full_name,
            event.pr_number,
            result.num_turns,
            result.duration_ms,
        )
    except Exception:
        logger.exception("Error running agent (worker)")

        await platform.post_reply(
            event,
            CommentReply(body="An unexpected error occurred while running the agent."),
        )


def build_prompt(
    event: PullRequestEvent,
    user_prompt: str,
    deps_path: str = "",
) -> str:
    """
    Build a contextual prompt for the agent from the event.

    Includes file path, diff hunk, and branch context when available.

    Args:
        event (PullRequestEvent): The event with PR context.
        user_prompt (str): The user's extracted prompt text.
        deps_path (str): Path to the shared dependencies directory.

    Returns:
        str: The full prompt to send to the agent.
    """

    parts: list[str] = []

    if event.file_path:
        parts.append(f"File: {event.file_path}")

    if event.diff_hunk:
        parts.append(f"Diff context:\n```\n{event.diff_hunk}\n```")

    parts.append(
        f"Branch: {event.pr_branch} (PR #{event.pr_number} on {event.repo_full_name})"
    )

    parts.append(f"Request: {user_prompt}")

    if deps_path:
        parts.append(
            f"Dependencies directory: {deps_path}\n"
            "If you need to understand a private dependency that is not available on\n"
            "PyPI, you can `git clone` it into this directory. Clone with `--depth=1`\n"
            "to minimize download time. Dependencies cloned here are shared across\n"
            "PRs for this repository."
        )

    return "\n\n".join(parts)
