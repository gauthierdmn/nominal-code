from __future__ import annotations

import logging
from pathlib import Path

from nominal_code.agent.api.runner import run_agent_api
from nominal_code.agent.cli.runner import run_agent_cli
from nominal_code.agent.providers.registry import create_provider
from nominal_code.agent.result import AgentResult
from nominal_code.config import AgentConfig, ApiAgentConfig, CliAgentConfig

logger: logging.Logger = logging.getLogger(__name__)


async def run_agent(
    prompt: str,
    cwd: Path,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    agent_config: AgentConfig | None = None,
    session_id: str = "",
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
        session_id (str): Optional session ID to resume (CLI mode only).

    Returns:
        AgentResult: The parsed result from the agent.
    """

    if agent_config is None:
        agent_config = CliAgentConfig()

    if isinstance(agent_config, ApiAgentConfig):
        provider = create_provider(agent_config.provider.name)

        return await run_agent_api(
            prompt=prompt,
            cwd=cwd,
            model=agent_config.provider.model,
            provider=provider,
            max_turns=agent_config.max_turns,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
        )

    return await run_agent_cli(
        prompt=prompt,
        cwd=cwd,
        model=agent_config.model,
        max_turns=agent_config.max_turns,
        cli_path=agent_config.cli_path,
        session_id=session_id,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
    )
