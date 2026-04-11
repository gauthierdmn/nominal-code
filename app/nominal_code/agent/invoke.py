from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nominal_code.agent.api.runner import run_api_agent
from nominal_code.agent.cli.runner import run_cli_agent
from nominal_code.agent.result import AgentResult
from nominal_code.config import AgentConfig, ApiAgentConfig, CliAgentConfig
from nominal_code.llm.messages import Message, ToolChoice
from nominal_code.llm.registry import create_provider
from nominal_code.platforms.base import PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.conversation.base import ConversationStore

logger: logging.Logger = logging.getLogger(__name__)


def prepare_conversation(
    event: PullRequestEvent,
    agent_config: AgentConfig,
    conversation_store: ConversationStore | None,
    conversation_id_override: str | None = None,
    namespace: str = "",
) -> tuple[str | None, list[Message] | None]:
    """
    Load conversation state from the store before invoking the agent.

    For CLI agents, loads the conversation ID (from override or store).
    For API agents, loads prior messages from the store.

    Args:
        event (PullRequestEvent): The event with platform/repo/PR context.
        agent_config (AgentConfig): The agent configuration.
        conversation_store (ConversationStore | None): Conversation store.
        conversation_id_override (str | None): Override conversation ID
            (e.g. for retries). Only used by the CLI backend.
        namespace (str): Logical namespace for key isolation.

    Returns:
        tuple[str | None, list[Message] | None]: A pair of
            (conversation_id, prior_messages).
    """

    conversation_id: str | None = None
    prior_messages: list[Message] | None = None

    if isinstance(agent_config, CliAgentConfig):
        conversation_id = conversation_id_override

        if conversation_id is None and conversation_store is not None:
            conversation_id = conversation_store.get_conversation_id(
                platform=event.platform,
                repo=event.repo_full_name,
                pr_number=event.pr_number,
                namespace=namespace,
            )
    elif isinstance(agent_config, ApiAgentConfig) and conversation_store is not None:
        prior_messages = conversation_store.get_messages(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            namespace=namespace,
        )

    return conversation_id, prior_messages


async def invoke_agent(
    prompt: str,
    cwd: Path,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    agent_config: AgentConfig | None = None,
    conversation_id: str | None = None,
    prior_messages: list[Message] | None = None,
    max_turns: int = 0,
    tool_choice: ToolChoice | None = None,
) -> AgentResult:
    """
    Run the agent by routing to the CLI or API backend.

    Delegates to either the CLI-based runner (default, for webhook server)
    or the API-based runner (for CI/CD environments) based on the type of
    ``agent_config``.

    Args:
        prompt (str): The user's prompt to pass to the agent.
        cwd (Path): Working directory for the agent.
        system_prompt (str): Optional system prompt for the agent.
        allowed_tools (list[str] | None): Restrict which tools the agent
            may use.
        agent_config (AgentConfig | None): Agent configuration. Pass an
            ``ApiAgentConfig`` for CI mode or a ``CliAgentConfig`` (default)
            for CLI/webhook mode.
        conversation_id (str | None): Optional conversation ID to resume.
        prior_messages (list[Message] | None): Prior conversation messages
            for multi-turn continuity (API mode only).
        max_turns (int): Maximum agentic turns (0 for unlimited).
        tool_choice (ToolChoice | None): Controls whether the model must
            use tools (API mode only, ignored in CLI mode).

    Returns:
        AgentResult: The parsed result from the agent.
    """

    if agent_config is None:
        agent_config = CliAgentConfig()

    if isinstance(agent_config, ApiAgentConfig):
        provider = create_provider(name=agent_config.reviewer.name)

        try:
            return await run_api_agent(
                prompt=prompt,
                cwd=cwd,
                model=agent_config.reviewer.model,
                provider=provider,
                max_turns=max_turns,
                system_prompt=system_prompt,
                allowed_tools=allowed_tools,
                prior_messages=prior_messages,
                provider_name=agent_config.reviewer.name,
                tool_choice=tool_choice,
            )
        finally:
            await provider.close()

    return await run_cli_agent(
        prompt=prompt,
        cwd=cwd,
        model=agent_config.model,
        max_turns=max_turns,
        cli_path=agent_config.cli_path,
        conversation_id=conversation_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
    )


def save_conversation(
    event: PullRequestEvent,
    result: AgentResult,
    agent_config: AgentConfig,
    conversation_store: ConversationStore | None,
    namespace: str = "",
) -> None:
    """
    Save conversation state to the store after invoking the agent.

    Persists the conversation ID for both CLI and API agents. For API
    agents, also persists the full message history on success.

    Args:
        event (PullRequestEvent): The event with platform/repo/PR context.
        result (AgentResult): The agent execution result.
        agent_config (AgentConfig): The agent configuration.
        conversation_store (ConversationStore | None): Conversation store.
        namespace (str): Logical namespace for key isolation.
    """

    if conversation_store is None:
        return

    if result.conversation_id:
        conversation_store.set_conversation_id(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            value=result.conversation_id,
            namespace=namespace,
        )

    if (
        isinstance(agent_config, ApiAgentConfig)
        and not result.is_error
        and result.messages
    ):
        conversation_store.set_messages(
            platform=event.platform,
            repo=event.repo_full_name,
            pr_number=event.pr_number,
            value=list(result.messages),
            namespace=namespace,
        )
