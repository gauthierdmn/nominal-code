# type: ignore
from unittest.mock import patch

import pytest

from nominal_code.llm.registry import (
    DEFAULT_MODELS,
    PROVIDERS,
    create_provider,
)
from nominal_code.models import ProviderName


class TestProviders:
    def test_all_provider_names_have_config(self):
        for provider_name in ProviderName:
            assert provider_name in PROVIDERS

    def test_default_models_match_provider_configs(self):
        for provider_name, config in PROVIDERS.items():
            assert DEFAULT_MODELS[provider_name] == config.model


class TestCreateProvider:
    def test_raises_on_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider(name="nonexistent")

    def test_creates_anthropic_provider(self):
        pytest.importorskip("anthropic")
        from nominal_code.llm.anthropic import AnthropicProvider

        provider = create_provider(name="anthropic")

        assert isinstance(provider, AnthropicProvider)

    def test_creates_openai_provider(self):
        pytest.importorskip("openai")
        from nominal_code.llm.openai import OpenAIProvider

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            provider = create_provider(name="openai")

        assert isinstance(provider, OpenAIProvider)

    def test_creates_google_provider(self):
        pytest.importorskip("google.genai")
        from nominal_code.llm.google import GoogleProvider

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            provider = create_provider(name="google")

        assert isinstance(provider, GoogleProvider)

    def test_creates_deepseek_via_openai_provider(self):
        pytest.importorskip("openai")
        from nominal_code.llm.openai import OpenAIProvider

        with patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "test-key"},
        ):
            provider = create_provider(name="deepseek")

        assert isinstance(provider, OpenAIProvider)

    def test_openai_compatible_uses_base_url(self):
        pytest.importorskip("openai")
        from nominal_code.llm.openai import OpenAIProvider

        with patch.dict(
            "os.environ",
            {"GROQ_API_KEY": "test-key"},
        ):
            provider = create_provider(name="groq")

        assert isinstance(provider, OpenAIProvider)

    def test_error_message_lists_available_providers(self):
        with pytest.raises(ValueError) as exc_info:
            create_provider(name="invalid")

        error_message = str(exc_info.value)

        for provider_name in ProviderName:
            assert provider_name.value in error_message
