"""backfill experiment pricing snapshots

Revision ID: c6d7e8f90123
Revises: b1c2d3e4f521
Create Date: 2026-05-25 17:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f90123"
down_revision: str | Sequence[str] | None = "b1c2d3e4f521"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICING_SNAPSHOT_VERSION = "2026-05-20-mvp-provider-pricing"
DEFAULT_PRICING: dict[tuple[str, str], dict[str, float]] = {
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


def upgrade() -> None:
    bind = op.get_bind()
    experiments = sa.table(
        "experiments",
        sa.column("id", sa.Integer()),
        sa.column("model_config_snapshots", sa.JSON()),
        sa.column("pricing_snapshot", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(
            experiments.c.id,
            experiments.c.model_config_snapshots,
            experiments.c.pricing_snapshot,
        )
    ).mappings()
    for row in rows:
        current = row["pricing_snapshot"]
        if isinstance(current, dict) and current:
            continue
        snapshot = _pricing_snapshot(row["model_config_snapshots"])
        bind.execute(
            sa.update(experiments)
            .where(experiments.c.id == row["id"])
            .values(pricing_snapshot=snapshot)
        )


def downgrade() -> None:
    pass


def _pricing_snapshot(model_config_snapshots: Any) -> dict[str, Any]:
    if not isinstance(model_config_snapshots, dict):
        return _build_pricing_snapshot([])
    models = []
    for snapshot in model_config_snapshots.values():
        if not isinstance(snapshot, dict):
            continue
        provider = snapshot.get("provider")
        model = snapshot.get("model")
        if isinstance(provider, str) and isinstance(model, str):
            models.append((provider, model))
    return _build_pricing_snapshot(models)


def _build_pricing_snapshot(models: list[tuple[str, str]]) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for provider, model in models:
        pricing = DEFAULT_PRICING.get((provider, model), {})
        entries[f"{provider}/{model}"] = {
            "provider": provider,
            "model": model,
            "currency": "USD",
            "unit": "1m_tokens",
            "input_usd_per_million_tokens": pricing.get("input_usd_per_million_tokens"),
            "output_usd_per_million_tokens": pricing.get("output_usd_per_million_tokens"),
            "source": "static_mvp_config",
        }
    return {"version": PRICING_SNAPSHOT_VERSION, "models": entries}
