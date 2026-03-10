# type: ignore
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from nominal_code.llm.cost import (
    CostSummary,
    _get_pricing,
    build_cost_summary,
    compute_cost,
)
from nominal_code.llm.messages import ModelPricing, TokenUsage
from nominal_code.llm.registry import PROVIDERS
from nominal_code.models import ProviderName

pytest.importorskip("anthropic")


class TestComputeCost:
    def test_known_model(self):
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        cost = compute_cost(usage=usage, model="gpt-4.1")

        assert cost is not None
        assert cost == pytest.approx(2.00 + 8.00)

    def test_unknown_model(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)

        cost = compute_cost(usage=usage, model="nonexistent-model-xyz")

        assert cost is None

    def test_with_anthropic_cache_tokens(self):
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_creation_input_tokens=1_000_000,
            cache_read_input_tokens=1_000_000,
        )

        cost = compute_cost(usage=usage, model="claude-sonnet-4-20250514")

        assert cost is not None
        expected = 3.00 + 15.00 + 3.75 + 0.30
        assert cost == pytest.approx(expected)

    def test_base_usage_ignores_cache_pricing(self):
        usage = TokenUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        cost = compute_cost(usage=usage, model="claude-sonnet-4-20250514")

        assert cost is not None
        expected = 3.00 + 15.00
        assert cost == pytest.approx(expected)

    def test_zero_usage(self):
        usage = TokenUsage()

        cost = compute_cost(usage=usage, model="gpt-4.1")

        assert cost == 0.0


class TestBuildCostSummary:
    def test_no_usage(self):
        result = build_cost_summary(
            usage=None,
            model="gpt-4.1",
            provider=ProviderName.OPENAI,
            num_api_calls=3,
        )

        assert isinstance(result, CostSummary)
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_cache_creation_tokens == 0
        assert result.total_cache_read_tokens == 0
        assert result.total_cost_usd is None
        assert result.provider == ProviderName.OPENAI
        assert result.model == "gpt-4.1"
        assert result.num_api_calls == 3

    def test_with_base_usage(self):
        usage = TokenUsage(input_tokens=500, output_tokens=200)

        result = build_cost_summary(
            usage=usage,
            model="gpt-4.1",
            provider=ProviderName.OPENAI,
            num_api_calls=1,
        )

        assert result.total_input_tokens == 500
        assert result.total_output_tokens == 200
        assert result.total_cache_creation_tokens == 0
        assert result.total_cache_read_tokens == 0
        assert result.total_cost_usd is not None
        assert result.total_cost_usd > 0

    def test_with_anthropic_usage(self):
        usage = TokenUsage(
            input_tokens=500,
            output_tokens=200,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=50,
        )

        result = build_cost_summary(
            usage=usage,
            model="claude-sonnet-4-20250514",
            provider=ProviderName.ANTHROPIC,
            num_api_calls=1,
        )

        assert result.total_cache_creation_tokens == 100
        assert result.total_cache_read_tokens == 50
        assert result.total_cost_usd is not None


class TestGetPricing:
    def test_loads_from_bundled_file(self):
        _get_pricing.cache_clear()
        result = _get_pricing()

        assert len(result) > 0
        assert all(isinstance(value, ModelPricing) for value in result.values())

    def test_returns_empty_on_missing_file(self):
        _get_pricing.cache_clear()

        with patch(
            "nominal_code.llm.cost.PRICING_PATH",
            Path("/nonexistent/pricing.json"),
        ):
            result = _get_pricing()

        _get_pricing.cache_clear()

        assert result == {}

    def test_parses_cache_fields(self):
        _get_pricing.cache_clear()
        pricing_data = {
            "test-model": {
                "input_per_token": 0.001,
                "output_per_token": 0.002,
                "cache_write_per_token": 0.003,
                "cache_read_per_token": 0.004,
            },
        }

        with patch(
            "nominal_code.llm.cost.PRICING_PATH",
        ) as mock_path:
            mock_path.read_text.return_value = json.dumps(pricing_data)
            result = _get_pricing()

        _get_pricing.cache_clear()

        assert "test-model" in result
        assert result["test-model"].cache_write_per_token == 0.003
        assert result["test-model"].cache_read_per_token == 0.004

    def test_defaults_cache_fields_to_zero(self):
        _get_pricing.cache_clear()
        pricing_data = {
            "test-model": {
                "input_per_token": 0.001,
                "output_per_token": 0.002,
            },
        }

        with patch(
            "nominal_code.llm.cost.PRICING_PATH",
        ) as mock_path:
            mock_path.read_text.return_value = json.dumps(pricing_data)
            result = _get_pricing()

        _get_pricing.cache_clear()

        assert result["test-model"].cache_write_per_token == 0.0
        assert result["test-model"].cache_read_per_token == 0.0


class TestAllRegistryModelsHavePricing:
    def test_all_default_models_covered(self):
        pricing = _get_pricing()
        missing = []

        for provider_name, config in PROVIDERS.items():
            if config.model not in pricing:
                missing.append(f"{provider_name.value}: {config.model}")

        assert missing == [], f"Models missing from pricing: {missing}"
