# type: ignore
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from update_pricing import _to_pricing_entry, build_pricing


class TestToPricingEntry:
    def test_base_pricing(self):
        entry = {
            "input_cost_per_token": 0.003,
            "output_cost_per_token": 0.015,
        }

        result = _to_pricing_entry(entry)

        assert result["input_per_token"] == 0.003
        assert result["output_per_token"] == 0.015
        assert "cache_write_per_token" not in result
        assert "cache_read_per_token" not in result

    def test_with_cache_pricing(self):
        entry = {
            "input_cost_per_token": 0.003,
            "output_cost_per_token": 0.015,
            "cache_creation_input_token_cost": 0.00375,
            "cache_read_input_token_cost": 0.0003,
        }

        result = _to_pricing_entry(entry)

        assert result["cache_write_per_token"] == 0.00375
        assert result["cache_read_per_token"] == 0.0003

    def test_missing_fields_default_to_zero(self):
        entry = {}

        result = _to_pricing_entry(entry)

        assert result["input_per_token"] == 0.0
        assert result["output_per_token"] == 0.0

    def test_null_cache_fields_treated_as_zero(self):
        entry = {
            "input_cost_per_token": 0.003,
            "output_cost_per_token": 0.015,
            "cache_creation_input_token_cost": None,
            "cache_read_input_token_cost": None,
        }

        result = _to_pricing_entry(entry)

        assert "cache_write_per_token" not in result


class TestBuildPricing:
    def test_filters_by_supported_provider(self):
        litellm_data = {
            "gpt-4": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.03,
                "output_cost_per_token": 0.06,
            },
            "unsupported-model": {
                "litellm_provider": "some_unknown_provider",
                "mode": "chat",
                "input_cost_per_token": 0.01,
                "output_cost_per_token": 0.02,
            },
        }

        result = build_pricing(litellm_data)

        assert "gpt-4" in result
        assert "unsupported-model" not in result

    def test_filters_to_chat_mode_only(self):
        litellm_data = {
            "gpt-4": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.03,
                "output_cost_per_token": 0.06,
            },
            "text-embedding-3": {
                "litellm_provider": "openai",
                "mode": "embedding",
                "input_cost_per_token": 0.001,
                "output_cost_per_token": 0.0,
            },
        }

        result = build_pricing(litellm_data)

        assert "gpt-4" in result
        assert "text-embedding-3" not in result

    def test_strips_provider_prefix(self):
        litellm_data = {
            "groq/llama-3": {
                "litellm_provider": "groq",
                "mode": "chat",
                "input_cost_per_token": 0.001,
                "output_cost_per_token": 0.002,
            },
        }

        result = build_pricing(litellm_data)

        assert "llama-3" in result
        assert "groq/llama-3" not in result

    def test_no_prefix_strip_for_native_providers(self):
        litellm_data = {
            "claude-3-opus": {
                "litellm_provider": "anthropic",
                "mode": "chat",
                "input_cost_per_token": 0.015,
                "output_cost_per_token": 0.075,
            },
        }

        result = build_pricing(litellm_data)

        assert "claude-3-opus" in result

    def test_empty_input(self):
        result = build_pricing({})

        assert result == {}
