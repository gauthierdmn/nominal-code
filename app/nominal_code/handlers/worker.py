from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nominal_code.bot_type import BotType
from nominal_code.handlers.common import (
    handle_agent_errors,
    resolve_branch,
    resolve_system_prompt,
    run_and_track_session,
    setup_workspace,
)
from nominal_code.platforms.base import CommentEvent, CommentReply

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.git_workspace import GitWorkspace
    from nominal_code.platforms.base import Platform
    from nominal_code.session import SessionStore

logger: logging.Logger = logging.getLogger(__name__)


async def review_and_fix(
    event: CommentEvent,
    prompt: str,
    config: Config,
    platform: Platform,
    session_store: SessionStore,
) -> None:
    """
    Review and fix code using the worker bot: clone, run agent, post reply.

    Args:
        event (CommentEvent): The comment event that triggered the worker.
        prompt (str): The extracted prompt.
        config (Config): Application configuration.
        platform (Platform): The platform client.
        session_store (SessionStore): Agent session store.
    """

    effective_event: CommentEvent | None = await resolve_branch(event, platform)  # type: ignore[assignment]

    if effective_event is None:
        return

    async with handle_agent_errors(event, platform, "worker"):
        workspace: GitWorkspace = await setup_workspace(effective_event, config)

        if config.worker is None:
            raise RuntimeError("Worker config is required but not configured")

        file_paths: list[str] = (
            [effective_event.file_path] if effective_event.file_path else []
        )
        system_prompt: str = resolve_system_prompt(
            workspace, config, config.worker.system_prompt, file_paths,
        )
        full_prompt: str = build_prompt(
            effective_event,
            prompt,
            deps_path=workspace.deps_path,
        )

        result = await run_and_track_session(
            event=event,
            bot_type=BotType.WORKER,
            session_store=session_store,
            system_prompt=system_prompt,
            prompt=full_prompt,
            cwd=workspace.repo_path,
            config=config,
        )

        await platform.post_reply(event, CommentReply(body=result.output))

        logger.info(
            "Worker finished for %s#%d (turns=%d, duration=%dms)",
            event.repo_full_name,
            event.pr_number,
            result.num_turns,
            result.duration_ms,
        )


def build_prompt(
    event: CommentEvent,
    user_prompt: str,
    deps_path: str = "",
) -> str:
    """
    Build a contextual prompt for the agent from the event.

    Includes file path, diff hunk, and branch context when available.

    Args:
        event (CommentEvent): The comment event with PR context.
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
