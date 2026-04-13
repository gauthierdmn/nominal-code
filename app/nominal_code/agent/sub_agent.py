from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

DEFAULT_MAX_TURNS_PER_SUB_AGENT: int = 32

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
    max_turns: int = DEFAULT_MAX_TURNS_PER_SUB_AGENT
    allowed_tools: list[str] = field(default_factory=list)
    description: str = ""
