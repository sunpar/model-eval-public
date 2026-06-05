"""add benchmark suites

Revision ID: a7c9d1e2f317
Revises: f6a7b8c91216
Create Date: 2026-05-21 07:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a7c9d1e2f317"
down_revision: str | Sequence[str] | None = "f6a7b8c91216"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cases",
        sa.Column("dataset_split", sa.String(length=40), nullable=False, server_default="dev"),
    )
    op.create_table(
        "benchmark_suites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("controls_json", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_benchmark_suites_project_slug_version",
        ),
    )
    op.create_table(
        "benchmark_suite_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=40), nullable=False),
        sa.Column("item_slug", sa.String(length=160), nullable=False),
        sa.Column("item_version", sa.Integer(), nullable=False),
        sa.Column("item_split", sa.String(length=40), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["suite_id"], ["benchmark_suites.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "suite_id",
            "item_type",
            "item_slug",
            "item_version",
            name="uq_benchmark_suite_items_membership",
        ),
    )


def downgrade() -> None:
    op.drop_table("benchmark_suite_items")
    op.drop_table("benchmark_suites")
    op.drop_column("cases", "dataset_split")
