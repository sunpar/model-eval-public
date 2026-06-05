"""repair phase 3 artifact and run columns

Revision ID: d7e8f9012345
Revises: c6d7e8f90123
Create Date: 2026-05-25 17:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9012345"
down_revision: str | Sequence[str] | None = "c6d7e8f90123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    artifact_columns = _columns("artifacts")
    if "input_mode" not in artifact_columns:
        op.add_column(
            "artifacts",
            sa.Column(
                "input_mode",
                sa.String(length=80),
                nullable=False,
                server_default="direct_file",
            ),
        )
    if "filename" not in artifact_columns:
        op.add_column("artifacts", sa.Column("filename", sa.String(length=500)))
    if "checksum_sha256" not in artifact_columns:
        op.add_column("artifacts", sa.Column("checksum_sha256", sa.String(length=64)))
    if "size_bytes" not in artifact_columns:
        op.add_column("artifacts", sa.Column("size_bytes", sa.Integer()))
    if "mime_type" not in artifact_columns:
        op.add_column("artifacts", sa.Column("mime_type", sa.String(length=255)))
    if "storage_uri" not in artifact_columns:
        op.add_column("artifacts", sa.Column("storage_uri", sa.String(length=1000)))
    if "image_width" not in artifact_columns:
        op.add_column("artifacts", sa.Column("image_width", sa.Integer()))
    if "image_height" not in artifact_columns:
        op.add_column("artifacts", sa.Column("image_height", sa.Integer()))

    run_columns = _columns("runs")
    if "model_input_snapshot" not in run_columns:
        op.add_column(
            "runs",
            sa.Column(
                "model_input_snapshot",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade() -> None:
    pass


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
