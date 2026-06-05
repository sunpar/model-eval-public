"""add replicate metadata

Revision ID: b8d4f1a6c219
Revises: a7c9d1e2f317
Create Date: 2026-05-21 07:55:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b8d4f1a6c219"
down_revision: str | Sequence[str] | None = "a7c9d1e2f317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_attempts",
        sa.Column("replicate_group_id", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "run_attempts",
        sa.Column("attempt_kind", sa.String(length=40), nullable=False, server_default="replicate"),
    )


def downgrade() -> None:
    op.drop_column("run_attempts", "attempt_kind")
    op.drop_column("run_attempts", "replicate_group_id")
