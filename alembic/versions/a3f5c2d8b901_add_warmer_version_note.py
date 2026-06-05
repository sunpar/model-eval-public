"""add warmer version note

Revision ID: a3f5c2d8b901
Revises: f2d8b9c4a1e5
Create Date: 2026-05-20 08:05:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a3f5c2d8b901"
down_revision: str | Sequence[str] | None = "f2d8b9c4a1e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversation_warmers", sa.Column("version_note", sa.Text()))


def downgrade() -> None:
    op.drop_column("conversation_warmers", "version_note")
