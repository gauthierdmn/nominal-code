"""
Fetch model pricing from LiteLLM and write a local JSON file.

Usage:
    python scripts/update_pricing.py

The script downloads the LiteLLM community pricing database, extracts
pricing for all chat models from providers supported by nominal-code,
and writes the result to ``app/nominal_code/llm/data/pricing.json``.

Providers that use a LiteLLM key prefix (e.g. ``groq/``,
``together_ai/``) have the prefix stripped so the output keys match
the model IDs used by nominal-code at runtime.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any

LITELLM_URL: str = (
    "https://raw.githubusercontent.com/BerriAI/litellm"
    "/main/model_prices_and_context_window.json"
)

OUTPUT_PATH: Path = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "nominal_code"
    / "llm"
    / "data"
    / "pricing.json"
)

PROVIDER_PREFIXES: dict[str, str] = {
    "anthropic": "",
    "openai": "",
    # Google Gemini models are listed under vertex_ai and gemini in LiteLLM
    "vertex_ai-language-models": "",
    "gemini": "",
    "deepseek": "",
    "groq": "groq/",
    "together_ai": "together_ai/",
    "fireworks_ai": "fireworks_ai/",
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger: logging.Logger = logging.getLogger(__name__)


def fetch_litellm_pricing() -> dict[str, Any]:
    """
    Download the LiteLLM pricing database.

    Returns:
        dict[str, Any]: The full LiteLLM pricing data.
    """

    logger.info("Fetching pricing from %s", LITELLM_URL)

    with urllib.request.urlopen(LITELLM_URL, timeout=30) as response:
        raw: bytes = response.read()

    return json.loads(raw)


def _to_pricing_entry(entry: dict[str, Any]) -> dict[str, float]:
    """
    Convert a LiteLLM entry to our pricing format.

    Args:
        entry (dict[str, Any]): A single LiteLLM model entry.

    Returns:
        dict[str, float]: Pricing dict with per-token rates.
    """

    pricing: dict[str, float] = {
        "input_per_token": entry.get("input_cost_per_token", 0.0),
        "output_per_token": entry.get("output_cost_per_token", 0.0),
    }

    cache_write: float = entry.get("cache_creation_input_token_cost", 0.0) or 0.0
    cache_read: float = entry.get("cache_read_input_token_cost", 0.0) or 0.0

    if cache_write > 0 or cache_read > 0:
        pricing["cache_write_per_token"] = cache_write
        pricing["cache_read_per_token"] = cache_read

    return pricing


def build_pricing(
    litellm_data: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """
    Extract pricing for all chat models from supported providers.

    Iterates over the full LiteLLM database, filters to chat models
    from providers in ``PROVIDER_PREFIXES``, strips provider prefixes,
    and builds the output dict.

    Args:
        litellm_data (dict[str, Any]): The full LiteLLM pricing data.

    Returns:
        dict[str, dict[str, float]]: Model ID to pricing dict mapping.
    """

    result: dict[str, dict[str, float]] = {}
    provider_counts: dict[str, int] = {}

    for litellm_key, entry in litellm_data.items():
        litellm_provider: str = entry.get("litellm_provider", "")

        if litellm_provider not in PROVIDER_PREFIXES:
            continue

        if entry.get("mode") != "chat":
            continue

        prefix: str = PROVIDER_PREFIXES[litellm_provider]

        if prefix and litellm_key.startswith(prefix):
            model_id: str = litellm_key[len(prefix) :]
        else:
            model_id = litellm_key

        result[model_id] = _to_pricing_entry(entry)
        provider_counts[litellm_provider] = (
            provider_counts.get(litellm_provider, 0) + 1
        )

    for provider, count in sorted(provider_counts.items()):
        logger.info("  %s: %d models", provider, count)

    return result


def main() -> None:
    """
    Fetch pricing and write the output file.
    """

    litellm_data: dict[str, Any] = fetch_litellm_pricing()
    pricing: dict[str, dict[str, float]] = build_pricing(litellm_data)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_PATH, "w") as output_file:
        json.dump(pricing, output_file, indent=2)
        output_file.write("\n")

    logger.info("Wrote %d models to %s", len(pricing), OUTPUT_PATH)


if __name__ == "__main__":
    main()
