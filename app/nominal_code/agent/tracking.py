from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from nominal_code.agent.runner import AgentResult, run_agent
from nominal_code.models import BotType
from nominal_code.platforms.base import PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.agent.session import SessionStore
    from nominal_code.config import Config

DEFAULT_PERMISSION_MODE: Literal["bypassPermissions"] = "bypassPermissions"

logger: logging.Logger = logging.getLogger(__name__)


async def run_and_track_session(
    event: PullRequestEvent,
    bot_type: BotType,
    session_store: SessionStore | None,
    system_prompt: str,
    prompt: str,
    cwd: Path,
    config: Config,
    allowed_tools: list[str] | None = None,
    session_id_override: str | None = None,
) -> AgentResult:
    """
    Run the agent and persist the session ID if a store is provided.

    Looks up the existing session (or uses ``session_id_override`` for retries),
    calls ``run_agent``, and stores the new session ID on success.

    Args:
        event (PullRequestEvent): The event that triggered the agent run.
        bot_type (BotType): Which bot personality is running.
        session_store (SessionStore | None): Session store (None to skip).
        system_prompt (str): The composed system prompt.
        prompt (str): The user/PR prompt to send to the agent.
        cwd (Path): Working directory for the agent.
        config (Config): Application configuration.
        allowed_tools (list[str] | None): Restrict which tools the agent may use.
        session_id_override (str | None): Override session ID (e.g. for retries).

    Returns:
        AgentResult: The agent execution result.
    """

    existing_session: str | None = session_id_override

    if existing_session is None and session_store is not None:
        existing_session = session_store.get(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
        )

    result: AgentResult = await run_agent(
        prompt=prompt,
        cwd=cwd,
        model=config.agent_model,
        max_turns=config.agent_max_turns,
        cli_path=config.agent_cli_path,
        session_id=existing_session or "",
        system_prompt=system_prompt,
        permission_mode=DEFAULT_PERMISSION_MODE,
        allowed_tools=allowed_tools,
    )

    if session_store is not None and result.session_id:
        session_store.set(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
            session_id=result.session_id,
        )

    return result
