from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nominal_code.models import ProviderName

REVIEWER_DEFAULT_MAX_TURNS: int = 8
EXPLORER_DEFAULT_MAX_TURNS: int = 32


class AgentRoleConfig(BaseModel):
    """
    Per-role runtime configuration for one agent in the review pipeline.

    Applies symmetrically to the reviewer and explorer roles. Each role
    carries both LLM selection (provider, model, base_url) and runtime
    behavior (system prompt, max turns).

    Attributes:
        name (ProviderName): Provider identifier.
        model (str): Model name (e.g. ``"claude-sonnet-4-5-20250929"``).
        base_url (str | None): Base URL for OpenAI-compatible providers.
            ``None`` for native providers and OpenAI itself (uses SDK default).
        system_prompt (str): Resolved system prompt text for this role.
            Populated by the config loader; empty in the ``PROVIDERS``
            catalog defaults and set per-role via ``model_copy``.
        max_turns (int): Maximum agentic turns for this role.
    """

    model_config = ConfigDict(frozen=True)

    name: ProviderName
    model: str
    base_url: str | None = None
    system_prompt: str = ""
    max_turns: int = REVIEWER_DEFAULT_MAX_TURNS

    @property
    def api_key_env(self) -> str:
        """
        Environment variable name for the provider's API key.

        Derived from the provider name: ``{NAME}_API_KEY``
        (e.g. ``"ANTHROPIC_API_KEY"``).

        Returns:
            str: The environment variable name.
        """

        return f"{self.name.upper()}_API_KEY"


class CliAgentConfig(BaseModel):
    """
    Agent configuration for CLI and webhook modes.

    Uses the Claude Code CLI subprocess.

    Attributes:
        model (str | None): Optional model override (None uses CLI default).
        cli_path (str | None): Path to the Claude Code CLI binary.
        system_prompt (str): Reviewer system prompt text. Populated by the
            config loader from ``settings.agent.reviewer.system_prompt``.
    """

    model_config = ConfigDict(frozen=True)

    model: str | None = None
    cli_path: str | None = None
    system_prompt: str = ""


class ApiAgentConfig(BaseModel):
    """
    Agent configuration for API-based modes (CI, webhook, CLI).

    Calls the LLM provider API directly. Requires a provider API key.
    The reviewer is the main agentic model that drives the review. The
    explorer is a cheaper sub-agent that the reviewer can delegate deep
    codebase investigation to via the ``Agent`` tool. Both roles share the
    same ``AgentRoleConfig`` shape.

    Attributes:
        reviewer (AgentRoleConfig): Reviewer runtime config.
        explorer (AgentRoleConfig): Explorer sub-agent runtime config.
    """

    model_config = ConfigDict(frozen=True)

    reviewer: AgentRoleConfig
    explorer: AgentRoleConfig


AgentConfig = CliAgentConfig | ApiAgentConfig
