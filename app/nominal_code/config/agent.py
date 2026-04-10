from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nominal_code.models import ProviderName


class ProviderConfig(BaseModel):
    """
    LLM provider configuration.

    Attributes:
        name (ProviderName): Provider identifier.
        model (str): Model name (e.g. ``"claude-sonnet-4-20250514"``).
        base_url (str | None): Base URL for OpenAI-compatible providers.
            ``None`` for native providers and OpenAI itself (uses SDK default).
    """

    model_config = ConfigDict(frozen=True)

    name: ProviderName
    model: str
    base_url: str | None = None

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
        model (str): Optional model override (empty string uses CLI default).
        cli_path (str): Path to the Claude Code CLI binary.
    """

    model_config = ConfigDict(frozen=True)

    model: str | None = None
    cli_path: str | None = None


class ApiAgentConfig(BaseModel):
    """
    Agent configuration for API-based modes (CI, webhook, CLI).

    Calls the LLM provider API directly. Requires a provider API key.

    Attributes:
        provider (ProviderConfig): The LLM provider configuration.
    """

    model_config = ConfigDict(frozen=True)

    provider: ProviderConfig


AgentConfig = CliAgentConfig | ApiAgentConfig


def resolve_agent_config(
    provider_name: ProviderName | None,
    model: str | None,
    cli_path: str | None = None,
) -> AgentConfig:
    """
    Build either a CLI or API agent config based on provider selection.

    Args:
        provider_name (ProviderName | None): Provider enum, or ``None``
            for CLI mode.
        model (str): Optional model override.
        cli_path (str): Path to CLI binary (only used for CLI mode).

    Returns:
        AgentConfig: Either ``CliAgentConfig`` or ``ApiAgentConfig``.
    """

    if provider_name is None:
        return CliAgentConfig(
            model=model,
            cli_path=cli_path,
        )

    from nominal_code.llm.registry import PROVIDERS

    provider_config: ProviderConfig = PROVIDERS[provider_name]

    if model:
        provider_config = provider_config.model_copy(update={"model": model})

    return ApiAgentConfig(provider=provider_config)
