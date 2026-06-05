from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from model_eval_api.providers.models import ProviderUsage


PRICING_SNAPSHOT_VERSION = "2026-05-20-mvp-provider-pricing"

DEFAULT_PRICING: dict[tuple[str, str], dict[str, Any]] = {
    ("openai", "gpt-5.5"): {
        "input_usd_per_million_tokens": 5.0,
        "output_usd_per_million_tokens": 15.0,
    },
    ("anthropic", "claude-opus"): {
        "input_usd_per_million_tokens": 15.0,
        "output_usd_per_million_tokens": 75.0,
    },
    ("anthropic", "claude-opus-4"): {
        "input_usd_per_million_tokens": 15.0,
        "output_usd_per_million_tokens": 75.0,
    },
}


def build_pricing_snapshot(models: Iterable[tuple[str, str]]) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for provider, model in models:
        key = (provider, model)
        pricing = DEFAULT_PRICING.get(key, {})
        entries[f"{provider}/{model}"] = {
            "provider": provider,
            "model": model,
            "currency": "USD",
            "unit": "1m_tokens",
            "input_usd_per_million_tokens": pricing.get("input_usd_per_million_tokens"),
            "output_usd_per_million_tokens": pricing.get("output_usd_per_million_tokens"),
            "source": "static_mvp_config",
        }
    return {
        "version": PRICING_SNAPSHOT_VERSION,
        "models": entries,
    }


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    usage: ProviderUsage,
    pricing_snapshot: dict[str, Any] | None = None,
) -> float | None:
    snapshot = pricing_snapshot or build_pricing_snapshot([(provider, model)])
    entry = snapshot.get("models", {}).get(f"{provider}/{model}", {})
    input_price = entry.get("input_usd_per_million_tokens")
    output_price = entry.get("output_usd_per_million_tokens")
    if not isinstance(input_price, (int, float)) or not isinstance(output_price, (int, float)):
        return None
    return (
        (usage.input_tokens / 1_000_000) * float(input_price)
        + (usage.output_tokens / 1_000_000) * float(output_price)
    )
