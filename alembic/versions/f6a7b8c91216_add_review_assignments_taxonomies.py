"""add review assignments taxonomies

Revision ID: f6a7b8c91216
Revises: e5f6a7b80115
Create Date: 2026-05-20 20:15:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c91216"
down_revision: str | Sequence[str] | None = "e5f6a7b80115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviewers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", name="uq_reviewers_project_slug"),
    )
    op.create_table(
        "failure_taxonomies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_failure_taxonomies_project_slug_version",
        ),
    )
    op.create_table(
        "review_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_set_id", sa.Integer(), nullable=False),
        sa.Column("review_item_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("taxonomy_snapshot", sa.JSON(), nullable=False),
        sa.Column("decision_snapshot", sa.JSON(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["review_item_id"], ["review_items.id"]),
        sa.ForeignKeyConstraint(["review_set_id"], ["review_sets.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["reviewers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_item_id",
            "reviewer_id",
            name="uq_review_assignments_item_reviewer",
        ),
    )
    op.create_index(
        "ix_review_assignments_review_set_reviewer",
        "review_assignments",
        ["review_set_id", "reviewer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_assignments_review_set_reviewer", table_name="review_assignments")
    op.drop_table("review_assignments")
    op.drop_table("failure_taxonomies")
    op.drop_table("reviewers")
