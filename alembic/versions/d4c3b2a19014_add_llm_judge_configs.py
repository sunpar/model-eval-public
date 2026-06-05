"""add llm judge configs

Revision ID: d4c3b2a19014
Revises: e7d4a2c9f013
Create Date: 2026-05-20 18:45:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4c3b2a19014"
down_revision: str | Sequence[str] | None = "e7d4a2c9f013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_judge_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("judge_model_config_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("judge_prompt", sa.Text(), nullable=False),
        sa.Column("rubric_dimensions", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("judge_model_config_slug", sa.String(length=160), nullable=False),
        sa.Column("judge_model_config_version", sa.Integer(), nullable=False),
        sa.Column("raw_provider_params", sa.JSON(), nullable=False),
        sa.Column("calibration_status", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["judge_model_config_id"], ["model_configs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_llm_judge_configs_project_slug_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_judge_configs")
