from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nominal_code.agent.api.runner import handle_event as handle_event_api
from nominal_code.agent.api.runner import run as run_api
from nominal_code.agent.cli.runner import handle_event as handle_event_cli
from nominal_code.agent.cli.runner import run as run_cli
from nominal_code.agent.result import AgentResult
from nominal_code.config import AgentConfig, ApiAgentConfig, CliAgentConfig
from nominal_code.llm.messages import Message
from nominal_code.llm.registry import create_provider
from nominal_code.models import BotType
from nominal_code.platforms.base import PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore

logger: logging.Logger = logging.getLogger(__name__)


async def run(
    prompt: str,
    cwd: Path,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    agent_config: AgentConfig | None = None,
    conversation_id: str | None = None,
    prior_messages: list[Message] | None = None,
) -> AgentResult:
    """
    Run the agent and return the result.

    Delegates to either the CLI-based runner (default, for webhook server)
    or the API-based runner (for CI/CD environments) based on the type of
    ``agent_config``.

    Args:
        prompt (str): The user's prompt to pass to the agent.
        cwd (Path): Working directory for the agent.
        system_prompt (str): Optional system prompt for the agent.
        allowed_tools (list[str] | None): Restrict which tools the agent may use.
        agent_config (AgentConfig | None): Agent configuration. Pass an
            ``ApiAgentConfig`` for CI mode or a ``CliAgentConfig`` (default)
            for CLI/webhook mode.
        conversation_id (str | None): Optional conversation ID to resume.
        prior_messages (list[Message] | None): Prior conversation messages
            for multi-turn continuity (API mode only).

    Returns:
        AgentResult: The parsed result from the agent.
    """

    if agent_config is None:
        agent_config = CliAgentConfig()

    if isinstance(agent_config, ApiAgentConfig):
        provider = create_provider(name=agent_config.provider.name)

        try:
            return await run_api(
                prompt=prompt,
                cwd=cwd,
                model=agent_config.provider.model,
                provider=provider,
                max_turns=agent_config.max_turns,
                system_prompt=system_prompt,
                allowed_tools=allowed_tools,
                prior_messages=prior_messages,
                provider_name=agent_config.provider.name,
            )
        finally:
            await provider.close()

    return await run_cli(
        prompt=prompt,
        cwd=cwd,
        model=agent_config.model,
        max_turns=agent_config.max_turns,
        cli_path=agent_config.cli_path,
        conversation_id=conversation_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
    )


async def handle_event(
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
    Dispatch a PR event to the appropriate backend's handle_event.

    Routes to the CLI or API runner based on the agent config type and
    delegates conversation persistence to the chosen backend.

    Args:
        event (PullRequestEvent): The event that triggered the agent run.
        bot_type (BotType): Which bot personality is running.
        system_prompt (str): The composed system prompt.
        prompt (str): The user/PR prompt to send to the agent.
        cwd (Path): Working directory for the agent.
        config (Config): Application configuration.
        allowed_tools (list[str] | None): Restrict which tools the agent
            may use.
        conversation_id_override (str | None): Override conversation ID
            (e.g. for retries). Only used by the CLI backend.
        conversation_store (ConversationStore | None): Conversation store
            (None to skip persistence).

    Returns:
        AgentResult: The agent execution result.
    """

    if isinstance(config.agent, ApiAgentConfig):
        return await handle_event_api(
            event=event,
            bot_type=bot_type,
            system_prompt=system_prompt,
            prompt=prompt,
            cwd=cwd,
            config=config,
            allowed_tools=allowed_tools,
            conversation_store=conversation_store,
        )

    return await handle_event_cli(
        event=event,
        bot_type=bot_type,
        system_prompt=system_prompt,
        prompt=prompt,
        cwd=cwd,
        config=config,
        allowed_tools=allowed_tools,
        conversation_id_override=conversation_id_override,
        conversation_store=conversation_store,
    )
