# type: ignore
import builtins
import importlib
import sys
from unittest.mock import patch

import pytest

from nominal_code.agent.providers.base import MissingProviderError
from nominal_code.agent.providers.registry import DEFAULT_MODELS, create_provider

_has_anthropic = importlib.util.find_spec("anthropic") is not None
_has_openai = importlib.util.find_spec("openai") is not None


class TestCreateProvider:
    @pytest.mark.skipif(not _has_anthropic, reason="anthropic SDK not installed")
    def test_create_anthropic_provider(self):
        from nominal_code.agent.providers.anthropic import AnthropicProvider

        provider = create_provider("anthropic")

        assert isinstance(provider, AnthropicProvider)

    @pytest.mark.skipif(not _has_openai, reason="openai SDK not installed")
    def test_create_openai_provider(self):
        from nominal_code.agent.providers.openai import (
            OpenAIProvider,
        )

        provider = create_provider("openai", api_key="test-key")

        assert isinstance(provider, OpenAIProvider)

    def test_create_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider("nonexistent")

    def test_default_models_has_anthropic(self):
        assert "anthropic" in DEFAULT_MODELS
        assert "claude" in DEFAULT_MODELS["anthropic"]

    def test_default_models_has_openai(self):
        assert "openai" in DEFAULT_MODELS
        assert "gpt" in DEFAULT_MODELS["openai"]

    def test_create_anthropic_raises_when_sdk_missing(self):
        real_import = builtins.__import__

        def _block_anthropic(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        cached_modules = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "anthropic" or key.startswith("anthropic.")
        }

        try:
            with patch("builtins.__import__", side_effect=_block_anthropic):
                with pytest.raises(MissingProviderError, match="anthropic"):
                    create_provider("anthropic")
        finally:
            sys.modules.update(cached_modules)

    def test_create_openai_raises_when_sdk_missing(self):
        real_import = builtins.__import__

        def _block_openai(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        cached_modules = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "openai" or key.startswith("openai.")
        }

        try:
            with patch("builtins.__import__", side_effect=_block_openai):
                with pytest.raises(MissingProviderError, match="openai"):
                    create_provider("openai")
        finally:
            sys.modules.update(cached_modules)
