from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from nominal_code.llm.messages import ModelPricing, TokenUsage
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


def format_cost_summary(cost: CostSummary | None) -> str:
    """
    Format a cost summary for log output.

    Args:
        cost (CostSummary | None): The cost summary, or ``None`` if
            cost tracking was not available.

    Returns:
        str: Formatted multi-line string prefixed with a newline,
            or empty string if no cost data is available.
    """

    if cost is None:
        return ""

    parts: list[str] = []

    if cost.model:
        parts.append(f"  Model: {cost.model} ({cost.provider})")

    tokens_in: int = cost.total_input_tokens
    tokens_out: int = cost.total_output_tokens
    tokens_line: str = f"  Tokens: {tokens_in:,} in / {tokens_out:,} out"

    extras: list[str] = []

    if cost.total_cache_creation_tokens > 0:
        extras.append(f"cache write: {cost.total_cache_creation_tokens:,}")

    if cost.total_cache_read_tokens > 0:
        extras.append(f"cache read: {cost.total_cache_read_tokens:,}")

    if extras:
        tokens_line += f" ({', '.join(extras)})"

    parts.append(tokens_line)

    if cost.total_cost_usd is not None:
        parts.append(f"  Cost: ${cost.total_cost_usd:.4f}")

    if cost.num_api_calls > 0:
        parts.append(f"  API calls: {cost.num_api_calls}")

    return "\n" + "\n".join(parts)


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
        total_cost_usd=compute_cost(usage=usage, model=model),
        provider=provider,
        model=model,
        num_api_calls=num_api_calls,
    )


def aggregate_cost_summary(
    *,
    reviewer: CostSummary | None,
    sub_agents: tuple[CostSummary, ...],
) -> CostSummary | None:
    """
    Sum a reviewer cost with sub-agent costs into one CostSummary.

    ``total_cost_usd`` is preserved as ``None`` only when no input has a
    cost; otherwise it sums the available values (missing prices are
    treated as 0 rather than poisoning the total).

    Args:
        reviewer (CostSummary | None): Reviewer-step cost.
        sub_agents (tuple[CostSummary, ...]): Sub-agent step costs.

    Returns:
        CostSummary | None: Aggregated cost, or ``None`` if both inputs
            are empty.
    """

    if reviewer is None and not sub_agents:
        return None

    base: CostSummary = reviewer if reviewer is not None else sub_agents[0]
    others: tuple[CostSummary, ...] = (
        sub_agents if reviewer is not None else sub_agents[1:]
    )

    non_none_costs: list[float] = [
        component.total_cost_usd
        for component in (base, *others)
        if component.total_cost_usd is not None
    ]
    total_cost_usd: float | None = sum(non_none_costs) if non_none_costs else None

    return CostSummary(
        total_input_tokens=base.total_input_tokens
        + sum(component.total_input_tokens for component in others),
        total_output_tokens=base.total_output_tokens
        + sum(component.total_output_tokens for component in others),
        total_cache_creation_tokens=base.total_cache_creation_tokens
        + sum(component.total_cache_creation_tokens for component in others),
        total_cache_read_tokens=base.total_cache_read_tokens
        + sum(component.total_cache_read_tokens for component in others),
        total_cost_usd=total_cost_usd,
        provider=base.provider,
        model=base.model,
        num_api_calls=base.num_api_calls
        + sum(component.num_api_calls for component in others),
    )
