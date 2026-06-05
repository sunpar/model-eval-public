from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect as sa_inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from model_eval_api.artifact_types import ArtifactInputMode
from model_eval_api.execution_states import AttemptStatus
from model_eval_api.persistence.snapshots import (
    build_artifact_preprocessing_run_snapshot,
    build_artifact_snapshot,
    build_case_snapshot,
    build_conversation_warmer_snapshot,
    build_evaluator_snapshot,
    build_benchmark_suite_snapshot,
    build_failure_taxonomy_snapshot,
    build_llm_judge_config_snapshot,
    build_metric_adapter_config_snapshot,
    build_model_config_snapshot,
    build_system_prompt_snapshot,
    sanitize_preprocessing_error_metadata,
    sanitize_provider_params,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    projects: Mapped[list[Project]] = relationship(back_populates="workspace")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_allow_list: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_deny_list: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    workspace: Mapped[Workspace] = relationship(back_populates="projects")
    experiments: Mapped[list[Experiment]] = relationship(back_populates="project")


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_cases_project_slug_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text)
    prompt_ref: Mapped[str | None] = mapped_column(String(500))
    dataset_split: Mapped[str] = mapped_column(String(40), default="dev", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "slug", "version", name="uq_artifacts_project_slug_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str | None] = mapped_column(String(80))
    uri: Mapped[str | None] = mapped_column(String(1000))
    input_mode: Mapped[str] = mapped_column(
        String(80), default=ArtifactInputMode.DIRECT_FILE.value, nullable=False
    )
    filename: Mapped[str | None] = mapped_column(String(500))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    storage_uri: Mapped[str | None] = mapped_column(String(1000))
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ArtifactPreprocessingRun(Base):
    __tablename__ = "artifact_preprocessing_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    source_artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(160), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    source_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    checksums: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    local_storage_uri: Mapped[str | None] = mapped_column(String(1000))
    source_artifact_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    derived_artifact_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    derived_artifact_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    error_kind: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship()
    source_artifact: Mapped[Artifact] = relationship()

    @property
    def snapshot(self) -> dict[str, Any]:
        return build_artifact_preprocessing_run_snapshot(self)


class SystemPrompt(Base):
    __tablename__ = "system_prompts"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "slug", "version", name="uq_system_prompts_project_slug_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text)
    prompt_ref: Mapped[str | None] = mapped_column(String(500))
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationWarmer(Base):
    __tablename__ = "conversation_warmers"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_warmers_project_slug_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(160))
    user_level: Mapped[str | None] = mapped_column(String(160))
    intent: Mapped[str | None] = mapped_column(Text)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    version_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "slug", "version", name="uq_model_configs_project_slug_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float | None] = mapped_column(Float)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_level: Mapped[str | None] = mapped_column(String(80))
    capability_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    raw_provider_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Evaluator(Base):
    __tablename__ = "evaluators"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "slug", "version", name="uq_evaluators_project_slug_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluator_type: Mapped[str | None] = mapped_column(String(80))
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LLMJudgeConfig(Base):
    __tablename__ = "llm_judge_configs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_llm_judge_configs_project_slug_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    judge_model_config_id: Mapped[int] = mapped_column(
        ForeignKey("model_configs.id"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    judge_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    judge_model_config_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    judge_model_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_provider_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    calibration_status: Mapped[str] = mapped_column(String(80), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MetricAdapterConfig(Base):
    __tablename__ = "metric_adapter_configs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_metric_adapter_configs_project_slug_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(80), nullable=False)
    required_inputs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    capability_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    local_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship()


class BenchmarkSuite(Base):
    __tablename__ = "benchmark_suites"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "slug", "version", name="uq_benchmark_suites_project_slug_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    controls_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship()
    items: Mapped[list[BenchmarkSuiteItem]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )


class BenchmarkSuiteItem(Base):
    __tablename__ = "benchmark_suite_items"
    __table_args__ = (
        UniqueConstraint(
            "suite_id",
            "item_type",
            "item_slug",
            "item_version",
            name="uq_benchmark_suite_items_membership",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("benchmark_suites.id"), nullable=False)
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    item_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    item_version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_split: Mapped[str | None] = mapped_column(String(40))
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    suite: Mapped[BenchmarkSuite] = relationship(back_populates="items")


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_experiments_project_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    manifest_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    case_snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    artifact_snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    system_prompt_snapshots: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    warmer_snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_config_snapshots: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    evaluator_snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    design_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    controls_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship(back_populates="experiments")
    runs: Mapped[list[Run]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "run_id", name="uq_runs_experiment_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(120), nullable=False)
    case_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    model_config_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    system_prompt_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    warmer_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    run_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    data_egress_label: Mapped[str] = mapped_column(String(80), default="local_only", nullable=False)
    context_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    truncation_policy: Mapped[str] = mapped_column(
        String(80), default="fail_on_over_budget", nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    experiment: Mapped[Experiment] = relationship(back_populates="runs")
    attempts: Mapped[list[RunAttempt]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_id", name="uq_run_attempts_run_attempt_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(120), nullable=False)
    replicate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    replicate_group_id: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attempt_kind: Mapped[str] = mapped_column(String(40), default="replicate", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(255))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provider_response_id: Mapped[str | None] = mapped_column(String(255))
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    system_fingerprint: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(40), default=AttemptStatus.QUEUED.value, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String(80))
    terminal_failure_reason: Mapped[str | None] = mapped_column(Text)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_attempt_id: Mapped[str | None] = mapped_column(String(120))
    retry_after_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cache_key: Mapped[str | None] = mapped_column(String(128))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[Run] = relationship(back_populates="attempts")
    scores: Mapped[list[Score]] = relationship(
        back_populates="run_attempt", cascade="all, delete-orphan"
    )


class ProviderCallCache(Base):
    __tablename__ = "provider_call_cache"
    __table_args__ = (
        UniqueConstraint("project_id", "cache_key", name="uq_provider_call_cache_project_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provider_response_id: Mapped[str | None] = mapped_column(String(255))
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    system_fingerprint: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class JudgeExecution(Base):
    __tablename__ = "judge_executions"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "evaluator_id",
            name="uq_judge_executions_experiment_evaluator",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    evaluator_id: Mapped[str] = mapped_column(String(160), nullable=False)
    judge_config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    source_run_attempt_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    score_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    mode: Mapped[str] = mapped_column(String(80), default="pairwise", nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    local_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    run_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("run_attempts.id"))
    event_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(160))
    actor: Mapped[str | None] = mapped_column(String(160))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_attempt_id: Mapped[int] = mapped_column(ForeignKey("run_attempts.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    evaluator_type: Mapped[str] = mapped_column(String(80), nullable=False)
    criterion: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    evaluator_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run_attempt: Mapped[RunAttempt] = relationship(back_populates="scores")


class ReviewSet(Base):
    __tablename__ = "review_sets"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_review_sets_project_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"))
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    review_type: Mapped[str] = mapped_column(String(80), default="blind", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    items: Mapped[list[ReviewItem]] = relationship(
        back_populates="review_set", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[ReviewAssignment]] = relationship(
        back_populates="review_set", cascade="all, delete-orphan"
    )


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_set_id: Mapped[int] = mapped_column(ForeignKey("review_sets.id"), nullable=False)
    run_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("run_attempts.id"))
    item_key: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    answer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reviewer_decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    review_set: Mapped[ReviewSet] = relationship(back_populates="items")
    run_attempt: Mapped[RunAttempt | None] = relationship()
    assignments: Mapped[list[ReviewAssignment]] = relationship(
        back_populates="review_item", cascade="all, delete-orphan"
    )


class Reviewer(Base):
    __tablename__ = "reviewers"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_reviewers_project_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship()
    assignments: Mapped[list[ReviewAssignment]] = relationship(back_populates="reviewer")


class FailureTaxonomy(Base):
    __tablename__ = "failure_taxonomies"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "slug",
            "version",
            name="uq_failure_taxonomies_project_slug_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship()


class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    __table_args__ = (
        UniqueConstraint(
            "review_item_id",
            "reviewer_id",
            name="uq_review_assignments_item_reviewer",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_set_id: Mapped[int] = mapped_column(ForeignKey("review_sets.id"), nullable=False)
    review_item_id: Mapped[int] = mapped_column(ForeignKey("review_items.id"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("reviewers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    taxonomy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    decision_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review_set: Mapped[ReviewSet] = relationship(back_populates="assignments")
    review_item: Mapped[ReviewItem] = relationship(back_populates="assignments")
    reviewer: Mapped[Reviewer] = relationship(back_populates="assignments")


@event.listens_for(Case, "before_insert")
@event.listens_for(Case, "before_update")
def _sync_case_snapshot(_: Any, __: Any, target: Case) -> None:
    target.created_at = target.created_at or utc_now()
    target.dataset_split = target.dataset_split or "dev"
    target.snapshot = build_case_snapshot(target)


@event.listens_for(Artifact, "before_insert")
@event.listens_for(Artifact, "before_update")
def _sync_artifact_snapshot(_: Any, __: Any, target: Artifact) -> None:
    target.created_at = target.created_at or utc_now()
    target.snapshot = build_artifact_snapshot(target)


@event.listens_for(ArtifactPreprocessingRun, "before_insert")
@event.listens_for(ArtifactPreprocessingRun, "before_update")
def _sync_artifact_preprocessing_run_payload(
    _: Any, __: Any, target: ArtifactPreprocessingRun
) -> None:
    target.created_at = target.created_at or utc_now()
    target.status = target.status or "queued"
    target.checksums = dict(target.checksums or {})
    target.derived_artifact_ids = list(target.derived_artifact_ids or [])
    target.derived_artifact_snapshots = list(target.derived_artifact_snapshots or [])
    target.error_metadata = sanitize_preprocessing_error_metadata(dict(target.error_metadata or {}))
    if not target.source_artifact_snapshot and target.source_artifact is not None:
        target.source_artifact_snapshot = build_artifact_snapshot(target.source_artifact)
    if target.source_checksum_sha256 is None and target.source_artifact is not None:
        target.source_checksum_sha256 = target.source_artifact.checksum_sha256


@event.listens_for(SystemPrompt, "before_insert")
@event.listens_for(SystemPrompt, "before_update")
def _sync_system_prompt_snapshot(_: Any, __: Any, target: SystemPrompt) -> None:
    target.created_at = target.created_at or utc_now()
    target.snapshot = build_system_prompt_snapshot(target)


@event.listens_for(ConversationWarmer, "before_insert")
@event.listens_for(ConversationWarmer, "before_update")
def _sync_conversation_warmer_snapshot(_: Any, __: Any, target: ConversationWarmer) -> None:
    target.created_at = target.created_at or utc_now()
    target.snapshot = build_conversation_warmer_snapshot(target)


@event.listens_for(ModelConfig, "before_insert")
@event.listens_for(ModelConfig, "before_update")
def _sync_model_config_snapshot(_: Any, __: Any, target: ModelConfig) -> None:
    target.created_at = target.created_at or utc_now()
    target.raw_provider_params = sanitize_provider_params(target.raw_provider_params or {})
    target.snapshot = build_model_config_snapshot(target)


@event.listens_for(Evaluator, "before_insert")
@event.listens_for(Evaluator, "before_update")
def _sync_evaluator_snapshot(_: Any, __: Any, target: Evaluator) -> None:
    target.created_at = target.created_at or utc_now()
    target.snapshot = build_evaluator_snapshot(target)


@event.listens_for(LLMJudgeConfig, "before_insert")
@event.listens_for(LLMJudgeConfig, "before_update")
def _sync_llm_judge_config_snapshot(_: Any, __: Any, target: LLMJudgeConfig) -> None:
    target.created_at = target.created_at or utc_now()
    target.raw_provider_params = sanitize_provider_params(target.raw_provider_params or {})
    target.snapshot = build_llm_judge_config_snapshot(target)


@event.listens_for(MetricAdapterConfig, "before_insert")
def _sync_metric_adapter_config_snapshot(_: Any, __: Any, target: MetricAdapterConfig) -> None:
    target.created_at = target.created_at or utc_now()
    target.required_inputs = [str(item) for item in target.required_inputs or []]
    target.capability_metadata = sanitize_provider_params(target.capability_metadata or {})
    target.snapshot = build_metric_adapter_config_snapshot(target)


@event.listens_for(MetricAdapterConfig, "before_update")
def _prevent_metric_adapter_config_update(_: Any, __: Any, target: MetricAdapterConfig) -> None:
    state = sa_inspect(target)
    changed_fields = [
        field
        for field in (
            "project_id",
            "slug",
            "name",
            "adapter_kind",
            "adapter_version",
            "required_inputs",
            "output_schema",
            "capability_metadata",
            "local_only",
            "version",
            "snapshot",
            "archived",
            "created_at",
        )
        if state.attrs[field].history.has_changes()
    ]
    if changed_fields:
        raise ValueError("Metric adapter config versions are immutable; create a new version.")


@event.listens_for(FailureTaxonomy, "before_insert")
@event.listens_for(FailureTaxonomy, "before_update")
def _sync_failure_taxonomy_snapshot(_: Any, __: Any, target: FailureTaxonomy) -> None:
    target.created_at = target.created_at or utc_now()
    target.tags = [str(tag) for tag in target.tags or []]
    target.snapshot = build_failure_taxonomy_snapshot(target)


@event.listens_for(BenchmarkSuite, "before_insert")
@event.listens_for(BenchmarkSuite, "before_update")
def _sync_benchmark_suite_snapshot(_: Any, __: Any, target: BenchmarkSuite) -> None:
    target.created_at = target.created_at or utc_now()
    target.controls_json = sanitize_provider_params(target.controls_json or {})
    target.snapshot = build_benchmark_suite_snapshot(target)


@event.listens_for(BenchmarkSuiteItem, "before_insert")
@event.listens_for(BenchmarkSuiteItem, "before_update")
def _sync_benchmark_suite_item_payload(_: Any, __: Any, target: BenchmarkSuiteItem) -> None:
    target.created_at = target.created_at or utc_now()
    target.snapshot_json = sanitize_provider_params(target.snapshot_json or {})


@event.listens_for(Reviewer, "before_insert")
@event.listens_for(Reviewer, "before_update")
def _sync_reviewer_timestamp(_: Any, __: Any, target: Reviewer) -> None:
    target.created_at = target.created_at or utc_now()


@event.listens_for(ReviewAssignment, "before_insert")
@event.listens_for(ReviewAssignment, "before_update")
def _sync_review_assignment_payload(_: Any, __: Any, target: ReviewAssignment) -> None:
    target.assigned_at = target.assigned_at or utc_now()
    target.taxonomy_snapshot = sanitize_provider_params(target.taxonomy_snapshot or {})
    target.decision_snapshot = sanitize_provider_params(target.decision_snapshot or {})


@event.listens_for(RunAttempt, "before_insert")
@event.listens_for(RunAttempt, "before_update")
def _sanitize_run_attempt_payloads(_: Any, __: Any, target: RunAttempt) -> None:
    target.request_payload = sanitize_provider_params(target.request_payload or {})
    target.response_payload = sanitize_provider_params(target.response_payload or {})
    target.pricing_snapshot = sanitize_provider_params(target.pricing_snapshot or {})
    target.provider_metadata = sanitize_provider_params(target.provider_metadata or {})


@event.listens_for(ProviderCallCache, "before_insert")
@event.listens_for(ProviderCallCache, "before_update")
def _sanitize_provider_call_cache(_: Any, __: Any, target: ProviderCallCache) -> None:
    target.request_payload = sanitize_provider_params(target.request_payload or {})
    target.response_payload = sanitize_provider_params(target.response_payload or {})
    target.provider_metadata = sanitize_provider_params(target.provider_metadata or {})


@event.listens_for(JudgeExecution, "before_insert")
@event.listens_for(JudgeExecution, "before_update")
def _sanitize_judge_execution_payloads(_: Any, __: Any, target: JudgeExecution) -> None:
    target.created_at = target.created_at or utc_now()
    target.judge_config_snapshot = sanitize_provider_params(target.judge_config_snapshot or {})
    target.request_payload = sanitize_provider_params(target.request_payload or {})
    target.response_payload = sanitize_provider_params(target.response_payload or {})
    target.metadata_json = sanitize_provider_params(target.metadata_json or {})


@event.listens_for(AuditLog, "before_insert")
@event.listens_for(AuditLog, "before_update")
def _sanitize_audit_details(_: Any, __: Any, target: AuditLog) -> None:
    target.details = sanitize_provider_params(target.details or {})
