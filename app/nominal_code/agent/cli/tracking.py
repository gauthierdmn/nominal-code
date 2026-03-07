from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nominal_code.agent.providers.types import Message
from nominal_code.agent.result import AgentResult
from nominal_code.agent.runner import run_agent
from nominal_code.config import ApiAgentConfig
from nominal_code.models import BotType
from nominal_code.platforms.base import PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.agent.memory import ConversationStore
    from nominal_code.config import Config

logger: logging.Logger = logging.getLogger(__name__)


async def run_and_track_conversation(
    event: PullRequestEvent,
    bot_type: BotType,
    system_prompt: str,
    prompt: str,
    cwd: Path,
    config: Config,
    allowed_tools: list[str] | None = None,
    conversation_id_override: str | None = None,
    conversation_store: ConversationStore | None = None,
) -> AgentResult:
    """
    Run the agent and persist the conversation ID if a store is provided.

    Looks up the existing conversation (or uses ``conversation_id_override`` for
    retries), calls ``run_agent``, and stores the new conversation ID on
    success.

    For API mode, loads prior conversation messages and stores the updated
    state after a successful run.

    Args:
        event (PullRequestEvent): The event that triggered the agent run.
        bot_type (BotType): Which bot personality is running.
        system_prompt (str): The composed system prompt.
        prompt (str): The user/PR prompt to send to the agent.
        cwd (Path): Working directory for the agent.
        config (Config): Application configuration.
        allowed_tools (list[str] | None): Restrict which tools the agent may use.
        conversation_id_override (str | None): Override conversation ID
            (e.g. for retries).
        conversation_store (ConversationStore | None): Conversation store
            (None to skip persistence).

    Returns:
        AgentResult: The agent execution result.
    """

    use_conversation_ids: bool = not isinstance(config.agent, ApiAgentConfig)
    existing_conversation_id: str | None = conversation_id_override

    if (
        use_conversation_ids
        and existing_conversation_id is None
        and conversation_store is not None
    ):
        existing_conversation_id = conversation_store.get_conversation_id(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
        )

    prior_messages: list[Message] | None = None

    if isinstance(config.agent, ApiAgentConfig) and conversation_store is not None:
        prior_messages = conversation_store.get_messages(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
        )

    result: AgentResult = await run_agent(
        prompt=prompt,
        cwd=cwd,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        agent_config=config.agent,
        conversation_id=existing_conversation_id,
        prior_messages=prior_messages,
    )

    if conversation_store is not None and result.conversation_id:
        conversation_store.set_conversation_id(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
            value=result.conversation_id,
        )

    if (
        isinstance(config.agent, ApiAgentConfig)
        and conversation_store is not None
        and not result.is_error
        and result.messages
    ):
        conversation_store.set_messages(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
            value=list(result.messages),
        )

    return result
