from __future__ import annotations

import os
from typing import Any

from nominal_code.config import ProviderConfig
from nominal_code.llm.provider import LLMProvider
from nominal_code.models import ProviderName

PROVIDERS: dict[ProviderName, ProviderConfig] = {
    ProviderName.ANTHROPIC: ProviderConfig(
        name=ProviderName.ANTHROPIC,
        model="claude-sonnet-4-20250514",
    ),
    ProviderName.OPENAI: ProviderConfig(
        name=ProviderName.OPENAI,
        model="gpt-4.1",
    ),
    ProviderName.GOOGLE: ProviderConfig(
        name=ProviderName.GOOGLE,
        model="gemini-2.5-flash",
    ),
    ProviderName.DEEPSEEK: ProviderConfig(
        name=ProviderName.DEEPSEEK,
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
    ),
    ProviderName.GROQ: ProviderConfig(
        name=ProviderName.GROQ,
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
    ),
    ProviderName.TOGETHER: ProviderConfig(
        name=ProviderName.TOGETHER,
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
    ),
    ProviderName.FIREWORKS: ProviderConfig(
        name=ProviderName.FIREWORKS,
        model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        base_url="https://api.fireworks.ai/inference/v1",
    ),
}

DEFAULT_MODELS: dict[ProviderName, str] = {
    provider_name: defaults.model for provider_name, defaults in PROVIDERS.items()
}

INSTALL_INSTRUCTIONS: dict[ProviderName, str] = {
    ProviderName.ANTHROPIC: 'pip install "nominal-code[anthropic]"',
    ProviderName.OPENAI: 'pip install "nominal-code[openai]"',
    ProviderName.GOOGLE: 'pip install "nominal-code[google]"',
}


def create_provider(name: str, **kwargs: Any) -> LLMProvider:
    """
    Create an LLM provider instance by name.

    Args:
        name (str): Provider name (e.g. ``"anthropic"``, ``"openai"``,
            ``"deepseek"``).
        **kwargs (Any): Additional keyword arguments passed to the provider
            constructor.

    Returns:
        LLMProvider: The provider instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """

    try:
        provider: ProviderName = ProviderName(name)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown provider: {name!r}. Available: {available}",
        ) from None

    if provider is ProviderName.ANTHROPIC:
        from nominal_code.llm.anthropic import AnthropicProvider

        return AnthropicProvider(**kwargs)

    if provider is ProviderName.GOOGLE:
        from nominal_code.llm.google import GoogleProvider

        return GoogleProvider(**kwargs)

    from nominal_code.llm.openai import OpenAIProvider

    provider_config = PROVIDERS[provider]
    api_key: str = (
        kwargs.pop("api_key", "")
        or os.environ.get(provider_config.api_key_env, "")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    base_url: str | None = kwargs.pop("base_url", None) or provider_config.base_url

    return OpenAIProvider(
        api_key=api_key,
        base_url=base_url,
        provider_name=provider,
        **kwargs,
    )
