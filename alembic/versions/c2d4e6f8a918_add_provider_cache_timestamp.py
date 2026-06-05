"""add provider cache timestamp

Revision ID: c2d4e6f8a918
Revises: b8d4f1a6c219
Create Date: 2026-05-21 12:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c2d4e6f8a918"
down_revision: str | Sequence[str] | None = "b8d4f1a6c219"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_call_cache",
        sa.Column("provider_timestamp", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("provider_call_cache", "provider_timestamp")
