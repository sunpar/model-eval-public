"""add experiment pricing snapshot

Revision ID: c8a9f2d7b6e1
Revises: bdb42fbb358a
Create Date: 2026-05-20 06:26:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8a9f2d7b6e1"
down_revision: str | Sequence[str] | None = "bdb42fbb358a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "pricing_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("experiments", "pricing_snapshot")
