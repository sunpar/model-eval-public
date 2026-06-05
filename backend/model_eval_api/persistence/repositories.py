from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from model_eval_api.manifest import (
    ArtifactManifest,
    DATASET_SPLITS,
    CaseManifest,
    EvaluatorManifest,
    ExperimentManifest,
    IdObject,
    ManifestPreviewResponse,
    ModelConfigManifest,
    SystemPromptManifest,
    WarmerManifest,
    expand_manifest,
)
from model_eval_api.metric_adapters import get_metric_adapter, normalize_metric_adapter_kind
from model_eval_api.artifact_types import DERIVED_ARTIFACT_INPUT_MODE_VALUES, ArtifactInputMode
from model_eval_api.persistence.models import (
    AuditLog,
    Artifact,
    ArtifactPreprocessingRun,
    BenchmarkSuite,
    BenchmarkSuiteItem,
    Case,
    ConversationWarmer,
    Evaluator,
    Experiment,
    FailureTaxonomy,
    LLMJudgeConfig,
    MetricAdapterConfig,
    ModelConfig,
    Project,
    ReviewAssignment,
    ReviewItem,
    Reviewer,
    ReviewSet,
    Run,
    RunAttempt,
    Score,
    SystemPrompt,
    Workspace,
    utc_now,
)
from model_eval_api.persistence.snapshots import (
    build_artifact_preprocessing_run_snapshot,
    build_model_input_snapshot,
    build_artifact_snapshot as snapshot_artifact,
    build_benchmark_suite_snapshot as snapshot_benchmark_suite,
    build_case_snapshot as snapshot_case,
    build_conversation_warmer_snapshot as snapshot_conversation_warmer,
    build_evaluator_snapshot as snapshot_evaluator,
    build_llm_judge_config_snapshot as snapshot_llm_judge_config,
    build_metric_adapter_config_snapshot as snapshot_metric_adapter_config,
    build_model_config_snapshot as snapshot_model_config,
    build_system_prompt_snapshot as snapshot_system_prompt,
    sanitize_preprocessing_error_metadata,
    sanitize_provider_params,
)
from model_eval_api.providers import build_pricing_snapshot
from model_eval_api.response_payloads import attempt_output_text as _shared_attempt_output_text


DEFAULT_COPPER_FAILURE_TAGS = [
    "too generic",
    "missed transmission mechanism",
    "no quantified impact",
    "invented numbers",
    "weak trade expression",
    "ignored inventory dynamics",
    "no second-order effects",
    "weak risks",
    "overconfident conclusion",
    "spot/futures confusion",
]

HUMAN_REVIEW_SCORE_TYPES = {
    "pairwise_preference",
    "pass_fail",
    "failure_tags",
    "rubric_notes",
    "freeform_notes",
}

BENCHMARK_ITEM_TYPES = {"case", "model", "system_prompt", "warmer", "evaluator"}
METRIC_ADAPTER_INPUT_FIELDS = {
    "answer_text",
    "retrieved_chunks",
    "citations",
    "reference_answers",
    "derived_artifacts",
}


def create_workspace(session: Session, *, slug: str, name: str) -> Workspace:
    workspace = Workspace(slug=slug, name=name)
    session.add(workspace)
    return workspace


def create_project(
    session: Session,
    *,
    workspace: Workspace,
    slug: str,
    name: str,
    provider_allow_list: list[str] | None = None,
    provider_deny_list: list[str] | None = None,
) -> Project:
    project = Project(
        workspace=workspace,
        slug=slug,
        name=name,
        provider_allow_list=normalize_provider_policy_list(provider_allow_list),
        provider_deny_list=normalize_provider_policy_list(provider_deny_list),
    )
    session.add(project)
    return project


