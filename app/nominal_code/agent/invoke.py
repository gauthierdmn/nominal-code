from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nominal_code.agent.api.runner import run_api_agent
from nominal_code.agent.cli.runner import run_cli_agent
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


async def invoke_agent(
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
    Invoke the agent for a PR event with conversation persistence.

    Routes to the CLI or API runner based on the agent config type,
    handles loading and saving conversation state from/to the store.

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

    backend: str = "api" if isinstance(config.agent, ApiAgentConfig) else "cli"

    logger.info(
        "Handling %s event for %s#%d (bot=%s, backend=%s)",
        event.event_type.value,
        event.repo_full_name,
        event.pr_number,
        bot_type.value,
        backend,
    )

    if isinstance(config.agent, ApiAgentConfig):
        return await _invoke_api_agent(
            event=event,
            bot_type=bot_type,
            system_prompt=system_prompt,
            prompt=prompt,
            cwd=cwd,
            agent_config=config.agent,
            allowed_tools=allowed_tools,
            conversation_store=conversation_store,
        )

    return await _invoke_cli_agent(
        event=event,
        bot_type=bot_type,
        system_prompt=system_prompt,
        prompt=prompt,
        cwd=cwd,
        agent_config=config.agent,
        allowed_tools=allowed_tools,
        conversation_id_override=conversation_id_override,
        conversation_store=conversation_store,
    )


async def invoke_agent_stateless(
    prompt: str,
    cwd: Path,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    agent_config: AgentConfig | None = None,
    conversation_id: str | None = None,
    prior_messages: list[Message] | None = None,
) -> AgentResult:
    """
    Run the agent without conversation persistence.

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
            return await run_api_agent(
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

    return await run_cli_agent(
        prompt=prompt,
        cwd=cwd,
        model=agent_config.model,
        max_turns=agent_config.max_turns,
        cli_path=agent_config.cli_path,
        conversation_id=conversation_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
    )


async def _invoke_cli_agent(
    event: PullRequestEvent,
    bot_type: BotType,
    system_prompt: str,
    prompt: str,
    cwd: Path,
    agent_config: CliAgentConfig,
    allowed_tools: list[str] | None = None,
    conversation_id_override: str | None = None,
    conversation_store: ConversationStore | None = None,
) -> AgentResult:
    """
    Run the CLI agent and persist the conversation ID.

    Args:
        event (PullRequestEvent): The event that triggered the agent run.
        bot_type (BotType): Which bot personality is running.
        system_prompt (str): The composed system prompt.
        prompt (str): The user/PR prompt to send to the agent.
        cwd (Path): Working directory for the agent.
        agent_config (CliAgentConfig): CLI agent configuration.
        allowed_tools (list[str] | None): Restrict which tools the agent
            may use.
        conversation_id_override (str | None): Override conversation ID
            (e.g. for retries).
        conversation_store (ConversationStore | None): Conversation store
            (None to skip persistence).

    Returns:
        AgentResult: The agent execution result.
    """

    existing_conversation_id: str | None = conversation_id_override

    if existing_conversation_id is None and conversation_store is not None:
        existing_conversation_id = conversation_store.get_conversation_id(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
        )

    result: AgentResult = await run_cli_agent(
        prompt=prompt,
        cwd=cwd,
        model=agent_config.model,
        max_turns=agent_config.max_turns,
        cli_path=agent_config.cli_path,
        conversation_id=existing_conversation_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
    )

    if conversation_store is not None and result.conversation_id:
        conversation_store.set_conversation_id(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
            value=result.conversation_id,
        )

    return result


async def _invoke_api_agent(
    event: PullRequestEvent,
    bot_type: BotType,
    system_prompt: str,
    prompt: str,
    cwd: Path,
    agent_config: ApiAgentConfig,
    allowed_tools: list[str] | None = None,
    conversation_store: ConversationStore | None = None,
) -> AgentResult:
    """
    Run the API agent and persist conversation state.

    Args:
        event (PullRequestEvent): The event that triggered the agent run.
        bot_type (BotType): Which bot personality is running.
        system_prompt (str): The composed system prompt.
        prompt (str): The user/PR prompt to send to the agent.
        cwd (Path): Working directory for the agent.
        agent_config (ApiAgentConfig): API agent configuration.
        allowed_tools (list[str] | None): Restrict which tools the agent
            may use.
        conversation_store (ConversationStore | None): Conversation store
            (None to skip persistence).

    Returns:
        AgentResult: The agent execution result.
    """

    prior_messages: list[Message] | None = None

    if conversation_store is not None:
        prior_messages = conversation_store.get_messages(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
        )

    provider = create_provider(name=agent_config.provider.name)

    try:
        result: AgentResult = await run_api_agent(
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

    if conversation_store is not None and result.conversation_id:
        conversation_store.set_conversation_id(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
            value=result.conversation_id,
        )

    if conversation_store is not None and not result.is_error and result.messages:
        conversation_store.set_messages(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            bot_type=bot_type,
            value=list(result.messages),
        )

    return result
