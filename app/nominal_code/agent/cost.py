from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from nominal_code.agent.providers.types import ModelPricing, TokenUsage
from nominal_code.models import ProviderName

logger: logging.Logger = logging.getLogger(__name__)

PRICING_PATH: Path = Path(__file__).parent / "data" / "pricing.json"


@lru_cache(maxsize=1)
def _get_pricing() -> dict[str, ModelPricing]:
    """
    Load model pricing from the bundled JSON file.

    Results are cached so the file is read at most once.

    Returns:
        dict[str, ModelPricing]: Model ID to pricing mapping.
    """

    try:
        pricing_data: str = PRICING_PATH.read_text()
    except FileNotFoundError:
        logger.warning("Pricing file not found: %s", PRICING_PATH)

        return {}

    data: dict[str, Any] = json.loads(pricing_data)
    result: dict[str, ModelPricing] = {}

    for model_id, entry in data.items():
        result[model_id] = ModelPricing(
            input_per_token=entry["input_per_token"],
            output_per_token=entry["output_per_token"],
            cache_write_per_token=entry.get("cache_write_per_token", 0.0),
            cache_read_per_token=entry.get("cache_read_per_token", 0.0),
        )

    return result


@dataclass(frozen=True)
class CostSummary:
    """
    Accumulated cost information for an agent invocation.

    Attributes:
        total_input_tokens (int): Total input tokens across all API calls.
        total_output_tokens (int): Total output tokens across all API calls.
        total_cache_creation_tokens (int): Total cache write tokens
            (0 for providers without caching).
        total_cache_read_tokens (int): Total cache read tokens
            (0 for providers without caching).
        total_cost_usd (float | None): Total cost in USD, or None if pricing
            is unavailable for the model.
        provider (ProviderName): Provider name.
        model (str): Model identifier.
        num_api_calls (int): Number of API calls made.
    """

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float | None = None
    provider: ProviderName = ProviderName.ANTHROPIC
    model: str = ""
    num_api_calls: int = 0


def compute_cost(usage: TokenUsage, model: str) -> float | None:
    """
    Compute the dollar cost for the given token usage.

    Delegates to ``TokenUsage.compute_cost`` which handles
    provider-specific token categories via polymorphism.

    Args:
        usage (TokenUsage): Token counts (single turn or accumulated).
        model (str): Model identifier to look up in the pricing table.

    Returns:
        float | None: Cost in USD, or None if the model is not in the
            pricing table.
    """

    pricing: ModelPricing | None = _get_pricing().get(model)

    if pricing is None:
        return None

    return usage.compute_cost(pricing)


def build_cost_summary(
    usage: TokenUsage | None,
    model: str,
    provider: ProviderName,
    num_api_calls: int,
) -> CostSummary:
    """
    Build a CostSummary from accumulated token usage.

    Args:
        usage (TokenUsage | None): Accumulated token usage, or None if no
            usage was tracked.
        model (str): Model identifier.
        provider (ProviderName): Provider name.
        num_api_calls (int): Number of API calls made.

    Returns:
        CostSummary: The computed cost summary.
    """

    if usage is None:
        return CostSummary(
            provider=provider,
            model=model,
            num_api_calls=num_api_calls,
        )

    return CostSummary(
        total_input_tokens=usage.input_tokens,
        total_output_tokens=usage.output_tokens,
        total_cache_creation_tokens=usage.cache_creation_input_tokens,
        total_cache_read_tokens=usage.cache_read_input_tokens,
        total_cost_usd=compute_cost(usage, model),
        provider=provider,
        model=model,
        num_api_calls=num_api_calls,
    )