def record_audit_event(
    session: Session,
    *,
    event_kind: str,
    entity_type: str,
    project: Project | None = None,
    experiment: Experiment | None = None,
    run: Run | None = None,
    run_attempt: RunAttempt | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    if project is None and experiment is not None:
        project = experiment.project
    if experiment is None and run is not None:
        experiment = run.experiment
        project = project or experiment.project
    if run is None and run_attempt is not None:
        run = run_attempt.run
        experiment = experiment or run.experiment
        project = project or experiment.project
    audit_log = AuditLog(
        project_id=project.id if project is not None else None,
        experiment_id=experiment.id if experiment is not None else None,
        run_id=run.id if run is not None else None,
        run_attempt_id=run_attempt.id if run_attempt is not None else None,
        event_kind=event_kind,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=_audit_details(details or {}),
    )
    session.add(audit_log)
    return audit_log


def create_case(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    prompt: str | None = None,
    prompt_ref: str | None = None,
    dataset_split: str = "dev",
    version: int = 1,
    archived: bool = False,
) -> Case:
    session.flush()
    split = normalize_dataset_split(dataset_split)
    case = Case(
        project_id=project.id,
        slug=slug,
        name=name,
        prompt=prompt,
        prompt_ref=prompt_ref,
        dataset_split=split,
        version=version,
        archived=archived,
    )
    case.snapshot = snapshot_case(case)
    session.add(case)
    return case


def create_artifact(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    artifact_type: str | None = None,
    uri: str | None = None,
    input_mode: ArtifactInputMode | str | None = None,
    filename: str | None = None,
    checksum_sha256: str | None = None,
    size_bytes: int | None = None,
    mime_type: str | None = None,
    storage_uri: str | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    metadata: dict[str, Any] | None = None,
    version: int = 1,
    archived: bool = False,
) -> Artifact:
    session.flush()
    normalized_input_mode = _artifact_input_mode_for_create(input_mode, mime_type)
    normalized_storage_uri = storage_uri or uri
    artifact = Artifact(
        project_id=project.id,
        slug=slug,
        name=name,
        artifact_type=artifact_type,
        uri=uri,
        input_mode=normalized_input_mode,
        filename=filename,
        checksum_sha256=checksum_sha256,
        size_bytes=size_bytes,
        mime_type=mime_type,
        storage_uri=normalized_storage_uri,
        image_width=image_width,
        image_height=image_height,
        metadata_json=metadata or {},
        version=version,
        archived=archived,
    )
    artifact.snapshot = snapshot_artifact(artifact)
    session.add(artifact)
    return artifact


def _artifact_input_mode_for_create(
    input_mode: ArtifactInputMode | str | None, mime_type: str | None
) -> str:
    if input_mode is not None:
        if isinstance(input_mode, ArtifactInputMode):
            return input_mode.value
        return ArtifactInputMode(input_mode).value
    if isinstance(mime_type, str) and mime_type.startswith("image/"):
        return ArtifactInputMode.IMAGE_DIRECT.value
    return ArtifactInputMode.DIRECT_FILE.value


def create_artifact_preprocessing_run(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    parser_name: str,
    parser_version: str,
    local_storage_uri: str | None = None,
    status: str = "queued",
) -> ArtifactPreprocessingRun:
    session.flush()
    if source_artifact.project_id != project.id:
        raise ValueError("Source artifact must belong to the preprocessing project.")
    record = ArtifactPreprocessingRun(
        project_id=project.id,
        source_artifact_id=source_artifact.id,
        source_artifact=source_artifact,
        parser_name=parser_name,
        parser_version=parser_version,
        status=status,
        source_checksum_sha256=source_artifact.checksum_sha256,
        checksums={"source": source_artifact.checksum_sha256},
        local_storage_uri=local_storage_uri or source_artifact.storage_uri or source_artifact.uri,
        source_artifact_snapshot=snapshot_artifact(source_artifact),
        derived_artifact_ids=[],
        derived_artifact_snapshots=[],
        error_metadata={},
    )
    session.add(record)
    return record


def complete_artifact_preprocessing_run(
    session: Session,
    *,
    preprocessing_run: ArtifactPreprocessingRun,
    derived_artifacts: list[Artifact],
    local_storage_uri: str | None = None,
    output_checksums: dict[str, Any] | None = None,
    extracted_at: datetime | None = None,
) -> ArtifactPreprocessingRun:
    session.flush()
    mismatched = [
        artifact.slug
        for artifact in derived_artifacts
        if artifact.project_id != preprocessing_run.project_id
    ]
    if mismatched:
        raise ValueError("Derived artifacts must belong to the preprocessing project.")
    preprocessing_run.status = "completed"
    preprocessing_run.derived_artifact_ids = [artifact.id for artifact in derived_artifacts]
    preprocessing_run.derived_artifact_snapshots = [
        snapshot_artifact(artifact) for artifact in derived_artifacts
    ]
    preprocessing_run.local_storage_uri = (
        local_storage_uri
        or preprocessing_run.local_storage_uri
        or (derived_artifacts[0].storage_uri if derived_artifacts else None)
    )
    preprocessing_run.checksums = {
        "source": preprocessing_run.source_checksum_sha256,
        "derived": {
            _artifact_checksum_key(artifact): artifact.checksum_sha256
            for artifact in derived_artifacts
            if artifact.checksum_sha256
        },
        "output": dict(output_checksums or {}),
    }
    preprocessing_run.extracted_at = extracted_at or utc_now()
    preprocessing_run.completed_at = utc_now()
    preprocessing_run.error_kind = None
    preprocessing_run.error_message = None
    preprocessing_run.error_metadata = {}
    return preprocessing_run


def fail_artifact_preprocessing_run(
    session: Session,
    *,
    preprocessing_run: ArtifactPreprocessingRun,
    error_kind: str,
    error_message: str,
    error_metadata: dict[str, Any] | None = None,
    completed_at: datetime | None = None,
) -> ArtifactPreprocessingRun:
    session.flush()
    preprocessing_run.status = "failed"
    preprocessing_run.error_kind = error_kind
    preprocessing_run.error_message = error_message
    preprocessing_run.error_metadata = sanitize_preprocessing_error_metadata(error_metadata or {})
    preprocessing_run.completed_at = completed_at or utc_now()
    return preprocessing_run


def list_artifact_preprocessing_runs(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact | None = None,
    status: str | None = None,
) -> list[ArtifactPreprocessingRun]:
    statement = (
        select(ArtifactPreprocessingRun)
        .where(ArtifactPreprocessingRun.project_id == project.id)
        .order_by(ArtifactPreprocessingRun.id)
    )
    if source_artifact is not None:
        statement = statement.where(
            ArtifactPreprocessingRun.source_artifact_id == source_artifact.id
        )
    if status is not None:
        statement = statement.where(ArtifactPreprocessingRun.status == status)
    return list(session.scalars(statement).all())


def snapshot_artifact_preprocessing_run(
    preprocessing_run: ArtifactPreprocessingRun,
) -> dict[str, Any]:
    return build_artifact_preprocessing_run_snapshot(preprocessing_run)


def create_system_prompt(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    prompt: str | None = None,
    prompt_ref: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    version: int = 1,
    archived: bool = False,
) -> SystemPrompt:
    session.flush()
    system_prompt = SystemPrompt(
        project_id=project.id,
        slug=slug,
        name=name,
        prompt=prompt,
        prompt_ref=prompt_ref,
        messages=messages or [],
        version=version,
        archived=archived,
    )
    system_prompt.snapshot = snapshot_system_prompt(system_prompt)
    session.add(system_prompt)
    return system_prompt


def create_conversation_warmer(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    messages: list[dict[str, Any]] | None = None,
    domain: str | None = None,
    user_level: str | None = None,
    intent: str | None = None,
    tags: list[str] | None = None,
    version_note: str | None = None,
    version: int = 1,
    archived: bool = False,
) -> ConversationWarmer:
    session.flush()
    warmer = ConversationWarmer(
        project_id=project.id,
        slug=slug,
        name=name,
        domain=domain,
        user_level=user_level,
        intent=intent,
        messages=messages or [],
        tags=tags or [],
        version_note=version_note,
        version=version,
        archived=archived,
    )
    warmer.snapshot = snapshot_conversation_warmer(warmer)
    session.add(warmer)
    return warmer


def create_model_config(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    provider: str,
    model: str,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    reasoning_level: str | None = None,
    capability_flags: dict[str, Any] | None = None,
    raw_provider_params: dict[str, Any] | None = None,
    version: int = 1,
    archived: bool = False,
) -> ModelConfig:
    session.flush()
    raw_params = raw_provider_params or {}
    model_config = ModelConfig(
        project_id=project.id,
        slug=slug,
        name=name,
        provider=normalize_provider_name(provider),
        model=model,
        temperature=temperature
        if temperature is not None
        else _float_param(raw_params, "temperature"),
        max_output_tokens=max_output_tokens
        if max_output_tokens is not None
        else _int_param(raw_params, "max_output_tokens", "max_tokens"),
        reasoning_level=reasoning_level or _reasoning_level(raw_params),
        capability_flags=capability_flags or {},
        raw_provider_params=sanitize_provider_params(raw_params),
        version=version,
        archived=archived,
    )
    model_config.snapshot = snapshot_model_config(model_config)
    session.add(model_config)
    return model_config


def create_evaluator(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    evaluator_type: str | None = None,
    definition: dict[str, Any] | None = None,
    version: int = 1,
    archived: bool = False,
) -> Evaluator:
    session.flush()
    evaluator = Evaluator(
        project_id=project.id,
        slug=slug,
        name=name,
        evaluator_type=evaluator_type,
        definition=definition or {},
        version=version,
        archived=archived,
    )
    evaluator.snapshot = snapshot_evaluator(evaluator)
    session.add(evaluator)
    return evaluator


def create_llm_judge_config(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    judge_prompt: str,
    rubric_dimensions: list[dict[str, Any]] | None = None,
    output_schema: dict[str, Any] | None = None,
    judge_model_config_slug: str,
    judge_model_config_version: int | None = None,
    raw_provider_params: dict[str, Any] | None = None,
    calibration_status: str = "draft",
    version: int = 1,
    archived: bool = False,
) -> LLMJudgeConfig:
    session.flush()
    model_config = _require_model_config_reference(
        session, project, judge_model_config_slug, judge_model_config_version
    )
    schema = _validated_output_schema(output_schema)
    prompt = _validated_judge_prompt(judge_prompt)
    judge_config = LLMJudgeConfig(
        project_id=project.id,
        judge_model_config_id=model_config.id,
        slug=slug,
        name=name,
        judge_prompt=prompt,
        rubric_dimensions=list(rubric_dimensions or []),
        output_schema=schema,
        judge_model_config_slug=model_config.slug,
        judge_model_config_version=model_config.version,
        raw_provider_params=sanitize_provider_params(raw_provider_params or {}),
        calibration_status=calibration_status,
        version=version,
        archived=archived,
    )
    judge_config.snapshot = snapshot_llm_judge_config(judge_config)
    session.add(judge_config)
    return judge_config


def create_llm_judge_config_version(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    judge_prompt: str,
    rubric_dimensions: list[dict[str, Any]] | None = None,
    output_schema: dict[str, Any] | None = None,
    judge_model_config_slug: str,
    judge_model_config_version: int | None = None,
    raw_provider_params: dict[str, Any] | None = None,
    calibration_status: str = "draft",
    archived: bool = False,
) -> LLMJudgeConfig:
    latest = _by_slug(session, LLMJudgeConfig, project, slug)
    if latest is None:
        raise _missing_reference_error("LLM judge config", project, slug)
    next_version = int(latest.version) + 1
    return create_llm_judge_config(
        session,
        project=project,
        slug=slug,
        name=name,
        judge_prompt=judge_prompt,
        rubric_dimensions=rubric_dimensions,
        output_schema=output_schema,
        judge_model_config_slug=judge_model_config_slug,
        judge_model_config_version=judge_model_config_version,
        raw_provider_params=raw_provider_params,
        calibration_status=calibration_status,
        version=next_version,
        archived=archived,
    )


def archive_llm_judge_config(
    session: Session, *, project: Project, slug: str, version: int | None = None
) -> LLMJudgeConfig:
    judge_config = _by_slug(session, LLMJudgeConfig, project, slug, version)
    if judge_config is None:
        raise _missing_reference_error("LLM judge config", project, slug)
    judge_config.archived = True
    judge_config.snapshot = snapshot_llm_judge_config(judge_config)
    session.add(judge_config)
    return judge_config


def archive_llm_judge_config_by_id(
    session: Session, *, project: Project, judge_config_id: int
) -> LLMJudgeConfig:
    judge_config = session.get(LLMJudgeConfig, judge_config_id)
    if judge_config is None or judge_config.project_id != project.id:
        raise ValueError(f"LLM judge config {judge_config_id} does not exist.")
    judge_config.archived = True
    judge_config.snapshot = snapshot_llm_judge_config(judge_config)
    session.add(judge_config)
    return judge_config


def list_llm_judge_configs(session: Session, *, project: Project) -> list[LLMJudgeConfig]:
    return session.scalars(
        select(LLMJudgeConfig)
        .where(LLMJudgeConfig.project_id == project.id)
        .order_by(
            LLMJudgeConfig.slug.asc(),
            LLMJudgeConfig.version.asc(),
            LLMJudgeConfig.id.asc(),
        )
    ).all()


def create_metric_adapter_config(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    adapter_kind: str,
    adapter_version: str,
    required_inputs: list[str],
    output_schema: dict[str, Any],
    capability_metadata: dict[str, Any] | None = None,
    local_only: bool = True,
    version: int = 1,
    archived: bool = False,
) -> MetricAdapterConfig:
    session.flush()
    normalized_inputs = _normalize_metric_adapter_inputs(required_inputs)
    normalized_kind = _validated_metric_adapter_kind(adapter_kind, local_only=local_only)
    config = MetricAdapterConfig(
        project_id=project.id,
        slug=slug,
        name=name,
        adapter_kind=normalized_kind,
        adapter_version=_validated_adapter_version(adapter_version),
        required_inputs=normalized_inputs,
        output_schema=_validated_json_object(output_schema, "output_schema"),
        capability_metadata=sanitize_provider_params(capability_metadata or {}),
        local_only=local_only,
        version=version,
        archived=archived,
    )
    config.snapshot = snapshot_metric_adapter_config(config)
    session.add(config)
    return config


def create_metric_adapter_config_version(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    adapter_kind: str,
    adapter_version: str,
    required_inputs: list[str],
    output_schema: dict[str, Any],
    capability_metadata: dict[str, Any] | None = None,
    local_only: bool = True,
    archived: bool = False,
) -> MetricAdapterConfig:
    latest = _by_slug(session, MetricAdapterConfig, project, slug)
    if latest is None:
        raise _missing_reference_error("Metric adapter config", project, slug)
    return create_metric_adapter_config(
        session,
        project=project,
        slug=slug,
        name=name,
        adapter_kind=adapter_kind,
        adapter_version=adapter_version,
        required_inputs=required_inputs,
        output_schema=output_schema,
        capability_metadata=capability_metadata,
        local_only=local_only,
        version=int(latest.version) + 1,
        archived=archived,
    )


def list_metric_adapter_configs(
    session: Session, *, project: Project
) -> list[MetricAdapterConfig]:
    return session.scalars(
        select(MetricAdapterConfig)
        .where(MetricAdapterConfig.project_id == project.id)
        .order_by(
            MetricAdapterConfig.slug.asc(),
            MetricAdapterConfig.version.asc(),
            MetricAdapterConfig.id.asc(),
        )
    ).all()


def create_benchmark_suite(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    case_ids: list[str],
    model_config_ids: list[str],
    system_prompt_ids: list[str],
    warmer_ids: list[str],
    evaluator_ids: list[str] | None = None,
    controls: dict[str, Any] | None = None,
    description: str | None = None,
    version: int = 1,
    archived: bool = False,
) -> BenchmarkSuite:
    session.flush()
    suite = BenchmarkSuite(
        project_id=project.id,
        slug=slug,
        name=name,
        description=description,
        controls_json=sanitize_provider_params(controls or {}),
        version=version,
        archived=archived,
    )
    session.add(suite)
    session.flush()
    for item in _benchmark_suite_items(
        session,
        project=project,
        case_ids=case_ids,
        model_config_ids=model_config_ids,
        system_prompt_ids=system_prompt_ids,
        warmer_ids=warmer_ids,
        evaluator_ids=evaluator_ids or [],
    ):
        suite.items.append(item)
    session.flush()
    suite.snapshot = snapshot_benchmark_suite(suite)
    record_audit_event(
        session,
        project=project,
        event_kind="benchmark_suite_created",
        entity_type="benchmark_suite",
        entity_id=str(suite.id),
        details={"slug": suite.slug, "version": suite.version},
    )
    return suite


def create_benchmark_suite_version(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    case_ids: list[str],
    model_config_ids: list[str],
    system_prompt_ids: list[str],
    warmer_ids: list[str],
    evaluator_ids: list[str] | None = None,
    controls: dict[str, Any] | None = None,
    description: str | None = None,
    archived: bool = False,
) -> BenchmarkSuite:
    latest = _by_slug(session, BenchmarkSuite, project, slug)
    next_version = 1 if latest is None else int(latest.version) + 1
    return create_benchmark_suite(
        session,
        project=project,
        slug=slug,
        name=name,
        case_ids=case_ids,
        model_config_ids=model_config_ids,
        system_prompt_ids=system_prompt_ids,
        warmer_ids=warmer_ids,
        evaluator_ids=evaluator_ids,
        controls=controls,
        description=description,
        version=next_version,
        archived=archived,
    )


def list_benchmark_suites(session: Session, *, project: Project) -> list[BenchmarkSuite]:
    return session.scalars(
        select(BenchmarkSuite)
        .where(BenchmarkSuite.project_id == project.id)
        .options(selectinload(BenchmarkSuite.items))
        .order_by(BenchmarkSuite.slug.asc(), BenchmarkSuite.version.asc(), BenchmarkSuite.id.asc())
    ).all()


def archive_benchmark_suite_by_id(
    session: Session, *, project: Project, suite_id: int
) -> BenchmarkSuite:
    suite = session.get(BenchmarkSuite, suite_id)
    if suite is None or suite.project_id != project.id:
        raise ValueError(f"Benchmark suite {suite_id} does not exist.")
    suite.archived = True
    suite.snapshot = snapshot_benchmark_suite(suite)
    session.add(suite)
    return suite


def preview_benchmark_suite(
    session: Session, *, suite: BenchmarkSuite, split: str | None = None
) -> dict[str, Any]:
    manifest = benchmark_suite_manifest(suite, split=split)
    preview = expand_manifest(manifest)
    suite_split = manifest.design.split
    return {
        "suite": suite,
        "split": suite_split,
        "suite_snapshot": filtered_benchmark_suite_snapshot(suite, split=split),
        "manifest": manifest,
        "preview": preview,
    }


def run_benchmark_suite(
    session: Session,
    *,
    project: Project,
    suite_ref: int | str,
    split: str | None = None,
    dry_run: bool = True,
    local_only: bool = True,
) -> dict[str, Any]:
    from model_eval_api.executor import execute_experiment
    from model_eval_api.providers import ProviderExecutionConfig

    suite = resolve_benchmark_suite(session, project=project, suite_ref=suite_ref)
    manifest = benchmark_suite_manifest(suite, split=split)
    suite_split = manifest.design.split
    preview = expand_manifest(manifest)
    experiment = session.scalar(
        select(Experiment).where(
            Experiment.project_id == project.id,
            Experiment.slug == manifest.experiment_id,
        )
    )
    if experiment is None:
        experiment = create_experiment_from_manifest(
            session,
            project=project,
            manifest=manifest,
            preview=preview,
        )
        session.flush()
    elif experiment.manifest_snapshot != snapshot_manifest(manifest):
        raise ValueError(
            f"Experiment '{manifest.experiment_id}' already exists with a different manifest."
        )
    execute_experiment(
        session,
        experiment.id,
        dry_run=dry_run,
        provider_config=ProviderExecutionConfig(local_only=local_only),
    )
    session.commit()
    return {
        "suite": suite,
        "split": suite_split,
        "dry_run": dry_run,
        "local_only": local_only,
        "suite_snapshot": filtered_benchmark_suite_snapshot(suite, split=split),
        "manifest": manifest,
        "preview": preview,
        "experiment": {
            "id": experiment.id,
            "slug": experiment.slug,
            "name": experiment.name,
            "status": experiment.status,
        },
        "experiment_record": experiment,
    }


def resolve_benchmark_suite(
    session: Session, *, project: Project, suite_ref: int | str, version: int | None = None
) -> BenchmarkSuite:
    suite_id = suite_ref if isinstance(suite_ref, int) else None
    if suite_id is not None:
        suite = session.get(BenchmarkSuite, suite_id)
        if suite is None or suite.project_id != project.id:
            raise ValueError(f"Benchmark suite {suite_id} does not exist.")
        return suite
    suite = _by_slug(session, BenchmarkSuite, project, str(suite_ref), version)
    if suite is None:
        raise _missing_reference_error("Benchmark suite", project, str(suite_ref))
    return suite


def benchmark_suite_manifest(
    suite: BenchmarkSuite, *, split: str | None = None
) -> ExperimentManifest:
    split = normalize_dataset_split(split) if split is not None else None
    if split == "archived":
        raise ValueError("Benchmark suite split 'archived' cannot be executed.")
    snapshot = filtered_benchmark_suite_snapshot(suite, split=split)
    controls = dict(snapshot["controls"])
    controls.pop("local_only", None)
    replicates = controls.get("replicates", 1)
    if type(replicates) is not int or replicates < 1:
        raise ValueError("Benchmark suite replicates must be an integer greater than or equal to 1.")
    randomize = bool(controls.get("randomize_run_order", True))
    random_seed = controls.get("random_seed")
    split_suffix = split or "all"
    payload = {
        "id": f"{suite.slug}_v{suite.version}_{split_suffix}_suite_run",
        "name": f"{suite.name} {split_suffix} suite run",
        "suite": {"id": suite.slug, "version": suite.version, "split": split},
        "cases": [
            {"id": item["id"], "version": item["version"]} for item in snapshot["cases"]
        ],
        "models": [
            {"id": item["id"], "version": item["version"]} for item in snapshot["models"]
        ],
        "system_prompts": [
            {"id": item["id"], "version": item["version"]}
            for item in snapshot["system_prompts"]
        ],
        "warmers": [
            {"id": item["id"], "version": item["version"]} for item in snapshot["warmers"]
        ],
        "design": {
            "type": "full_factorial",
            "replicates": replicates,
            "randomize_run_order": randomize,
            "random_seed": random_seed,
            "split": split,
        },
        "evaluation": {
            "evaluators": [
                {"id": item["id"], "version": item["version"]} for item in snapshot["evaluators"]
            ]
        },
        "controls": controls,
    }
    return ExperimentManifest.model_validate(payload)


def filtered_benchmark_suite_snapshot(
    suite: BenchmarkSuite, *, split: str | None = None
) -> dict[str, Any]:
    split = normalize_dataset_split(split) if split is not None else None
    cases = [
        _suite_item_payload(item)
        for item in _suite_items(suite, "case")
        if _include_case_suite_item(item, split=split)
    ]
    if not cases:
        label = split or "active"
        raise ValueError(f"Benchmark suite '{suite.slug}' has no {label} cases to run.")
    return {
        "id": suite.slug,
        "name": suite.name,
        "description": suite.description,
        "version": suite.version,
        "archived": suite.archived,
        "controls": sanitize_provider_params(dict(suite.controls_json or {})),
        "case_count": len(cases),
        "cases": cases,
        "models": [_suite_item_payload(item) for item in _suite_items(suite, "model")],
        "system_prompts": [
            _suite_item_payload(item) for item in _suite_items(suite, "system_prompt")
        ],
        "warmers": [_suite_item_payload(item) for item in _suite_items(suite, "warmer")],
        "evaluators": [_suite_item_payload(item) for item in _suite_items(suite, "evaluator")],
    }


def create_experiment_from_manifest(
    session: Session,
    *,
    project: Project,
    manifest: ExperimentManifest,
    preview: ManifestPreviewResponse | None = None,
) -> Experiment:
    session.flush()
    preview = preview or expand_manifest(manifest)
    snapshots = _build_experiment_snapshots(session, project, manifest)
    experiment = Experiment(
        project_id=project.id,
        slug=manifest.experiment_id,
        name=manifest.name,
        manifest_snapshot=snapshot_manifest(manifest),
        case_snapshots=snapshots["cases"],
        artifact_snapshots=snapshots["artifacts"],
        system_prompt_snapshots=snapshots["system_prompts"],
        warmer_snapshots=snapshots["warmers"],
        model_config_snapshots=snapshots["models"],
        evaluator_snapshots=snapshots["evaluators"],
        design_snapshot=snapshot_design(manifest, preview.random_seed),
        controls_snapshot=manifest.controls.model_dump(mode="json"),
        pricing_snapshot=_pricing_snapshot(snapshots["models"]),
    )
    session.add(experiment)
    session.flush()
    _create_runs_from_preview(session, experiment=experiment, preview=preview, snapshots=snapshots)
    record_audit_event(
        session,
        project=project,
        experiment=experiment,
        event_kind="experiment_created",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={
            "slug": experiment.slug,
            "logical_runs": preview.logical_runs,
            "run_attempts": preview.run_attempts,
        },
    )
    return experiment


def update_draft_experiment_from_manifest(
    session: Session,
    *,
    project: Project,
    experiment: Experiment,
    manifest: ExperimentManifest,
    preview: ManifestPreviewResponse | None = None,
) -> Experiment:
    session.flush()
    preview = preview or expand_manifest(manifest)
    snapshots = _build_experiment_snapshots(session, project, manifest)
    experiment.slug = manifest.experiment_id
    experiment.name = manifest.name
    experiment.manifest_snapshot = snapshot_manifest(manifest)
    experiment.case_snapshots = snapshots["cases"]
    experiment.artifact_snapshots = snapshots["artifacts"]
    experiment.system_prompt_snapshots = snapshots["system_prompts"]
    experiment.warmer_snapshots = snapshots["warmers"]
    experiment.model_config_snapshots = snapshots["models"]
    experiment.evaluator_snapshots = snapshots["evaluators"]
    experiment.design_snapshot = snapshot_design(manifest, preview.random_seed)
    experiment.controls_snapshot = manifest.controls.model_dump(mode="json")
    experiment.pricing_snapshot = _pricing_snapshot(snapshots["models"])
    experiment.runs.clear()
    session.flush()
    _create_runs_from_preview(session, experiment=experiment, preview=preview, snapshots=snapshots)
    record_audit_event(
        session,
        project=project,
        experiment=experiment,
        event_kind="experiment_updated",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={
            "slug": experiment.slug,
            "logical_runs": preview.logical_runs,
            "run_attempts": preview.run_attempts,
        },
    )
    return experiment


def _build_experiment_snapshots(
    session: Session, project: Project, manifest: ExperimentManifest
) -> dict[str, dict[str, Any]]:
    return {
        "cases": {
            item.id: _case_snapshot_from_manifest(session, project, item) for item in manifest.cases
        },
        "system_prompts": {
            item.id: _system_prompt_snapshot_from_manifest(session, project, item)
            for item in manifest.system_prompts
        },
        "warmers": {
            item.id: _warmer_snapshot_from_manifest(session, project, item)
            for item in manifest.warmers
        },
        "artifacts": {
            item.id: _artifact_snapshot_from_manifest(session, project, item)
            for item in manifest.artifacts
        },
        "models": {
            item.id: _model_config_snapshot_from_manifest(session, project, item)
            for item in manifest.models
        },
        "evaluators": {
            item.id: _evaluator_snapshot_from_manifest(session, project, item)
            for item in manifest.evaluation.evaluators
        },
    }


def _create_runs_from_preview(
    session: Session,
    *,
    experiment: Experiment,
    preview: ManifestPreviewResponse,
    snapshots: dict[str, dict[str, Any]],
) -> None:
    for logical_run in preview.runs:
        case_snapshot = snapshots["cases"][logical_run.case_id]
        model_config_snapshot = snapshots["models"][logical_run.model_config_id]
        system_prompt_snapshot = snapshots["system_prompts"][logical_run.system_prompt_id]
        warmer_snapshot = snapshots["warmers"][logical_run.warmer_id]
        model_input_snapshot = build_model_input_snapshot(
            case_snapshot=case_snapshot,
            system_prompt_snapshot=system_prompt_snapshot,
            warmer_snapshot=warmer_snapshot,
            artifact_snapshots=snapshots["artifacts"],
        )
        run = Run(
            experiment_id=experiment.id,
            run_id=logical_run.run_id,
            case_slug=logical_run.case_id,
            model_config_slug=logical_run.model_config_id,
            system_prompt_slug=logical_run.system_prompt_id,
            warmer_slug=logical_run.warmer_id,
            model_input_snapshot=model_input_snapshot,
            run_snapshot={
                "case": case_snapshot,
                "model_config": model_config_snapshot,
                "system_prompt": system_prompt_snapshot,
                "warmer": warmer_snapshot,
                "artifacts": snapshots["artifacts"],
                "model_input_snapshot": model_input_snapshot,
            },
        )
        session.add(run)
        session.flush()
        for attempt in logical_run.attempts:
            record_run_attempt(
                session,
                run=run,
                attempt_id=attempt.attempt_id,
                replicate_index=attempt.replicate_index,
                replicate_group_id=attempt.replicate_group_id,
                attempt_kind=attempt.attempt_kind,
            )


def record_run_attempt(
    session: Session,
    *,
    run: Run,
    attempt_id: str,
    replicate_index: int,
    replicate_group_id: str | None = None,
    attempt_kind: str = "replicate",
    provider: str | None = None,
    model: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    provider_response_id: str | None = None,
    provider_timestamp: datetime | None = None,
    pricing_snapshot: dict[str, Any] | None = None,
    provider_metadata: dict[str, Any] | None = None,
    system_fingerprint: str | None = None,
    status: str = "queued",
    error_message: str | None = None,
    error_kind: str | None = None,
    terminal_failure_reason: str | None = None,
    attempt_number: int = 1,
    parent_attempt_id: str | None = None,
    retry_after_seconds: int = 0,
    available_at: datetime | None = None,
    cache_key: str | None = None,
    cache_hit: bool = False,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cost_usd: float | None = None,
) -> RunAttempt:
    attempt = RunAttempt(
        run=run,
        attempt_id=attempt_id,
        replicate_index=replicate_index,
        replicate_group_id=replicate_group_id or _replicate_group_id(run),
        attempt_kind=attempt_kind,
        provider=provider,
        model=model,
        request_payload=sanitize_provider_params(request_payload or {}),
        response_payload=sanitize_provider_params(response_payload or {}),
        provider_response_id=provider_response_id,
        provider_timestamp=provider_timestamp,
        pricing_snapshot=sanitize_provider_params(pricing_snapshot or {}),
        provider_metadata=sanitize_provider_params(provider_metadata or {}),
        system_fingerprint=system_fingerprint,
        status=status,
        error_message=error_message,
        error_kind=error_kind,
        terminal_failure_reason=terminal_failure_reason,
        attempt_number=attempt_number,
        parent_attempt_id=parent_attempt_id,
        retry_after_seconds=retry_after_seconds,
        available_at=available_at,
        cache_key=cache_key,
        cache_hit=cache_hit,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )
    session.add(attempt)
    return attempt


def _replicate_group_id(run: Run) -> str:
    payload = {
        "case_id": run.case_slug,
        "model_config_id": run.model_config_slug,
        "system_prompt_id": run.system_prompt_slug,
        "warmer_id": run.warmer_slug,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"group_{hashlib.sha256(encoded).hexdigest()[:16]}"


def record_score(
    session: Session,
    *,
    run_attempt: RunAttempt,
    type: str,
    evaluator_type: str,
    criterion: str,
    value: dict[str, Any],
    explanation: str | None = None,
    confidence: float | None = None,
    evaluator_version: int | None = None,
) -> Score:
    score = Score(
        run_attempt=run_attempt,
        type=type,
        evaluator_type=evaluator_type,
        criterion=criterion,
        value=value,
        explanation=explanation,
        confidence=confidence,
        evaluator_version=evaluator_version,
    )
    session.add(score)
    return score


def create_review_set(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    experiment: Experiment | None = None,
    review_type: str = "blind",
    metadata: dict[str, Any] | None = None,
) -> ReviewSet:
    session.flush()
    if experiment is not None and experiment.project_id != project.id:
        raise ValueError("Review set experiment must belong to the selected project.")
    review_set = ReviewSet(
        project_id=project.id,
        experiment_id=experiment.id if experiment else None,
        slug=slug,
        name=name,
        review_type=review_type,
        metadata_json=metadata or {},
    )
    session.add(review_set)
    session.flush()
    record_audit_event(
        session,
        project=project,
        experiment=experiment,
        event_kind="review_set_created",
        entity_type="review_set",
        entity_id=str(review_set.id),
        details={"slug": review_set.slug, "review_type": review_set.review_type},
    )
    return review_set


def create_reviewer(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    email: str | None = None,
) -> Reviewer:
    reviewer = session.scalar(
        select(Reviewer).where(Reviewer.project_id == project.id, Reviewer.slug == slug)
    )
    if reviewer is not None:
        return reviewer
    reviewer = Reviewer(project_id=project.id, slug=slug, name=name, email=email)
    session.add(reviewer)
    return reviewer


def list_reviewers(session: Session, *, project: Project) -> list[Reviewer]:
    return session.scalars(
        select(Reviewer).where(Reviewer.project_id == project.id).order_by(Reviewer.slug)
    ).all()


def create_failure_taxonomy(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    tags: list[str],
    version: int = 1,
    archived: bool = False,
) -> FailureTaxonomy:
    taxonomy = session.scalar(
        select(FailureTaxonomy).where(
            FailureTaxonomy.project_id == project.id,
            FailureTaxonomy.slug == slug,
            FailureTaxonomy.version == version,
        )
    )
    if taxonomy is not None:
        raise ValueError(f"Failure taxonomy '{slug}' version {version} already exists.")
    taxonomy = FailureTaxonomy(
        project_id=project.id,
        slug=slug,
        name=name,
        tags=list(tags),
        version=version,
        archived=archived,
    )
    session.add(taxonomy)
    return taxonomy


def list_failure_taxonomies(session: Session, *, project: Project) -> list[FailureTaxonomy]:
    return session.scalars(
        select(FailureTaxonomy)
        .where(FailureTaxonomy.project_id == project.id)
        .order_by(FailureTaxonomy.slug, FailureTaxonomy.version)
    ).all()


def _seed_failure_taxonomy(session: Session, *, project: Project) -> FailureTaxonomy:
    taxonomy = session.scalar(
        select(FailureTaxonomy).where(
            FailureTaxonomy.project_id == project.id,
            FailureTaxonomy.slug == "copper-memo-defaults",
            FailureTaxonomy.version == 1,
        )
    )
    if taxonomy is not None:
        return taxonomy
    taxonomy = create_failure_taxonomy(
        session,
        project=project,
        slug="copper-memo-defaults",
        name="Copper memo defaults",
        tags=DEFAULT_COPPER_FAILURE_TAGS,
        version=1,
    )
    session.flush()
    return taxonomy


def _failure_taxonomy_by_slug(
    session: Session,
    *,
    project: Project,
    slug: str | None,
) -> FailureTaxonomy:
    if slug is None:
        return _seed_failure_taxonomy(session, project=project)
    taxonomy = session.scalar(
        select(FailureTaxonomy)
        .where(
            FailureTaxonomy.project_id == project.id,
            FailureTaxonomy.slug == slug,
            FailureTaxonomy.archived.is_(False),
        )
        .order_by(FailureTaxonomy.version.desc(), FailureTaxonomy.id.desc())
    )
    if taxonomy is None:
        raise ValueError(f"Failure taxonomy '{slug}' does not exist.")
    return taxonomy


def create_review_item(
    session: Session,
    *,
    review_set: ReviewSet,
    item_key: str,
    run_attempt: RunAttempt | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
    answer_snapshot: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReviewItem:
    session.flush()
    if run_attempt is not None:
        attempt_experiment_id = run_attempt.run.experiment_id
        if review_set.experiment_id is not None and review_set.experiment_id != attempt_experiment_id:
            raise ValueError("Review item attempt must belong to the review set experiment.")
        if review_set.project_id != run_attempt.run.experiment.project_id:
            raise ValueError("Review item attempt must belong to the review set project.")

    item = ReviewItem(
        review_set=review_set,
        run_attempt=run_attempt,
        item_key=item_key,
        prompt_snapshot=prompt_snapshot or {},
        answer_snapshot=answer_snapshot or {},
        metadata_json=metadata or {},
        reviewer_decision={},
    )
    session.add(item)
    return item


def create_review_set_from_completed_experiment(
    session: Session,
    *,
    project: Project,
    experiment: Experiment,
    slug: str,
    name: str,
    random_seed: int | None = None,
    failure_tags: list[str] | None = None,
    failure_taxonomy_slug: str | None = None,
    reviewer_slugs: list[str] | None = None,
) -> ReviewSet:
    session.flush()
    if experiment.project_id != project.id:
        raise ValueError("Review set experiment must belong to the selected project.")
    if experiment.status != "complete":
        raise ValueError("Review sets can only be created from completed experiments.")
    taxonomy = _failure_taxonomy_by_slug(
        session,
        project=project,
        slug=failure_taxonomy_slug,
    )
    taxonomy_snapshot = dict(taxonomy.snapshot or {})
    selected_failure_tags = (
        list(failure_tags)
        if failure_tags is not None
        else list(taxonomy_snapshot.get("tags") or [])
    )
    review_set = create_review_set(
        session,
        project=project,
        experiment=experiment,
        slug=slug,
        name=name,
        review_type="blind_pairwise",
        metadata={
            "blind": True,
            "failure_tags": selected_failure_tags,
            "failure_taxonomy": taxonomy_snapshot,
            "random_seed": random_seed,
            "source_experiment_id": experiment.id,
        },
    )
    rng = random.Random(random_seed)
    item_number = 1
    for group_key, attempts in _pairwise_attempt_groups(experiment).items():
        for pair_index, pair in enumerate(combinations(attempts, 2), start=1):
            ordered = list(pair)
            rng.shuffle(ordered)
            answers = [
                {
                    "label": chr(ord("A") + index),
                    "run_attempt_id": attempt.id,
                    "text": _attempt_output_text(attempt),
                }
                for index, attempt in enumerate(ordered)
            ]
            create_review_item(
                session,
                review_set=review_set,
                item_key=f"pair_{item_number:04d}",
                prompt_snapshot={
                    "case_slug": group_key[0],
                    "replicate_index": int(group_key[3]),
                },
                answer_snapshot={"answers": answers},
                metadata={
                    "blind": True,
                    "answer_order": [attempt.id for attempt in ordered],
                    "group": {
                        "case_slug": group_key[0],
                        "system_prompt_slug": group_key[1],
                        "warmer_slug": group_key[2],
                        "replicate_index": int(group_key[3]),
                        "pair_index": pair_index,
                    },
                    "reveal_metadata": {
                        "answers": [
                            {
                                "label": answers[index]["label"],
                                "run_attempt_id": attempt.id,
                                "model_config_slug": attempt.run.model_config_slug,
                                "system_prompt_slug": attempt.run.system_prompt_slug,
                                "warmer_slug": attempt.run.warmer_slug,
                                "case_slug": attempt.run.case_slug,
                                "cost_usd": attempt.cost_usd,
                                "input_tokens": attempt.input_tokens,
                                "output_tokens": attempt.output_tokens,
                                "total_tokens": attempt.total_tokens,
                            }
                            for index, attempt in enumerate(ordered)
                        ]
                    },
                },
            )
            item_number += 1
    if reviewer_slugs:
        create_review_assignments(
            session,
            review_set=review_set,
            reviewer_slugs=reviewer_slugs,
            taxonomy_snapshot=taxonomy_snapshot,
        )
    return review_set


def create_review_assignments(
    session: Session,
    *,
    review_set: ReviewSet,
    reviewer_slugs: list[str],
    taxonomy_snapshot: dict[str, Any] | None = None,
) -> list[ReviewAssignment]:
    session.flush()
    taxonomy = taxonomy_snapshot or _review_set_taxonomy_snapshot(review_set)
    unique_slugs = sorted(dict.fromkeys(reviewer_slugs))
    reviewers_by_slug = {
        reviewer.slug: reviewer
        for reviewer in session.scalars(
            select(Reviewer).where(
                Reviewer.project_id == review_set.project_id,
                Reviewer.slug.in_(unique_slugs),
            )
        ).all()
    }
    items = sorted(review_set.items, key=lambda item: item.id or 0)
    assignments: list[ReviewAssignment] = []
    for reviewer_slug in unique_slugs:
        reviewer = reviewers_by_slug.get(reviewer_slug)
        if reviewer is None:
            project = session.get(Project, review_set.project_id)
            if project is None:
                raise ValueError("Review set project does not exist.")
            reviewer = create_reviewer(
                session,
                project=project,
                slug=reviewer_slug,
                name=reviewer_slug,
            )
            session.flush()
            reviewers_by_slug[reviewer_slug] = reviewer

    reviewer_ids = [reviewer.id for reviewer in reviewers_by_slug.values() if reviewer.id is not None]
    item_ids = [item.id for item in items if item.id is not None]
    existing_assignments = {
        (assignment.review_item_id, assignment.reviewer_id): assignment
        for assignment in session.scalars(
            select(ReviewAssignment).where(
                ReviewAssignment.review_set_id == review_set.id,
                ReviewAssignment.review_item_id.in_(item_ids),
                ReviewAssignment.reviewer_id.in_(reviewer_ids),
            )
        ).all()
    }
    for reviewer_slug in unique_slugs:
        reviewer = reviewers_by_slug[reviewer_slug]
        for item in items:
            existing = existing_assignments.get((item.id, reviewer.id))
            if existing is not None:
                assignments.append(existing)
                continue
            assignment = ReviewAssignment(
                review_set=review_set,
                review_item=item,
                reviewer=reviewer,
                status="pending",
                taxonomy_snapshot=dict(taxonomy),
                decision_snapshot={},
            )
            session.add(assignment)
            assignments.append(assignment)
            existing_assignments[(item.id, reviewer.id)] = assignment
    return assignments


def get_reviewer_queue(
    session: Session,
    *,
    review_set: ReviewSet,
    reviewer_slug: str,
) -> dict[str, Any]:
    reviewer = session.scalar(
        select(Reviewer).where(
            Reviewer.project_id == review_set.project_id,
            Reviewer.slug == reviewer_slug,
        )
    )
    if reviewer is None:
        raise ValueError(f"Reviewer '{reviewer_slug}' does not exist.")
    assignments = session.scalars(
        select(ReviewAssignment)
        .options(selectinload(ReviewAssignment.review_item))
        .where(
            ReviewAssignment.review_set_id == review_set.id,
            ReviewAssignment.reviewer_id == reviewer.id,
        )
        .order_by(ReviewAssignment.id)
    ).all()
    return {
        "review_set": {
            "id": review_set.id,
            "slug": review_set.slug,
            "name": review_set.name,
            "review_type": review_set.review_type,
        },
        "reviewer": _reviewer_payload(reviewer),
        "failure_taxonomy": _review_set_taxonomy_snapshot(review_set),
        "progress": _assignment_progress(assignments),
        "items": [_assignment_queue_item(assignment) for assignment in assignments],
    }


def record_assignment_decision(
    session: Session,
    *,
    assignment: ReviewAssignment,
    winner: str,
    pass_fail: dict[str, bool],
    failure_tags: dict[str, list[str]],
    rubric_notes: dict[str, str],
    notes: str | None = None,
    confidence: float | None = None,
) -> ReviewAssignment:
    reviewer_slug = assignment.reviewer.slug
    allowed_failure_tags = [
        str(tag) for tag in (assignment.taxonomy_snapshot or {}).get("tags") or []
    ]
    if not allowed_failure_tags:
        allowed_failure_tags = [
            str(tag) for tag in _review_set_taxonomy_snapshot(assignment.review_set).get("tags") or []
        ]
    review_item = record_review_decision(
        session,
        review_item=assignment.review_item,
        reviewer_id=reviewer_slug,
        winner=winner,
        pass_fail=pass_fail,
        failure_tags=failure_tags,
        rubric_notes=rubric_notes,
        notes=notes,
        confidence=confidence,
        allowed_failure_tags=allowed_failure_tags,
    )
    decision = dict(review_item.reviewer_decision or {})
    decision["assignment_id"] = assignment.id
    decision["taxonomy_snapshot"] = dict(assignment.taxonomy_snapshot or {})
    assignment.decision_snapshot = decision
    assignment.status = "submitted"
    assignment.submitted_at = utc_now()
    session.flush()
    _stamp_assignment_id_on_scores(
        session,
        review_item=assignment.review_item,
        reviewer_id=reviewer_slug,
        assignment_id=assignment.id,
        taxonomy_snapshot=assignment.taxonomy_snapshot,
    )
    return assignment


def _reviewer_payload(reviewer: Reviewer) -> dict[str, Any]:
    return {
        "id": reviewer.id,
        "slug": reviewer.slug,
        "name": reviewer.name,
        "email": reviewer.email,
    }


def _assignment_progress(assignments: list[ReviewAssignment]) -> dict[str, int]:
    submitted = sum(1 for assignment in assignments if assignment.status == "submitted")
    return {
        "assigned": len(assignments),
        "submitted": submitted,
        "pending": len(assignments) - submitted,
    }


def _review_set_taxonomy_snapshot(review_set: ReviewSet) -> dict[str, Any]:
    metadata = dict(review_set.metadata_json or {})
    taxonomy = dict(metadata.get("failure_taxonomy") or {})
    if not taxonomy.get("tags"):
        failure_tags = metadata.get("failure_tags")
        if isinstance(failure_tags, list):
            taxonomy["tags"] = [str(tag) for tag in failure_tags]
    return taxonomy


def _assignment_queue_item(assignment: ReviewAssignment) -> dict[str, Any]:
    item = assignment.review_item
    return {
        "assignment_id": assignment.id,
        "assignment_status": assignment.status,
        "id": item.id,
        "item_key": f"review-item-{item.id}",
        "prompt": dict(item.prompt_snapshot or {}),
        "answers": [
            {"label": answer.get("label"), "text": answer.get("text", "")}
            for answer in (item.answer_snapshot or {}).get("answers") or []
        ],
        "reviewer_decision": dict(assignment.decision_snapshot or {}),
    }


def _stamp_assignment_id_on_scores(
    session: Session,
    *,
    review_item: ReviewItem,
    reviewer_id: str,
    assignment_id: int,
    taxonomy_snapshot: dict[str, Any],
) -> None:
    attempt_ids = _review_item_attempt_ids(review_item)
    if not attempt_ids:
        return
    scores = session.scalars(
        select(Score).where(
            Score.run_attempt_id.in_(attempt_ids),
            Score.evaluator_type == "human",
        )
    ).all()
    taxonomy_version = taxonomy_snapshot.get("version")
    for score in scores:
        value = dict(score.value or {})
        if value.get("review_item_id") != review_item.id or value.get("reviewer_id") != reviewer_id:
            continue
        value["assignment_id"] = assignment_id
        if taxonomy_version is not None:
            value["taxonomy_version"] = taxonomy_version
        score.value = value


def record_review_decision(
    session: Session,
    *,
    review_item: ReviewItem,
    reviewer_id: str,
    winner: str,
    pass_fail: dict[str, bool],
    failure_tags: dict[str, list[str]],
    rubric_notes: dict[str, str],
    notes: str | None = None,
    confidence: float | None = None,
    evaluator_version: int = 1,
    allowed_failure_tags: list[str] | None = None,
) -> ReviewItem:
    session.flush()
    answers = list((review_item.answer_snapshot or {}).get("answers") or [])
    if allowed_failure_tags is None:
        configured_tags = (review_item.review_set.metadata_json or {}).get("failure_tags")
        if isinstance(configured_tags, list):
            allowed_failure_tags = [str(tag) for tag in configured_tags]
    _validate_review_labels(
        answers,
        winner=winner,
        pass_fail=pass_fail,
        failure_tags=failure_tags,
        rubric_notes=rubric_notes,
        allowed_failure_tags=allowed_failure_tags,
    )
    answer_order = [int(answer["run_attempt_id"]) for answer in answers]
    review_item.reviewer_decision = {
        "reviewer_id": reviewer_id,
        "winner": winner,
        "pass_fail": pass_fail,
        "failure_tags": failure_tags,
        "rubric_notes": rubric_notes,
        "notes": notes,
        "answer_order": answer_order,
    }
    _clear_existing_review_scores(session, review_item=review_item, reviewer_id=reviewer_id)
    for answer in answers:
        label = str(answer["label"])
        attempt = session.get(RunAttempt, int(answer["run_attempt_id"]))
        if attempt is None:
            raise ValueError(f"Run attempt {answer['run_attempt_id']} does not exist.")
        record_score(
            session,
            run_attempt=attempt,
            type="pairwise_preference",
            evaluator_type="human",
            criterion="blind_pairwise_preference",
            value={
                "label": label,
                "outcome": _pairwise_outcome(label, winner),
                "winner": winner,
                "review_item_id": review_item.id,
                "reviewer_id": reviewer_id,
            },
            confidence=confidence,
            evaluator_version=evaluator_version,
        )
    for answer in answers:
        label = str(answer["label"])
        if label not in pass_fail:
            continue
        attempt = session.get(RunAttempt, int(answer["run_attempt_id"]))
        if attempt is None:
            continue
        record_score(
            session,
            run_attempt=attempt,
            type="pass_fail",
            evaluator_type="human",
            criterion="blind_pairwise_pass_fail",
            value={
                "label": label,
                "passed": bool(pass_fail[label]),
                "review_item_id": review_item.id,
                "reviewer_id": reviewer_id,
            },
            confidence=confidence,
            evaluator_version=evaluator_version,
        )
    for label, tags in failure_tags.items():
        if not tags:
            continue
        attempt = _attempt_for_label(session, answers, label)
        if attempt is None:
            continue
        record_score(
            session,
            run_attempt=attempt,
            type="failure_tags",
            evaluator_type="human",
            criterion="blind_pairwise_failure_tags",
            value={
                "label": label,
                "tags": list(tags),
                "review_item_id": review_item.id,
                "reviewer_id": reviewer_id,
            },
            confidence=confidence,
            evaluator_version=evaluator_version,
        )
    for label, note in rubric_notes.items():
        attempt = _attempt_for_label(session, answers, label)
        if attempt is None:
            continue
        record_score(
            session,
            run_attempt=attempt,
            type="rubric_notes",
            evaluator_type="human",
            criterion="blind_pairwise_rubric_notes",
            value={
                "label": label,
                "note": note,
                "review_item_id": review_item.id,
                "reviewer_id": reviewer_id,
            },
            explanation=note,
            confidence=confidence,
            evaluator_version=evaluator_version,
        )
    if notes:
        for answer in answers:
            label = str(answer["label"])
            attempt = session.get(RunAttempt, int(answer["run_attempt_id"]))
            if attempt is None:
                continue
            record_score(
                session,
                run_attempt=attempt,
                type="freeform_notes",
                evaluator_type="human",
                criterion="blind_pairwise_freeform_notes",
                value={
                    "label": label,
                    "note": notes,
                    "review_item_id": review_item.id,
                    "reviewer_id": reviewer_id,
                },
                explanation=notes,
                confidence=confidence,
                evaluator_version=evaluator_version,
            )
    record_audit_event(
        session,
        project=session.get(Project, review_item.review_set.project_id),
        experiment=(
            session.get(Experiment, review_item.review_set.experiment_id)
            if review_item.review_set.experiment_id is not None
            else None
        ),
        event_kind="review_decision_recorded",
        entity_type="review_item",
        entity_id=str(review_item.id),
        details={
            "review_set_id": review_item.review_set_id,
            "reviewer_id": reviewer_id,
            "winner": winner,
            "answer_count": len(answers),
        },
    )
    return review_item


def pairwise_aggregation_inputs(
    session: Session, *, review_set: ReviewSet
) -> list[dict[str, Any]]:
    review_item_ids = {item.id for item in review_set.items}
    attempt_ids = _review_set_attempt_ids(review_set)
    if not attempt_ids:
        return []
    scores = session.scalars(
        select(Score)
        .where(
            Score.type == "pairwise_preference",
            Score.run_attempt_id.in_(attempt_ids),
        )
        .order_by(Score.id)
    ).all()
    inputs: list[dict[str, Any]] = []
    for score in scores:
        review_item_id = score.value.get("review_item_id")
        if review_item_id not in review_item_ids:
            continue
        run = score.run_attempt.run
        inputs.append(
            {
                "review_set_id": review_set.id,
                "review_item_id": review_item_id,
                "score_id": score.id,
                "score_type": score.type,
                "run_attempt_id": score.run_attempt_id,
                "run_id": run.id,
                "case_slug": run.case_slug,
                "model_config_slug": run.model_config_slug,
                "system_prompt_slug": run.system_prompt_slug,
                "warmer_slug": run.warmer_slug,
                "label": score.value.get("label"),
                "outcome": score.value.get("outcome"),
                "winner": score.value.get("winner"),
            }
        )
    return inputs


def _pairwise_attempt_groups(experiment: Experiment) -> dict[tuple[str, str, str, str], list[RunAttempt]]:
    groups: dict[tuple[str, str, str, str], list[RunAttempt]] = {}
    for run in sorted(experiment.runs, key=lambda item: item.run_id):
        for attempt in _latest_succeeded_attempts_by_replicate(run):
            key = (
                run.case_slug,
                run.system_prompt_slug,
                run.warmer_slug,
                str(attempt.replicate_index),
            )
            groups.setdefault(key, []).append(attempt)
    return {
        key: sorted(attempts, key=lambda attempt: attempt.run.model_config_slug)
        for key, attempts in groups.items()
        if len({attempt.run.model_config_slug for attempt in attempts}) >= 2
    }


def _latest_succeeded_attempts_by_replicate(run: Run) -> list[RunAttempt]:
    latest: dict[int, RunAttempt] = {}
    for attempt in run.attempts:
        if attempt.status != "succeeded":
            continue
        current = latest.get(attempt.replicate_index)
        if current is None or (attempt.attempt_number, attempt.id or 0) > (
            current.attempt_number,
            current.id or 0,
        ):
            latest[attempt.replicate_index] = attempt
    return [latest[key] for key in sorted(latest)]


def _attempt_output_text(attempt: RunAttempt) -> str:
    return _shared_attempt_output_text(attempt)


def _pairwise_outcome(label: str, winner: str) -> str:
    if winner in {"tie", "cannot_judge"}:
        return winner
    return "winner" if label == winner else "loser"


def _attempt_for_label(
    session: Session, answers: list[dict[str, Any]], label: str
) -> RunAttempt | None:
    for answer in answers:
        if answer.get("label") == label:
            return session.get(RunAttempt, int(answer["run_attempt_id"]))
    return None


def _validate_review_labels(
    answers: list[dict[str, Any]],
    *,
    winner: str,
    pass_fail: dict[str, bool],
    failure_tags: dict[str, list[str]],
    rubric_notes: dict[str, str],
    allowed_failure_tags: list[str] | None = None,
) -> None:
    labels = {str(answer["label"]) for answer in answers}
    provided_labels = set(pass_fail) | set(failure_tags) | set(rubric_notes)
    if winner not in {"tie", "cannot_judge"}:
        provided_labels.add(winner)
    unknown_labels = sorted(provided_labels - labels)
    if unknown_labels:
        raise ValueError(
            "Review decision includes unknown answer labels: " + ", ".join(unknown_labels)
        )
    if allowed_failure_tags is not None:
        allowed_tags = set(allowed_failure_tags)
        provided_tags = {str(tag) for tags in failure_tags.values() for tag in tags}
        unknown_tags = sorted(provided_tags - allowed_tags)
        if unknown_tags:
            raise ValueError(
                "Review decision includes unknown failure tags: " + ", ".join(unknown_tags)
            )


def _review_item_attempt_ids(review_item: ReviewItem) -> list[int]:
    return [
        int(answer["run_attempt_id"])
        for answer in (review_item.answer_snapshot or {}).get("answers") or []
    ]


def _review_set_attempt_ids(review_set: ReviewSet) -> list[int]:
    attempt_ids: list[int] = []
    for item in review_set.items:
        attempt_ids.extend(_review_item_attempt_ids(item))
    return attempt_ids


def _clear_existing_review_scores(
    session: Session, *, review_item: ReviewItem, reviewer_id: str
) -> None:
    if review_item.id is None:
        return
    attempt_ids = _review_item_attempt_ids(review_item)
    if not attempt_ids:
        return
    scores = session.scalars(
        select(Score).where(
            Score.type.in_(HUMAN_REVIEW_SCORE_TYPES),
            Score.evaluator_type == "human",
            Score.run_attempt_id.in_(attempt_ids),
        )
    ).all()
    for score in scores:
        value = score.value or {}
        if value.get("review_item_id") == review_item.id and value.get("reviewer_id") == reviewer_id:
            session.delete(score)
    session.flush()


def snapshot_manifest(manifest: ExperimentManifest) -> dict[str, Any]:
    snapshot = manifest.model_dump(mode="json")
    for model in snapshot.get("models", []):
        if isinstance(model, dict):
            model.update(sanitize_provider_params(model))
    return snapshot


def snapshot_design(manifest: ExperimentManifest, effective_random_seed: int | None) -> dict[str, Any]:
    snapshot = manifest.design.model_dump(mode="json")
    if snapshot.get("randomize_run_order") and snapshot.get("random_seed") is None:
        snapshot["random_seed"] = effective_random_seed
    return snapshot


def _pricing_snapshot(model_config_snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    models = [
        (snapshot["provider"], snapshot["model"])
        for snapshot in model_config_snapshots.values()
        if isinstance(snapshot.get("provider"), str) and isinstance(snapshot.get("model"), str)
    ]
    return build_pricing_snapshot(models)


def _case_snapshot_from_manifest(
    session: Session, project: Project, item: CaseManifest
) -> dict[str, Any]:
    library_item = _by_slug(session, Case, project, item.id, item.version)
    if library_item:
        return snapshot_case(library_item)
    if not _has_inline_payload(item, "prompt", "prompt_ref"):
        raise _missing_reference_error("Case", project, item.id)
    snapshot = {
        "id": item.id,
        "name": item.id,
        "prompt": item.prompt,
        "prompt_ref": item.prompt_ref,
        "dataset_split": "dev",
        "version": item.version or 1,
        "archived": False,
    }
    variables = (item.model_extra or {}).get("variables")
    if isinstance(variables, dict):
        snapshot["variables"] = dict(variables)
    return snapshot


def _system_prompt_snapshot_from_manifest(
    session: Session, project: Project, item: SystemPromptManifest
) -> dict[str, Any]:
    library_item = _by_slug(session, SystemPrompt, project, item.id, item.version)
    if library_item:
        return snapshot_system_prompt(library_item)
    if not _has_inline_payload(item, "prompt", "prompt_ref", "messages"):
        raise _missing_reference_error("System prompt", project, item.id)
    return {
        "id": item.id,
        "name": item.id,
        "prompt": item.prompt,
        "prompt_ref": item.prompt_ref,
        "messages": item.messages or [],
        "version": item.version or 1,
        "archived": False,
    }


def _warmer_snapshot_from_manifest(
    session: Session, project: Project, item: WarmerManifest
) -> dict[str, Any]:
    library_item = _by_slug(session, ConversationWarmer, project, item.id, item.version)
    if library_item:
        return snapshot_conversation_warmer(library_item)
    if not _has_inline_payload(item, "prompt") and "messages" not in item.model_fields_set:
        raise _missing_reference_error("Conversation warmer", project, item.id)
    return {
        "id": item.id,
        "name": item.id,
        "domain": None,
        "user_level": None,
        "intent": item.prompt,
        "messages": item.messages or [],
        "tags": [],
        "version": item.version or 1,
        "archived": False,
    }


def _model_config_snapshot_from_manifest(
    session: Session, project: Project, item: ModelConfigManifest
) -> dict[str, Any]:
    library_item = _by_slug(session, ModelConfig, project, item.id, item.version)
    if library_item:
        return snapshot_model_config(library_item)
    if item.is_library_reference:
        raise _missing_reference_error("Model config", project, item.id)
    raw_params = item.raw_provider_params
    return {
        "id": item.id,
        "name": item.id,
        "provider": normalize_provider_name(item.provider or ""),
        "model": item.model,
        "temperature": item.temperature,
        "max_output_tokens": _int_param(raw_params, "max_output_tokens", "max_tokens"),
        "reasoning_level": item.reasoning_level,
        "capability_flags": {},
        "raw_provider_params": sanitize_provider_params(raw_params),
        "version": item.version or 1,
        "archived": False,
    }


def _evaluator_snapshot_from_manifest(
    session: Session, project: Project, item: EvaluatorManifest
) -> dict[str, Any]:
    library_item = _by_slug(session, Evaluator, project, item.id, item.version)
    if library_item:
        snapshot = snapshot_evaluator(library_item)
        if snapshot.get("type") == "llm_judge":
            return _llm_judge_evaluator_snapshot_from_definition(
                session,
                project,
                id=str(snapshot["id"]),
                name=str(snapshot.get("name") or snapshot["id"]),
                definition=snapshot.get("definition"),
                version=int(snapshot.get("version") or 1),
                archived=bool(snapshot.get("archived")),
            )
        return snapshot
    if not item.type and not item.definition:
        raise _missing_reference_error("Evaluator", project, item.id)
    if item.type == "llm_judge":
        return _llm_judge_evaluator_snapshot_from_definition(
            session,
            project,
            id=item.id,
            name=item.id,
            definition=item.definition,
            version=item.version or 1,
            archived=False,
        )
    return {
        "id": item.id,
        "name": item.id,
        "type": item.type,
        "definition": dict(item.definition),
        "version": item.version or 1,
        "archived": False,
    }


def _llm_judge_evaluator_snapshot_from_definition(
    session: Session,
    project: Project,
    *,
    id: str,
    name: str,
    definition: Any,
    version: int,
    archived: bool,
) -> dict[str, Any]:
    definition = dict(definition or {})
    judge_config_id, judge_config_version = _judge_config_ref_from_definition(definition)
    if judge_config_id:
        judge_config = _by_slug(session, LLMJudgeConfig, project, judge_config_id, judge_config_version)
        if judge_config is None:
            if _uses_legacy_criterion_ref(definition, judge_config_id):
                return {
                    "id": id,
                    "name": name,
                    "type": "llm_judge",
                    "definition": definition,
                    "version": version,
                    "archived": archived,
                }
            raise _missing_reference_error("LLM judge config", project, judge_config_id)
        definition["judge_config"] = snapshot_llm_judge_config(judge_config)
        return {
            "id": id,
            "name": name,
            "type": "llm_judge",
            "definition": definition,
            "version": version,
            "archived": archived,
        }

    judge_model_slug = definition.get("judge_model_config_id") or definition.get(
        "judge_model_config_slug"
    )
    if not isinstance(judge_model_slug, str) or not judge_model_slug.strip():
        raise ValueError(
            f"Inline LLM judge evaluator '{id}' must include judge_model_config_id."
        )
    model_config = _require_model_config_reference(
        session,
        project,
        judge_model_slug.strip(),
        _positive_int(definition.get("judge_model_config_version")),
    )
    output_schema = _validated_output_schema(definition.get("output_schema"))
    inline_config = _inline_llm_judge_config_snapshot(
        id=id,
        name=name,
        judge_prompt=_validated_judge_prompt(definition.get("judge_prompt")),
        rubric_dimensions=definition.get("rubric_dimensions"),
        output_schema=output_schema,
        model_config=model_config,
        raw_provider_params=definition.get("raw_provider_params"),
        calibration_status=str(definition.get("calibration_status") or "draft"),
        version=version,
    )
    definition["judge_config"] = inline_config
    return {
        "id": id,
        "name": name,
        "type": "llm_judge",
        "definition": definition,
        "version": version,
        "archived": archived,
    }


def _judge_config_ref_from_definition(definition: dict[str, Any]) -> tuple[str | None, int | None]:
    raw_ref = definition.get("judge_config_ref")
    raw_id = definition.get("judge_config_id")
    if isinstance(raw_ref, dict):
        slug = raw_ref.get("id") or raw_ref.get("slug")
        version = raw_ref.get("version")
    else:
        slug = raw_id or raw_ref or definition.get("criterion")
        version = definition.get("judge_config_version")
    if not isinstance(slug, str) or not slug.strip():
        return None, None
    return slug.strip(), _positive_int(version)


def _uses_legacy_criterion_ref(definition: dict[str, Any], judge_config_id: str) -> bool:
    return (
        "judge_config_id" not in definition
        and "judge_config_ref" not in definition
        and definition.get("criterion") == judge_config_id
    )


def _inline_llm_judge_config_snapshot(
    *,
    id: str,
    name: str,
    judge_prompt: str,
    rubric_dimensions: Any,
    output_schema: dict[str, Any],
    model_config: ModelConfig,
    raw_provider_params: Any,
    calibration_status: str,
    version: int,
) -> dict[str, Any]:
    dimensions = rubric_dimensions if isinstance(rubric_dimensions, list) else []
    params = raw_provider_params if isinstance(raw_provider_params, dict) else {}
    return {
        "id": id,
        "name": name,
        "judge_prompt": judge_prompt,
        "rubric_dimensions": dimensions,
        "output_schema": output_schema,
        "judge_model_config_ref": {"id": model_config.slug, "version": model_config.version},
        "raw_provider_params": sanitize_provider_params(params),
        "calibration_status": calibration_status,
        "version": version,
        "archived": False,
    }


def _artifact_snapshot_from_manifest(
    session: Session, project: Project, item: ArtifactManifest
) -> dict[str, Any]:
    library_item = _by_slug(session, Artifact, project, item.id, item.version)
    if library_item:
        snapshot = snapshot_artifact(library_item)
        target_input_mode = (
            item.input_mode.value if item.input_mode is not None else snapshot.get("input_mode")
        )
        if (
            target_input_mode in DERIVED_ARTIFACT_INPUT_MODE_VALUES
            and not _artifact_snapshot_has_derived_binding(snapshot)
        ):
            derived_artifact = _latest_derived_artifact_for_input_mode(
                session,
                project=project,
                source_artifact=library_item,
                input_mode=target_input_mode,
            )
            if derived_artifact is None:
                raise ValueError(
                    f"Artifact reference '{item.id}' selected derived input mode "
                    f"'{target_input_mode}' but no completed derived artifact exists. "
                    "Start preprocessing first or reference a derived artifact directly."
                )
            snapshot = snapshot_artifact(derived_artifact)
        elif item.input_mode is not None:
            snapshot = {**snapshot, "input_mode": target_input_mode}
        if item.metadata:
            snapshot_metadata = dict(snapshot.get("metadata") or {})
            item_metadata = dict(item.metadata)
            if _artifact_snapshot_has_derived_binding(snapshot):
                item_metadata = {
                    key: value
                    for key, value in item_metadata.items()
                    if key not in DERIVED_ARTIFACT_BINDING_METADATA_KEYS
                }
            snapshot = {
                **snapshot,
                "metadata": {**snapshot_metadata, **item_metadata},
            }
        return snapshot
    if not _has_inline_payload(
        item,
        "artifact_type",
        "uri",
        "filename",
        "checksum_sha256",
        "size_bytes",
        "mime_type",
        "storage_uri",
        "metadata",
    ):
        raise _missing_reference_error("Artifact", project, item.id)
    return {
        "id": item.id,
        "name": item.name or item.id,
        "artifact_type": item.artifact_type,
        "uri": item.uri,
        "input_mode": (
            item.input_mode.value if item.input_mode else ArtifactInputMode.DIRECT_FILE.value
        ),
        "filename": item.filename,
        "checksum_sha256": item.checksum_sha256,
        "size_bytes": item.size_bytes,
        "mime_type": item.mime_type,
        "storage_uri": item.storage_uri or item.uri,
        "image_width": item.image_width,
        "image_height": item.image_height,
        "created_at": None,
        "metadata": dict(item.metadata),
        "version": item.version or 1,
        "archived": False,
    }


DERIVED_ARTIFACT_BINDING_METADATA_KEYS = {
    "source_artifact_id",
    "source_checksum_sha256",
    "parser_name",
    "parser_version",
    "derived_artifact_id",
}


def _artifact_snapshot_has_derived_binding(snapshot: dict[str, Any]) -> bool:
    metadata = dict(snapshot.get("metadata") or {})
    return all(
        metadata.get(key) is not None
        for key in (
            "source_artifact_id",
            "source_checksum_sha256",
            "parser_name",
            "parser_version",
            "derived_artifact_id",
        )
    )


def _latest_derived_artifact_for_input_mode(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    input_mode: str,
) -> Artifact | None:
    runs = list_artifact_preprocessing_runs(
        session, project=project, source_artifact=source_artifact, status="completed"
    )
    for run in reversed(runs):
        for artifact_id in reversed(list(run.derived_artifact_ids or [])):
            artifact = session.get(Artifact, artifact_id)
            if (
                artifact is not None
                and artifact.project_id == project.id
                and artifact.input_mode == input_mode
            ):
                return artifact
    return None


def _benchmark_suite_items(
    session: Session,
    *,
    project: Project,
    case_ids: list[str],
    model_config_ids: list[str],
    system_prompt_ids: list[str],
    warmer_ids: list[str],
    evaluator_ids: list[str],
) -> list[BenchmarkSuiteItem]:
    items: list[BenchmarkSuiteItem] = []
    for slug in _unique_members(case_ids):
        case = _require_suite_item(session, Case, project, slug, "Case")
        items.append(_benchmark_suite_item("case", case.slug, case.version, case.dataset_split, snapshot_case(case)))
    for slug in _unique_members(model_config_ids):
        model = _require_suite_item(session, ModelConfig, project, slug, "Model config")
        items.append(
            _benchmark_suite_item("model", model.slug, model.version, None, snapshot_model_config(model))
        )
    for slug in _unique_members(system_prompt_ids):
        prompt = _require_suite_item(session, SystemPrompt, project, slug, "System prompt")
        items.append(
            _benchmark_suite_item(
                "system_prompt", prompt.slug, prompt.version, None, snapshot_system_prompt(prompt)
            )
        )
    for slug in _unique_members(warmer_ids):
        warmer = _require_suite_item(session, ConversationWarmer, project, slug, "Conversation warmer")
        items.append(
            _benchmark_suite_item(
                "warmer", warmer.slug, warmer.version, None, snapshot_conversation_warmer(warmer)
            )
        )
    for slug in _unique_members(evaluator_ids):
        evaluator = _require_suite_item(session, Evaluator, project, slug, "Evaluator")
        items.append(
            _benchmark_suite_item(
                "evaluator", evaluator.slug, evaluator.version, None, snapshot_evaluator(evaluator)
            )
        )
    return items


def _unique_members(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        slug = value.strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        ordered.append(slug)
    return ordered


def _require_suite_item(
    session: Session,
    model: type[Any],
    project: Project,
    slug: str,
    label: str,
) -> Any:
    item = _by_slug(session, model, project, slug)
    if item is None:
        raise _missing_reference_error(label, project, slug)
    return item


def _benchmark_suite_item(
    item_type: str,
    slug: str,
    version: int,
    split: str | None,
    snapshot: dict[str, Any],
) -> BenchmarkSuiteItem:
    return BenchmarkSuiteItem(
        item_type=item_type,
        item_slug=slug,
        item_version=version,
        item_split=split,
        snapshot_json=sanitize_provider_params(snapshot),
    )


def _suite_items(suite: BenchmarkSuite, item_type: str) -> list[BenchmarkSuiteItem]:
    if item_type not in BENCHMARK_ITEM_TYPES:
        raise ValueError(f"Unknown benchmark suite item type '{item_type}'.")
    return sorted(
        [item for item in suite.items if item.item_type == item_type],
        key=lambda item: (item.item_slug, item.item_version, item.id or 0),
    )


def _include_case_suite_item(item: BenchmarkSuiteItem, *, split: str | None) -> bool:
    snapshot = dict(item.snapshot_json or {})
    item_split = normalize_dataset_split(item.item_split or snapshot.get("dataset_split") or "dev")
    if snapshot.get("archived") or item_split == "archived":
        return False
    return split is None or item_split == split


def _suite_item_payload(item: BenchmarkSuiteItem) -> dict[str, Any]:
    payload = {
        "id": item.item_slug,
        "version": item.item_version,
        "split": item.item_split,
    }
    payload.update(dict(item.snapshot_json or {}))
    return payload


def _by_slug(
    session: Session, model: type[Any], project: Project, slug: str, version: int | None = None
) -> Any | None:
    query = select(model).where(model.project_id == project.id, model.slug == slug)
    if version is not None:
        query = query.where(model.version == version)
    else:
        query = query.order_by(model.version.desc())
    return session.scalar(query.limit(1))


def _has_inline_payload(item: IdObject, *field_names: str) -> bool:
    for field_name in field_names:
        if field_name not in item.model_fields_set:
            continue
        value = getattr(item, field_name)
        if value not in (None, "", [], {}):
            return True
    return False


def _missing_reference_error(entity: str, project: Project, slug: str) -> ValueError:
    return ValueError(f"{entity} reference '{slug}' does not exist in project '{project.slug}'.")


def _require_model_config_reference(
    session: Session, project: Project, slug: str, version: int | None
) -> ModelConfig:
    model_config = _by_slug(session, ModelConfig, project, slug, version)
    if model_config is None:
        raise _missing_reference_error("Model config", project, slug)
    return model_config


def _validated_output_schema(output_schema: dict[str, Any] | Any | None) -> dict[str, Any]:
    if not isinstance(output_schema, dict) or output_schema.get("type") != "object":
        raise ValueError("Output schema must be a JSON object schema.")
    return dict(output_schema)


def _validated_json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return dict(value)


def _normalize_metric_adapter_inputs(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("Metric adapter config must include required_inputs.")
    normalized: list[str] = []
    for value in values:
        input_name = _normalized_identifier(value, "required input")
        if input_name not in METRIC_ADAPTER_INPUT_FIELDS:
            raise ValueError(f"Unsupported metric adapter input: {value}")
        if input_name not in normalized:
            normalized.append(input_name)
    return normalized


def _validated_metric_adapter_kind(value: Any, *, local_only: bool) -> str:
    kind = normalize_metric_adapter_kind(_normalized_identifier(value, "adapter_kind"))
    if local_only:
        get_metric_adapter(kind)
    return kind


def _validated_adapter_version(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Metric adapter adapter_version must be a non-empty string.")
    return value.strip()


def _normalized_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Metric adapter {field_name} must be a non-empty string.")
    return value.strip().lower()


def _validated_judge_prompt(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("LLM judge config must include judge_prompt.")
    return value


def _float_param(params: dict[str, Any], key: str) -> float | None:
    value = params.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int_param(params: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = params.get(key)
        if type(value) is int:
            return value
    return None


def _positive_int(value: Any) -> int | None:
    if type(value) is int and value >= 1:
        return value
    return None


def _reasoning_level(params: dict[str, Any]) -> str | None:
    for key in ("reasoning_effort", "reasoning_level", "thinking_budget"):
        value = params.get(key)
        if isinstance(value, str):
            return value
    return None


def normalize_provider_name(value: str) -> str:
    return value.strip().lower()


def normalize_dataset_split(value: str | None) -> str:
    split = (value or "dev").strip().lower()
    if split not in DATASET_SPLITS:
        raise ValueError("Dataset split must be one of: archived, dev, holdout, validation.")
    return split


def normalize_provider_policy_list(values: list[str] | None) -> list[str]:
    return sorted({normalize_provider_name(value) for value in values or [] if value.strip()})


def _audit_details(details: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = {
        "request_payload",
        "response_payload",
        "messages",
        "final_messages",
        "prompt",
        "prompt_text",
        "manifest",
        "manifest_snapshot",
        "artifact_inputs",
    }

    def is_sensitive_audit_key(key: str) -> bool:
        normalized = key.lower()
        if normalized in {
            "cache_key",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "estimated_tokens",
            "budget_tokens",
        }:
            return False
        return (
            "authorization" in normalized
            or "password" in normalized
            or "secret" in normalized
            or "header" in normalized
            or any(part in normalized for part in {"key", "token"})
        )

    def sanitize_mapping(value: dict[str, Any], *, drop_blocked: bool) -> dict[str, Any]:
        sanitized = {}
        for key, item in value.items():
            if is_sensitive_audit_key(key):
                sanitized[key] = "[redacted]"
                continue
            if key in blocked_keys:
                if not drop_blocked:
                    sanitized[key] = "[redacted]"
                continue
            sanitized[key] = sanitize_detail(item)
        return sanitized

    def sanitize_detail(value: Any) -> Any:
        if isinstance(value, dict):
            return sanitize_mapping(value, drop_blocked=False)
        if isinstance(value, list):
            return [sanitize_detail(item) for item in value]
        return sanitize_provider_params(value)

    return sanitize_mapping(details, drop_blocked=True)


def _artifact_checksum_key(artifact: Artifact) -> str:
    return f"{artifact.slug}@v{artifact.version}"
