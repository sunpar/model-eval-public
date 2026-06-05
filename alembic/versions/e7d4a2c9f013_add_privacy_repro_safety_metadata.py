"""add privacy reproducibility safety metadata

Revision ID: e7d4a2c9f013
Revises: a3f5c2d8b901
Create Date: 2026-05-20 17:45:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7d4a2c9f013"
down_revision: str | Sequence[str] | None = "a3f5c2d8b901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "provider_allow_list",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "provider_deny_list",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "data_egress_label",
            sa.String(length=80),
            nullable=False,
            server_default="local_only",
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "context_report",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "truncation_policy",
            sa.String(length=80),
            nullable=False,
            server_default="fail_on_over_budget",
        ),
    )
    op.add_column("run_attempts", sa.Column("provider", sa.String(length=80)))
    op.add_column("run_attempts", sa.Column("model", sa.String(length=255)))
    op.add_column("run_attempts", sa.Column("provider_timestamp", sa.DateTime(timezone=True)))
    op.add_column(
        "run_attempts",
        sa.Column(
            "pricing_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "run_attempts",
        sa.Column(
            "provider_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column("run_attempts", sa.Column("system_fingerprint", sa.String(length=255)))
    op.add_column(
        "provider_call_cache",
        sa.Column(
            "provider_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "provider_call_cache", sa.Column("system_fingerprint", sa.String(length=255))
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer()),
        sa.Column("experiment_id", sa.Integer()),
        sa.Column("run_id", sa.Integer()),
        sa.Column("run_attempt_id", sa.Integer()),
        sa.Column("event_kind", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=160)),
        sa.Column("actor", sa.String(length=160)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_attempt_id"], ["run_attempts.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_column("provider_call_cache", "system_fingerprint")
    op.drop_column("provider_call_cache", "provider_metadata")
    op.drop_column("run_attempts", "system_fingerprint")
    op.drop_column("run_attempts", "provider_metadata")
    op.drop_column("run_attempts", "pricing_snapshot")
    op.drop_column("run_attempts", "provider_timestamp")
    op.drop_column("run_attempts", "model")
    op.drop_column("run_attempts", "provider")
    op.drop_column("runs", "truncation_policy")
    op.drop_column("runs", "context_report")
    op.drop_column("runs", "data_egress_label")
    op.drop_column("projects", "provider_deny_list")
    op.drop_column("projects", "provider_allow_list")
