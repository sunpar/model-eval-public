"""add metric adapter configs

Revision ID: b1c2d3e4f521
Revises: a8c1d2e3f401
Create Date: 2026-05-21 17:50:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f521"
down_revision: str | Sequence[str] | None = "a8c1d2e3f401"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_adapter_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("adapter_kind", sa.String(length=120), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("required_inputs", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("capability_metadata", sa.JSON(), nullable=False),
        sa.Column("local_only", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_metric_adapter_configs_project_slug_version",
        ),
    )
    op.create_index(
        "ix_metric_adapter_configs_project_kind",
        "metric_adapter_configs",
        ["project_id", "adapter_kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_adapter_configs_project_kind",
        table_name="metric_adapter_configs",
    )
    op.drop_table("metric_adapter_configs")
