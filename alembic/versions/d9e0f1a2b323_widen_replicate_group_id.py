"""widen replicate group id

Revision ID: d9e0f1a2b323
Revises: c2d4e6f8a918
Create Date: 2026-05-21 12:45:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d9e0f1a2b323"
down_revision: str | Sequence[str] | None = "c2d4e6f8a918"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run_attempts") as batch_op:
        batch_op.alter_column(
            "replicate_group_id",
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("run_attempts") as batch_op:
        batch_op.alter_column(
            "replicate_group_id",
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
