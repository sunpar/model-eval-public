"""add executor attempt metadata

Revision ID: f2d8b9c4a1e5
Revises: c8a9f2d7b6e1
Create Date: 2026-05-20 07:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f2d8b9c4a1e5"
down_revision: str | Sequence[str] | None = "c8a9f2d7b6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_attempts", sa.Column("error_kind", sa.String(length=80)))
    op.add_column("run_attempts", sa.Column("terminal_failure_reason", sa.Text()))
    op.add_column(
        "run_attempts",
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("run_attempts", sa.Column("parent_attempt_id", sa.String(length=120)))
    op.add_column(
        "run_attempts",
        sa.Column("retry_after_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("run_attempts", sa.Column("available_at", sa.DateTime(timezone=True)))
    op.add_column("run_attempts", sa.Column("cache_key", sa.String(length=128)))
    op.add_column(
        "run_attempts",
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE run_attempts SET status = 'queued' WHERE status = 'pending'")
    op.create_table(
        "provider_call_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("provider_response_id", sa.String(length=255)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "cache_key", name="uq_provider_call_cache_project_key"),
    )


def downgrade() -> None:
    op.drop_table("provider_call_cache")
    op.drop_column("run_attempts", "cache_hit")
    op.drop_column("run_attempts", "cache_key")
    op.drop_column("run_attempts", "available_at")
    op.drop_column("run_attempts", "retry_after_seconds")
    op.drop_column("run_attempts", "parent_attempt_id")
    op.drop_column("run_attempts", "attempt_number")
    op.drop_column("run_attempts", "terminal_failure_reason")
    op.drop_column("run_attempts", "error_kind")
