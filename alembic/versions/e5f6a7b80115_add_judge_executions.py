"""add judge executions

Revision ID: e5f6a7b80115
Revises: d4c3b2a19014
Create Date: 2026-05-20 19:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b80115"
down_revision: str | Sequence[str] | None = "d4c3b2a19014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "judge_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("evaluator_id", sa.String(length=160), nullable=False),
        sa.Column("judge_config_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_run_attempt_ids", sa.JSON(), nullable=False),
        sa.Column("score_ids", sa.JSON(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=80), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("local_only", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "evaluator_id",
            name="uq_judge_executions_experiment_evaluator",
        ),
    )


def downgrade() -> None:
    op.drop_table("judge_executions")
