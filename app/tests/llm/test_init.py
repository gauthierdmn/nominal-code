# type: ignore
import builtins
import importlib
import sys
from unittest.mock import patch

import pytest

from nominal_code.llm.provider import MissingProviderError
from nominal_code.llm.registry import DEFAULT_MODELS, create_provider


def _spec_exists(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


_has_anthropic = _spec_exists("anthropic")
_has_openai = _spec_exists("openai")
_has_google = _spec_exists("google.genai")


class TestCreateProvider:
    @pytest.mark.skipif(not _has_anthropic, reason="anthropic SDK not installed")
    def test_create_anthropic_provider(self):
        from nominal_code.llm.anthropic import AnthropicProvider

        provider = create_provider("anthropic")

        assert isinstance(provider, AnthropicProvider)

    @pytest.mark.skipif(not _has_openai, reason="openai SDK not installed")
    def test_create_openai_provider(self):
        from nominal_code.llm.openai import (
            OpenAIProvider,
        )

        provider = create_provider("openai", api_key="test-key")

        assert isinstance(provider, OpenAIProvider)

    @pytest.mark.skipif(not _has_google, reason="google-genai SDK not installed")
    def test_create_google_provider(self):
        from nominal_code.llm.google import GoogleProvider

        with patch("google.genai.Client"):
            provider = create_provider("google")

        assert isinstance(provider, GoogleProvider)

    def test_create_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider("nonexistent")

    def test_default_models_has_anthropic(self):
        assert "anthropic" in DEFAULT_MODELS
        assert "claude" in DEFAULT_MODELS["anthropic"]

    def test_default_models_has_openai(self):
        assert "openai" in DEFAULT_MODELS
        assert "gpt" in DEFAULT_MODELS["openai"]

    def test_default_models_has_google(self):
        assert "google" in DEFAULT_MODELS
        assert "gemini" in DEFAULT_MODELS["google"]

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

    def test_create_google_raises_when_sdk_missing(self):
        real_import = builtins.__import__

        def _block_google(name, *args, **kwargs):
            if name == "google.genai" or name == "google":
                raise ImportError("No module named 'google.genai'")
            return real_import(name, *args, **kwargs)

        cached_modules = {
            key: sys.modules.pop(key)
            for key in list(sys.modules)
            if key == "google" or key.startswith("google.")
        }

        try:
            with patch("builtins.__import__", side_effect=_block_google):
                with pytest.raises(MissingProviderError, match="google"):
                    create_provider("google")
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
