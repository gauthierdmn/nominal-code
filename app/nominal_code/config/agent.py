from __future__ import annotations

from environs import Env
from pydantic import BaseModel, ConfigDict

from nominal_code.models import ProviderName

_env: Env = Env()
DEFAULT_AGENT_MAX_TURNS: int = 0


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
        max_turns (int): Maximum agentic turns (0 for unlimited).
        cli_path (str): Path to the Claude Code CLI binary.
    """

    model_config = ConfigDict(frozen=True)

    model: str = ""
    max_turns: int = 0
    cli_path: str = ""


class ApiAgentConfig(BaseModel):
    """
    Agent configuration for API-based modes (CI, webhook, CLI).

    Calls the LLM provider API directly. Requires a provider API key.

    Attributes:
        provider (ProviderConfig): The LLM provider configuration.
        max_turns (int): Maximum agentic turns (0 for unlimited).
    """

    model_config = ConfigDict(frozen=True)

    provider: ProviderConfig
    max_turns: int = 0


AgentConfig = CliAgentConfig | ApiAgentConfig


def resolve_provider_config(default: str = "") -> ProviderConfig:
    """
    Resolve the LLM provider from ``AGENT_PROVIDER`` or a caller-supplied default.

    Args:
        default (str): Fallback provider name when ``AGENT_PROVIDER`` is unset.

    Returns:
        ProviderConfig: The resolved provider configuration.

    Raises:
        ValueError: If the provider name is not recognised.
    """

    from nominal_code.llm.registry import PROVIDERS

    provider_env: str = _env.str("AGENT_PROVIDER", default)

    try:
        provider_name: ProviderName = ProviderName(provider_env)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown AGENT_PROVIDER: {provider_env!r}. Available: {available}",
        ) from None

    return PROVIDERS[provider_name]


def parse_provider_env() -> ProviderName | None:
    """
    Read ``AGENT_PROVIDER`` from the environment and convert to enum.

    Returns:
        ProviderName | None: The parsed provider, or ``None`` when unset.

    Raises:
        ValueError: If the value is not a recognised provider name.
    """

    provider: str = _env.str("AGENT_PROVIDER", "")

    if not provider:
        return None

    try:
        return ProviderName(provider)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown AGENT_PROVIDER: {provider!r}. Available: {available}",
        ) from None


def resolve_agent_config(
    provider_name: ProviderName | None,
    model: str,
    max_turns: int,
    cli_path: str = "",
) -> AgentConfig:
    """
    Build either a CLI or API agent config based on provider selection.

    Args:
        provider_name (ProviderName | None): Provider enum, or ``None``
            for CLI mode.
        model (str): Optional model override.
        max_turns (int): Maximum agentic turns.
        cli_path (str): Path to CLI binary (only used for CLI mode).

    Returns:
        AgentConfig: Either ``CliAgentConfig`` or ``ApiAgentConfig``.
    """

    if provider_name is None:
        return CliAgentConfig(
            model=model,
            max_turns=max_turns,
            cli_path=cli_path,
        )

    from nominal_code.llm.registry import PROVIDERS

    provider_config: ProviderConfig = PROVIDERS[provider_name]

    if model:
        provider_config = provider_config.model_copy(update={"model": model})

    return ApiAgentConfig(
        provider=provider_config,
        max_turns=max_turns,
    )


def read_agent_env() -> tuple[str, int]:
    """
    Read agent model and max turns from environment variables.

    Returns:
        tuple[str, int]: A ``(model, max_turns)`` tuple.
    """

    model: str = _env.str("AGENT_MODEL", "")
    max_turns: int = _env.int("AGENT_MAX_TURNS", 0)

    return model, max_turns
