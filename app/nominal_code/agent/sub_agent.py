from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nominal_code.config.agent import EXPLORER_DEFAULT_MAX_TURNS

if TYPE_CHECKING:
    from nominal_code.llm.provider import LLMProvider
    from nominal_code.models import ProviderName


@dataclass(frozen=True)
class SubAgentConfig:
    """
    Configuration for a sub-agent type spawnable via the Agent tool.

    Attributes:
        provider (LLMProvider): LLM provider instance for the sub-agent.
        model (str): Model identifier.
        provider_name (ProviderName): Provider name for cost tracking.
        system_prompt (str): Full system prompt for the sub-agent.
        max_turns (int): Maximum agentic turns.
        allowed_tools (list[str]): Tool names the sub-agent may use.
        description (str): Human-readable description shown in Agent
            tool schema.
    """

    provider: LLMProvider
    model: str
    provider_name: ProviderName
    system_prompt: str
    max_turns: int = EXPLORER_DEFAULT_MAX_TURNS
    allowed_tools: list[str] = field(default_factory=list)
    description: str = ""
