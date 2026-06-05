"""create persistence tables

Revision ID: bdb42fbb358a
Revises:
Create Date: 2026-05-20 05:37:15.337355
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "bdb42fbb358a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", sa.String(length=80), nullable=True),
        sa.Column("uri", sa.String(length=1000), nullable=True),
        sa.Column("input_mode", sa.String(length=80), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("storage_uri", sa.String(length=1000), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=True),
        sa.Column("image_height", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_artifacts_project_slug_version"
        ),
    )
    op.create_table(
        "cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("prompt_ref", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", "version", name="uq_cases_project_slug_version"),
    )
    op.create_table(
        "conversation_warmers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=160), nullable=True),
        sa.Column("user_level", sa.String(length=160), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", "version", name="uq_warmers_project_slug_version"),
    )
    op.create_table(
        "evaluators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("evaluator_type", sa.String(length=80), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_evaluators_project_slug_version"
        ),
    )
    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest_snapshot", sa.JSON(), nullable=False),
        sa.Column("case_snapshots", sa.JSON(), nullable=False),
        sa.Column("artifact_snapshots", sa.JSON(), nullable=False),
        sa.Column("system_prompt_snapshots", sa.JSON(), nullable=False),
        sa.Column("warmer_snapshots", sa.JSON(), nullable=False),
        sa.Column("model_config_snapshots", sa.JSON(), nullable=False),
        sa.Column("evaluator_snapshots", sa.JSON(), nullable=False),
        sa.Column("design_snapshot", sa.JSON(), nullable=False),
        sa.Column("controls_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", name="uq_experiments_project_slug"),
    )
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_level", sa.String(length=80), nullable=True),
        sa.Column("capability_flags", sa.JSON(), nullable=False),
        sa.Column("raw_provider_params", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_model_configs_project_slug_version"
        ),
    )
    op.create_table(
        "system_prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("prompt_ref", sa.String(length=500), nullable=True),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_system_prompts_project_slug_version"
        ),
    )
    op.create_table(
        "review_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("review_type", sa.String(length=80), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", name="uq_review_sets_project_slug"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("case_slug", sa.String(length=160), nullable=False),
        sa.Column("model_config_slug", sa.String(length=160), nullable=False),
        sa.Column("system_prompt_slug", sa.String(length=160), nullable=False),
        sa.Column("warmer_slug", sa.String(length=160), nullable=False),
        sa.Column("run_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_input_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "run_id", name="uq_runs_experiment_run_id"),
    )
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=120), nullable=False),
        sa.Column("replicate_index", sa.Integer(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "attempt_id", name="uq_run_attempts_run_attempt_id"),
    )
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_set_id", sa.Integer(), nullable=False),
        sa.Column("run_attempt_id", sa.Integer(), nullable=True),
        sa.Column("item_key", sa.String(length=160), nullable=False),
        sa.Column("prompt_snapshot", sa.JSON(), nullable=False),
        sa.Column("answer_snapshot", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("reviewer_decision", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_set_id"],
            ["review_sets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_attempt_id"],
            ["run_attempts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_attempt_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("evaluator_type", sa.String(length=80), nullable=False),
        sa.Column("criterion", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evaluator_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_attempt_id"],
            ["run_attempts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("scores")
    op.drop_table("review_items")
    op.drop_table("run_attempts")
    op.drop_table("runs")
    op.drop_table("review_sets")
    op.drop_table("system_prompts")
    op.drop_table("model_configs")
    op.drop_table("experiments")
    op.drop_table("evaluators")
    op.drop_table("conversation_warmers")
    op.drop_table("cases")
    op.drop_table("artifacts")
    op.drop_table("projects")
    op.drop_table("workspaces")
