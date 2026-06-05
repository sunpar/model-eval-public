"""add artifact preprocessing runs

Revision ID: a8c1d2e3f401
Revises: d9e0f1a2b323
Create Date: 2026-05-21 13:10:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a8c1d2e3f401"
down_revision: str | Sequence[str] | None = "d9e0f1a2b323"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_preprocessing_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("parser_name", sa.String(length=160), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source_checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("checksums", sa.JSON(), nullable=False),
        sa.Column("local_storage_uri", sa.String(length=1000), nullable=True),
        sa.Column("source_artifact_snapshot", sa.JSON(), nullable=False),
        sa.Column("derived_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("derived_artifact_snapshots", sa.JSON(), nullable=False),
        sa.Column("error_kind", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_metadata", sa.JSON(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifact_preprocessing_runs_project_status",
        "artifact_preprocessing_runs",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_artifact_preprocessing_runs_source",
        "artifact_preprocessing_runs",
        ["source_artifact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_preprocessing_runs_source",
        table_name="artifact_preprocessing_runs",
    )
    op.drop_index(
        "ix_artifact_preprocessing_runs_project_status",
        table_name="artifact_preprocessing_runs",
    )
    op.drop_table("artifact_preprocessing_runs")
