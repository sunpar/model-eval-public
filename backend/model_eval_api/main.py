from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from model_eval_api import artifacts as artifact_processing
from model_eval_api.executor import cancel_experiment, cancel_run, create_retry_attempt_for_run
from model_eval_api.execution_states import ExperimentStatus
from model_eval_api.headless import export_experiment_response
from model_eval_api.llm_judges import run_llm_judge
from model_eval_api.manifest import (
    ManifestPreviewResponse,
    ManifestValidationResult,
    ManifestValidationError,
    expand_manifest,
    parse_manifest,
    validate_manifest_payload,
)
from model_eval_api.metric_adapter_execution import run_metric_adapters_for_experiment
from model_eval_api.persistence import repositories
from model_eval_api.persistence.database import get_session
from model_eval_api.persistence.models import (
    AuditLog,
    Artifact,
    BenchmarkSuite,
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
    SystemPrompt,
    Workspace,
)
from model_eval_api.persistence.snapshots import sanitize_preprocessing_error_metadata
from model_eval_api.promptfoo import persist_promptfoo_import, preview_promptfoo_import_content
from model_eval_api.queue import enqueue_experiment_execution
from model_eval_api.results_analytics import aggregate_experiment_results
from model_eval_api.schemas import (
    ArtifactCreate,
    ArtifactInputModeUpdate,
    ArtifactPreprocessingCreate,
    BenchmarkSuiteCreate,
    CaseCreate,
    ConversationWarmerCreate,
    EvaluatorCreate,
    HealthResponse,
    JudgeRunCreate,
    LLMJudgeConfigCreate,
    MetricAdapterConfigCreate,
    MetricAdapterRunCreate,
    ModelConfigCreate,
    PromptfooImportPreviewCreate,
    ProjectProviderPolicyUpdate,
    FailureTaxonomyCreate,
    ReviewAssignmentCreate,
    ReviewDecisionCreate,
    ReviewerCreate,
    ReviewSetCreate,
    SystemPromptCreate,
)

app = FastAPI(
    title="Model Eval API",
    description="Workbench API for conversation-context model evaluations.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/manifests/preview", response_model=ManifestPreviewResponse)
def preview_manifest(request: dict[str, Any]) -> ManifestPreviewResponse:
    try:
        manifest = parse_manifest(request)
        return expand_manifest(manifest)
    except ManifestValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors) from error


@app.post("/manifests/validate", response_model=ManifestValidationResult)
def validate_manifest(request: dict[str, Any]) -> ManifestValidationResult:
    return validate_manifest_payload(request)


@app.post("/projects/{project_slug}/imports/promptfoo/preview")
def preview_project_promptfoo_import(
    project_slug: str,
    request: PromptfooImportPreviewCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        preview = preview_promptfoo_import_content(request.content, source_name=project_slug)
        payload = preview.to_payload()
        if request.persist:
            project = _get_or_create_project(session, project_slug)
            payload["persisted"] = persist_promptfoo_import(
                session,
                project=project,
                preview=preview,
            )
            session.commit()
    except ManifestValidationError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=error.errors) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return payload


@app.get("/projects/{project_slug}/provider-policy")
def get_project_provider_policy(
    project_slug: str, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    return _project_provider_policy_payload(project)


@app.put("/projects/{project_slug}/provider-policy")
def update_project_provider_policy(
    project_slug: str,
    request: ProjectProviderPolicyUpdate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    project.provider_allow_list = repositories.normalize_provider_policy_list(
        request.provider_allow_list
    )
    project.provider_deny_list = repositories.normalize_provider_policy_list(
        request.provider_deny_list
    )
    repositories.record_audit_event(
        session,
        project=project,
        event_kind="project_provider_policy_updated",
        entity_type="project",
        entity_id=str(project.id),
        details={
            "provider_allow_list": project.provider_allow_list,
            "provider_deny_list": project.provider_deny_list,
        },
    )
    session.commit()
    return _project_provider_policy_payload(project)


@app.post("/projects/{project_slug}/reviewers", status_code=201)
def create_project_reviewer(
    project_slug: str,
    request: ReviewerCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    reviewer = repositories.create_reviewer(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        email=request.email,
    )
    _commit_or_conflict(session)
    return _reviewer_payload(reviewer)


@app.get("/projects/{project_slug}/reviewers")
def list_project_reviewers(
    project_slug: str,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [
        _reviewer_payload(reviewer)
        for reviewer in repositories.list_reviewers(session, project=project)
    ]


@app.post("/projects/{project_slug}/failure-taxonomies", status_code=201)
def create_project_failure_taxonomy(
    project_slug: str,
    request: FailureTaxonomyCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        taxonomy = repositories.create_failure_taxonomy(
            session,
            project=project,
            slug=request.slug,
            name=request.name,
            tags=request.tags,
            version=request.version,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _failure_taxonomy_payload(taxonomy)


@app.get("/projects/{project_slug}/failure-taxonomies")
def list_project_failure_taxonomies(
    project_slug: str,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [
        _failure_taxonomy_payload(taxonomy)
        for taxonomy in repositories.list_failure_taxonomies(session, project=project)
    ]


@app.get("/projects/{project_slug}/library/cases")
def list_library_cases(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [_case_payload(item) for item in _library_items(session, project, Case)]


@app.post("/projects/{project_slug}/library/cases", status_code=201)
def create_library_case(
    project_slug: str, request: CaseCreate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    item = repositories.create_case(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        prompt=request.prompt,
        prompt_ref=request.prompt_ref,
        dataset_split=request.dataset_split,
        version=request.version,
        archived=request.archived,
    )
    _commit_or_conflict(session)
    return _case_payload(item)


@app.get("/projects/{project_slug}/library/system-prompts")
def list_library_system_prompts(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [_system_prompt_payload(item) for item in _library_items(session, project, SystemPrompt)]


@app.post("/projects/{project_slug}/library/system-prompts", status_code=201)
def create_library_system_prompt(
    project_slug: str, request: SystemPromptCreate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    item = repositories.create_system_prompt(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        prompt=request.prompt,
        prompt_ref=request.prompt_ref,
        messages=request.messages,
        version=request.version,
        archived=request.archived,
    )
    _commit_or_conflict(session)
    return _system_prompt_payload(item)


@app.get("/projects/{project_slug}/library/warmers")
def list_library_warmers(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [
        _conversation_warmer_payload(item)
        for item in _library_items(session, project, ConversationWarmer)
    ]


@app.post("/projects/{project_slug}/library/warmers", status_code=201)
def create_library_warmer(
    project_slug: str, request: ConversationWarmerCreate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    item = repositories.create_conversation_warmer(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        domain=request.domain,
        user_level=request.user_level,
        intent=request.intent,
        messages=request.messages,
        tags=request.tags,
        version_note=request.version_note,
        version=request.version,
        archived=request.archived,
    )
    _commit_or_conflict(session)
    return _conversation_warmer_payload(item)


@app.get("/projects/{project_slug}/library/model-configs")
def list_library_model_configs(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [_model_config_payload(item) for item in _library_items(session, project, ModelConfig)]


@app.post("/projects/{project_slug}/library/model-configs", status_code=201)
def create_library_model_config(
    project_slug: str, request: ModelConfigCreate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    item = repositories.create_model_config(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        provider=request.provider,
        model=request.model,
        temperature=request.temperature,
        max_output_tokens=request.max_output_tokens,
        reasoning_level=request.reasoning_level,
        capability_flags=request.capability_flags,
        raw_provider_params=request.raw_provider_params,
        version=request.version,
        archived=request.archived,
    )
    _commit_or_conflict(session)
    return _model_config_payload(item)


@app.get("/projects/{project_slug}/library/evaluators")
def list_library_evaluators(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [_evaluator_payload(item) for item in _library_items(session, project, Evaluator)]


@app.post("/projects/{project_slug}/library/evaluators", status_code=201)
def create_library_evaluator(
    project_slug: str, request: EvaluatorCreate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    item = repositories.create_evaluator(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        evaluator_type=request.evaluator_type,
        definition=request.definition,
        version=request.version,
        archived=request.archived,
    )
    _commit_or_conflict(session)
    return _evaluator_payload(item)


@app.get("/projects/{project_slug}/library/llm-judge-configs")
def list_library_llm_judge_configs(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [
        _llm_judge_config_payload(item)
        for item in repositories.list_llm_judge_configs(session, project=project)
    ]


@app.post("/projects/{project_slug}/library/llm-judge-configs", status_code=201)
def create_library_llm_judge_config(
    project_slug: str,
    request: LLMJudgeConfigCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.create_llm_judge_config(
            session,
            project=project,
            slug=request.slug,
            name=request.name,
            judge_prompt=request.judge_prompt,
            rubric_dimensions=request.rubric_dimensions,
            output_schema=request.output_schema,
            judge_model_config_slug=request.judge_model_config_slug,
            judge_model_config_version=request.judge_model_config_version,
            raw_provider_params=request.raw_provider_params,
            calibration_status=request.calibration_status,
            version=request.version,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _llm_judge_config_payload(item)


@app.post("/projects/{project_slug}/library/llm-judge-configs/{slug}/versions", status_code=201)
def create_library_llm_judge_config_version(
    project_slug: str,
    slug: str,
    request: LLMJudgeConfigCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.create_llm_judge_config_version(
            session,
            project=project,
            slug=slug,
            name=request.name,
            judge_prompt=request.judge_prompt,
            rubric_dimensions=request.rubric_dimensions,
            output_schema=request.output_schema,
            judge_model_config_slug=request.judge_model_config_slug,
            judge_model_config_version=request.judge_model_config_version,
            raw_provider_params=request.raw_provider_params,
            calibration_status=request.calibration_status,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _llm_judge_config_payload(item)


@app.delete("/projects/{project_slug}/library/llm-judge-configs/{judge_config_id}")
def archive_library_llm_judge_config(
    project_slug: str,
    judge_config_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.archive_llm_judge_config_by_id(
            session, project=project, judge_config_id=judge_config_id
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _llm_judge_config_payload(item)


@app.get("/projects/{project_slug}/library/metric-adapter-configs")
def list_library_metric_adapter_configs(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [
        _metric_adapter_config_payload(item)
        for item in repositories.list_metric_adapter_configs(session, project=project)
    ]


@app.post("/projects/{project_slug}/library/metric-adapter-configs", status_code=201)
def create_library_metric_adapter_config(
    project_slug: str,
    request: MetricAdapterConfigCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.create_metric_adapter_config(
            session,
            project=project,
            slug=request.slug,
            name=request.name,
            adapter_kind=request.adapter_kind,
            adapter_version=request.adapter_version,
            required_inputs=request.required_inputs,
            output_schema=request.output_schema,
            capability_metadata=request.capability_metadata,
            local_only=request.local_only,
            version=request.version,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _metric_adapter_config_payload(item)


@app.post(
    "/projects/{project_slug}/library/metric-adapter-configs/{slug}/versions",
    status_code=201,
)
def create_library_metric_adapter_config_version(
    project_slug: str,
    slug: str,
    request: MetricAdapterConfigCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.create_metric_adapter_config_version(
            session,
            project=project,
            slug=slug,
            name=request.name,
            adapter_kind=request.adapter_kind,
            adapter_version=request.adapter_version,
            required_inputs=request.required_inputs,
            output_schema=request.output_schema,
            capability_metadata=request.capability_metadata,
            local_only=request.local_only,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _metric_adapter_config_payload(item)


@app.get("/projects/{project_slug}/library/benchmark-suites")
def list_library_benchmark_suites(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [
        _benchmark_suite_payload(item)
        for item in repositories.list_benchmark_suites(session, project=project)
    ]


@app.post("/projects/{project_slug}/library/benchmark-suites", status_code=201)
def create_library_benchmark_suite(
    project_slug: str,
    request: BenchmarkSuiteCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.create_benchmark_suite(
            session,
            project=project,
            slug=request.slug,
            name=request.name,
            description=request.description,
            case_ids=request.case_ids,
            model_config_ids=request.model_config_ids,
            system_prompt_ids=request.system_prompt_ids,
            warmer_ids=request.warmer_ids,
            evaluator_ids=request.evaluator_ids,
            controls=request.controls,
            version=request.version,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _benchmark_suite_payload(item)


@app.post("/projects/{project_slug}/library/benchmark-suites/{slug}/versions", status_code=201)
def create_library_benchmark_suite_version(
    project_slug: str,
    slug: str,
    request: BenchmarkSuiteCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.create_benchmark_suite_version(
            session,
            project=project,
            slug=slug,
            name=request.name,
            description=request.description,
            case_ids=request.case_ids,
            model_config_ids=request.model_config_ids,
            system_prompt_ids=request.system_prompt_ids,
            warmer_ids=request.warmer_ids,
            evaluator_ids=request.evaluator_ids,
            controls=request.controls,
            archived=request.archived,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _benchmark_suite_payload(item)


@app.delete("/projects/{project_slug}/library/benchmark-suites/{suite_id}")
def archive_library_benchmark_suite(
    project_slug: str,
    suite_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        item = repositories.archive_benchmark_suite_by_id(
            session, project=project, suite_id=suite_id
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _benchmark_suite_payload(item)


@app.get("/projects/{project_slug}/library/benchmark-suites/{suite_id}/preview")
def preview_library_benchmark_suite(
    project_slug: str,
    suite_id: int,
    split: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        suite = repositories.resolve_benchmark_suite(session, project=project, suite_ref=suite_id)
        result = repositories.preview_benchmark_suite(session, suite=suite, split=split)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _benchmark_suite_preview_payload(result)


@app.get("/projects/{project_slug}/library/artifacts")
def list_library_artifacts(
    project_slug: str, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    return [_artifact_payload(item) for item in _library_items(session, project, Artifact)]


@app.post("/projects/{project_slug}/library/artifacts", status_code=201)
def create_library_artifact(
    project_slug: str, request: ArtifactCreate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    item = repositories.create_artifact(
        session,
        project=project,
        slug=request.slug,
        name=request.name,
        artifact_type=request.artifact_type,
        uri=request.uri,
        input_mode=request.input_mode,
        filename=request.filename,
        checksum_sha256=request.checksum_sha256,
        size_bytes=request.size_bytes,
        mime_type=request.mime_type,
        storage_uri=request.storage_uri,
        image_width=request.image_width,
        image_height=request.image_height,
        metadata=request.metadata,
        version=request.version,
        archived=request.archived,
    )
    _commit_or_conflict(session)
    return _artifact_payload(item)


@app.patch("/projects/{project_slug}/library/artifacts/{artifact_ref}/input-mode")
def update_library_artifact_input_mode(
    project_slug: str,
    artifact_ref: str,
    request: ArtifactInputModeUpdate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    artifact = _resolve_project_artifact(session, project, artifact_ref)
    artifact.input_mode = request.input_mode.value
    artifact.snapshot = repositories.snapshot_artifact(artifact)
    _commit_or_conflict(session)
    return _artifact_payload(artifact)


@app.post(
    "/projects/{project_slug}/library/artifacts/{artifact_ref}/preprocessing-runs",
    status_code=201,
)
def start_library_artifact_preprocessing(
    project_slug: str,
    artifact_ref: str,
    request: ArtifactPreprocessingCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    source_artifact = _resolve_project_artifact(session, project, artifact_ref)
    try:
        record = _start_artifact_preprocessing(session, project, source_artifact, request)
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _artifact_preprocessing_run_payload(record)


@app.get("/projects/{project_slug}/library/artifacts/{artifact_ref}/preprocessing-runs")
def list_library_artifact_preprocessing_runs(
    project_slug: str,
    artifact_ref: str,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    source_artifact = _resolve_project_artifact(session, project, artifact_ref)
    return [
        _artifact_preprocessing_run_payload(record)
        for record in repositories.list_artifact_preprocessing_runs(
            session, project=project, source_artifact=source_artifact, status=status
        )
    ]


@app.get("/projects/{project_slug}/library/artifacts/{artifact_ref}/derived-artifacts")
def list_library_artifact_derived_outputs(
    project_slug: str,
    artifact_ref: str,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    source_artifact = _resolve_project_artifact(session, project, artifact_ref)
    records = repositories.list_artifact_preprocessing_runs(
        session, project=project, source_artifact=source_artifact
    )
    outputs: dict[str, dict[str, Any]] = {}
    for record in records:
        for artifact in record.derived_artifact_snapshots:
            artifact_id = artifact.get("id")
            if artifact_id is not None:
                outputs[str(artifact_id)] = _derived_artifact_payload(artifact)
    return list(outputs.values())


@app.post("/projects/{project_slug}/experiments/preview", response_model=ManifestPreviewResponse)
def preview_project_experiment(
    project_slug: str, request: dict[str, Any], session: Session = Depends(get_session)
) -> ManifestPreviewResponse:
    _get_or_create_project(session, project_slug)
    try:
        manifest = parse_manifest(_manifest_with_local_only_default(request))
        return expand_manifest(manifest)
    except ManifestValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors) from error


@app.post("/projects/{project_slug}/experiments/drafts", status_code=201)
def create_experiment_draft(
    project_slug: str, request: dict[str, Any], session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    try:
        manifest = parse_manifest(_manifest_with_local_only_default(request))
        preview = expand_manifest(manifest)
        experiment = repositories.create_experiment_from_manifest(
            session, project=project, manifest=manifest, preview=preview
        )
        experiment.status = ExperimentStatus.DRAFT.value
        _commit_or_conflict(session)
    except ManifestValidationError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=error.errors) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=[str(error)]) from error
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Resource already exists.") from error
    return _experiment_detail_payload(experiment, preview=preview.model_dump(mode="json"))


@app.put("/projects/{project_slug}/experiments/{experiment_id}/draft")
def update_experiment_draft(
    project_slug: str,
    experiment_id: int,
    request: dict[str, Any],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    experiment = _require_project_experiment(session, project, experiment_id)
    if experiment.status != ExperimentStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="Only draft experiments can be updated.")
    try:
        manifest = parse_manifest(_manifest_with_local_only_default(request))
        preview = expand_manifest(manifest)
        repositories.update_draft_experiment_from_manifest(
            session,
            project=project,
            experiment=experiment,
            manifest=manifest,
            preview=preview,
        )
        _commit_or_conflict(session)
    except ManifestValidationError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=error.errors) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=[str(error)]) from error
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Resource already exists.") from error
    return _experiment_detail_payload(experiment, preview=preview.model_dump(mode="json"))


@app.post("/projects/{project_slug}/experiments/{experiment_id}/queue")
def queue_project_experiment(
    project_slug: str, experiment_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    experiment = _require_project_experiment(session, project, experiment_id)
    if experiment.status != ExperimentStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="Only draft experiments can be queued.")
    try:
        jobs = enqueue_experiment_execution(experiment.id)
    except Exception as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=f"Could not enqueue experiment: {error}") from error
    experiment.status = ExperimentStatus.QUEUED.value
    repositories.record_audit_event(
        session,
        experiment=experiment,
        event_kind="experiment_execution_queued",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={"queued_jobs": len(jobs)},
    )
    session.commit()
    payload = _experiment_detail_payload(experiment)
    payload["queued_jobs"] = len(jobs)
    return payload


@app.get("/monitor/experiments")
def list_experiments(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    experiments = session.scalars(
        select(Experiment).options(selectinload(Experiment.project)).order_by(Experiment.id)
    ).all()
    return [_experiment_payload(experiment) for experiment in experiments]


@app.get("/monitor/experiments/{experiment_id}/runs")
def list_experiment_runs(
    experiment_id: int, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    _require_experiment(session, experiment_id)
    runs = session.scalars(
        select(Run).where(Run.experiment_id == experiment_id).order_by(Run.id)
    ).all()
    return [_run_payload(run) for run in runs]


@app.get("/monitor/runs/{run_id}/attempts")
def list_run_attempts(run_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    _require_run(session, run_id)
    attempts = session.scalars(
        select(RunAttempt).where(RunAttempt.run_id == run_id).order_by(RunAttempt.id)
    ).all()
    return [_attempt_payload(attempt) for attempt in attempts]


@app.get("/monitor/experiments/{experiment_id}/failures")
def list_experiment_failures(
    experiment_id: int, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    _require_experiment(session, experiment_id)
    attempts = session.scalars(
        select(RunAttempt)
        .join(Run)
        .where(Run.experiment_id == experiment_id, RunAttempt.status == "failed")
        .order_by(RunAttempt.id)
    ).all()
    return [_attempt_payload(attempt) for attempt in attempts]


@app.get("/monitor/experiments/{experiment_id}/analytics")
def get_experiment_results_analytics(
    experiment_id: int,
    case_slug: str | None = None,
    suite_slug: str | None = None,
    suite_split: str | None = None,
    model_config_slug: str | None = None,
    system_prompt_slug: str | None = None,
    warmer_slug: str | None = None,
    evaluator_source: str | None = None,
    reviewer_id: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_experiment(session, experiment_id)
    return aggregate_experiment_results(
        session,
        experiment_id=experiment_id,
        case_slug=case_slug,
        suite_slug=suite_slug,
        suite_split=suite_split,
        model_config_slug=model_config_slug,
        system_prompt_slug=system_prompt_slug,
        warmer_slug=warmer_slug,
        evaluator_source=evaluator_source,
        reviewer_id=reviewer_id,
    )


@app.post("/monitor/experiments/{experiment_id}/judges/{evaluator_id}/run")
def run_experiment_judge(
    experiment_id: int,
    evaluator_id: str,
    request: JudgeRunCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        payload = run_llm_judge(
            session,
            experiment_id=experiment_id,
            evaluator_id=evaluator_id,
            dry_run=request.dry_run,
            local_only=request.local_only,
            position_swap=request.position_swap,
            random_seed=request.random_seed,
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Judge execution already exists for this experiment and evaluator.",
        ) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return payload


@app.post("/monitor/experiments/{experiment_id}/metric-adapters/run")
def run_experiment_metric_adapters(
    experiment_id: int,
    request: MetricAdapterRunCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    experiment = _require_experiment(session, experiment_id)
    try:
        payload = run_metric_adapters_for_experiment(
            session,
            experiment_id=experiment.id,
            adapter_config_slug=request.adapter_config_slug,
            adapter_config_version=request.adapter_config_version,
            dry_run=request.dry_run,
            local_only=request.local_only,
            force=request.force,
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return payload


@app.get("/monitor/experiments/{experiment_id}/exports")
def export_monitor_experiment(
    experiment_id: int,
    format: str = "markdown",
    case_slug: str | None = None,
    suite_slug: str | None = None,
    suite_split: str | None = None,
    model_config_slug: str | None = None,
    system_prompt_slug: str | None = None,
    warmer_slug: str | None = None,
    evaluator_source: str | None = None,
    reviewer_id: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _require_experiment(session, experiment_id)
    try:
        return export_experiment_response(
            session,
            experiment_id,
            format,
            case_slug=case_slug,
            suite_slug=suite_slug,
            suite_split=suite_split,
            model_config_slug=model_config_slug,
            system_prompt_slug=system_prompt_slug,
            warmer_slug=warmer_slug,
            evaluator_source=evaluator_source,
            reviewer_id=reviewer_id,
        )
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/monitor/audit-logs")
def list_audit_logs(
    experiment_id: int | None = None,
    event_kind: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(AuditLog).order_by(AuditLog.id)
    if experiment_id is not None:
        query = query.where(AuditLog.experiment_id == experiment_id)
    if event_kind is not None:
        query = query.where(AuditLog.event_kind == event_kind)
    return [_audit_log_payload(item) for item in session.scalars(query).all()]


@app.post("/monitor/runs/{run_id}/retry")
def retry_run(run_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        attempt = create_retry_attempt_for_run(session, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return _attempt_payload(attempt)


@app.post("/monitor/experiments/{experiment_id}/cancel")
def cancel_experiment_endpoint(
    experiment_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        experiment = cancel_experiment(session, experiment_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return _experiment_payload(experiment)


@app.post("/monitor/runs/{run_id}/cancel")
def cancel_run_endpoint(run_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        run = cancel_run(session, run_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session.commit()
    return _run_payload(run)


@app.post("/projects/{project_slug}/experiments/{experiment_id}/review-sets", status_code=201)
def create_experiment_review_set(
    project_slug: str,
    experiment_id: int,
    request: ReviewSetCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    experiment = _require_project_experiment(session, project, experiment_id)
    existing = session.scalar(
        select(ReviewSet).where(
            ReviewSet.project_id == project.id,
            ReviewSet.experiment_id == experiment.id,
            ReviewSet.slug == request.slug,
        )
    )
    if existing is not None:
        if _review_set_request_conflicts(existing, request):
            raise HTTPException(
                status_code=409,
                detail="Review set already exists with different parameters.",
            )
        if request.reviewer_slugs:
            repositories.create_review_assignments(
                session,
                review_set=existing,
                reviewer_slugs=request.reviewer_slugs,
            )
            _commit_or_conflict(session)
        return _review_set_payload(existing, reveal_metadata=False)
    try:
        review_set = repositories.create_review_set_from_completed_experiment(
            session,
            project=project,
            experiment=experiment,
            slug=request.slug,
            name=request.name,
            random_seed=request.random_seed,
            failure_taxonomy_slug=request.failure_taxonomy_slug,
            reviewer_slugs=request.reviewer_slugs,
        )
        _commit_or_conflict(session)
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _review_set_payload(review_set, reveal_metadata=False)


def _review_set_request_conflicts(review_set: ReviewSet, request: ReviewSetCreate) -> bool:
    random_seed = (review_set.metadata_json or {}).get("random_seed")
    taxonomy = (review_set.metadata_json or {}).get("failure_taxonomy") or {}
    return (
        review_set.name != request.name
        or random_seed != request.random_seed
        or (
            request.failure_taxonomy_slug is not None
            and taxonomy.get("slug") != request.failure_taxonomy_slug
        )
    )


@app.get("/projects/{project_slug}/experiments/{experiment_id}/review-sets")
def list_experiment_review_sets(
    project_slug: str,
    experiment_id: int,
    slug: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    project = _get_or_create_project(session, project_slug)
    experiment = _require_project_experiment(session, project, experiment_id)
    query = (
        select(ReviewSet)
        .options(
            selectinload(ReviewSet.items),
            selectinload(ReviewSet.assignments).selectinload(ReviewAssignment.reviewer),
        )
        .where(
            ReviewSet.project_id == project.id,
            ReviewSet.experiment_id == experiment.id,
        )
        .order_by(ReviewSet.id.asc())
    )
    if slug is not None:
        query = query.where(ReviewSet.slug == slug)
    return [
        _review_set_payload(review_set, reveal_metadata=False)
        for review_set in session.scalars(query).all()
    ]


@app.get("/review-sets/{review_set_id}")
def get_review_set(
    review_set_id: int,
    reveal_metadata: bool = False,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    review_set = session.scalar(
        select(ReviewSet)
        .options(
            selectinload(ReviewSet.items),
            selectinload(ReviewSet.assignments).selectinload(ReviewAssignment.reviewer),
        )
        .where(ReviewSet.id == review_set_id)
    )
    if review_set is None:
        raise HTTPException(status_code=404, detail=f"Review set {review_set_id} does not exist.")
    return _review_set_payload(review_set, reveal_metadata=reveal_metadata)


@app.post("/review-sets/{review_set_id}/assignments", status_code=201)
def create_review_set_assignments(
    review_set_id: int,
    request: ReviewAssignmentCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    review_set = session.get(ReviewSet, review_set_id)
    if review_set is None:
        raise HTTPException(status_code=404, detail=f"Review set {review_set_id} does not exist.")
    assignments = repositories.create_review_assignments(
        session,
        review_set=review_set,
        reviewer_slugs=request.reviewer_slugs,
    )
    _commit_or_conflict(session)
    return {
        "review_set_id": review_set.id,
        "assignment_progress": _assignment_progress(assignments),
        "assignments": [_assignment_payload(assignment) for assignment in assignments],
    }


@app.get("/review-sets/{review_set_id}/reviewers/{reviewer_slug}/queue")
def get_review_set_reviewer_queue(
    review_set_id: int,
    reviewer_slug: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    review_set = session.get(ReviewSet, review_set_id)
    if review_set is None:
        raise HTTPException(status_code=404, detail=f"Review set {review_set_id} does not exist.")
    try:
        return repositories.get_reviewer_queue(
            session,
            review_set=review_set,
            reviewer_slug=reviewer_slug,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/review-assignments/{assignment_id}/decision")
def record_review_assignment_decision(
    assignment_id: int,
    request: ReviewDecisionCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    assignment = session.get(ReviewAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(
            status_code=404, detail=f"Review assignment {assignment_id} does not exist."
        )
    try:
        repositories.record_assignment_decision(
            session,
            assignment=assignment,
            winner=request.winner,
            pass_fail=request.pass_fail,
            failure_tags=request.failure_tags,
            rubric_notes=request.rubric_notes,
            notes=request.notes,
            confidence=request.confidence,
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _assignment_payload(assignment, include_decision=True)


@app.post("/review-items/{review_item_id}/decision")
def record_review_item_decision(
    review_item_id: int,
    request: ReviewDecisionCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    review_item = session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise HTTPException(
            status_code=404, detail=f"Review item {review_item_id} does not exist."
        )
    try:
        repositories.record_review_decision(
            session,
            review_item=review_item,
            reviewer_id=request.reviewer_id,
            winner=request.winner,
            pass_fail=request.pass_fail,
            failure_tags=request.failure_tags,
            rubric_notes=request.rubric_notes,
            notes=request.notes,
            confidence=request.confidence,
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _review_item_payload(review_item, reveal_metadata=False)


def _experiment_payload(experiment: Experiment) -> dict[str, Any]:
    return {
        "id": experiment.id,
        "project_slug": experiment.project.slug,
        "slug": experiment.slug,
        "name": experiment.name,
        "status": experiment.status,
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
    }


def _project_provider_policy_payload(project: Project) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "project_slug": project.slug,
        "provider_allow_list": list(project.provider_allow_list or []),
        "provider_deny_list": list(project.provider_deny_list or []),
    }


def _audit_log_payload(item: AuditLog) -> dict[str, Any]:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "experiment_id": item.experiment_id,
        "run_id": item.run_id,
        "run_attempt_id": item.run_attempt_id,
        "event_kind": item.event_kind,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "actor": item.actor,
        "details": dict(item.details or {}),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _run_payload(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_id": run.run_id,
        "experiment_id": run.experiment_id,
        "case_slug": run.case_slug,
        "model_config_slug": run.model_config_slug,
        "system_prompt_slug": run.system_prompt_slug,
        "warmer_slug": run.warmer_slug,
        "status": run.status,
        "data_egress_label": run.data_egress_label,
        "context_report": dict(run.context_report or {}),
        "truncation_policy": run.truncation_policy,
    }


def _attempt_payload(attempt: RunAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "run_id": attempt.run_id,
        "attempt_id": attempt.attempt_id,
        "replicate_index": attempt.replicate_index,
        "attempt_number": attempt.attempt_number,
        "parent_attempt_id": attempt.parent_attempt_id,
        "status": attempt.status,
        "error_kind": attempt.error_kind,
        "error_message": attempt.error_message,
        "terminal_failure_reason": attempt.terminal_failure_reason,
        "provider_response_id": attempt.provider_response_id,
        "provider": attempt.provider,
        "model": attempt.model,
        "provider_timestamp": (
            attempt.provider_timestamp.isoformat() if attempt.provider_timestamp else None
        ),
        "pricing_snapshot": dict(attempt.pricing_snapshot or {}),
        "provider_metadata": dict(attempt.provider_metadata or {}),
        "system_fingerprint": attempt.system_fingerprint,
        "request_payload": dict(attempt.request_payload or {}),
        "response_payload": dict(attempt.response_payload or {}),
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "latency_ms": attempt.latency_ms,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "total_tokens": attempt.total_tokens,
        "cost_usd": attempt.cost_usd,
        "cache_key": attempt.cache_key,
        "cache_hit": attempt.cache_hit,
    }


def _reviewer_payload(reviewer: Reviewer) -> dict[str, Any]:
    return {
        "id": reviewer.id,
        "slug": reviewer.slug,
        "name": reviewer.name,
        "email": reviewer.email,
    }


def _failure_taxonomy_payload(taxonomy: FailureTaxonomy) -> dict[str, Any]:
    return {
        "id": taxonomy.id,
        "slug": taxonomy.slug,
        "name": taxonomy.name,
        "tags": list(taxonomy.tags or []),
        "version": taxonomy.version,
        "archived": taxonomy.archived,
        "snapshot": dict(taxonomy.snapshot or {}),
    }


def _assignment_progress(assignments: list[ReviewAssignment]) -> dict[str, int]:
    submitted = sum(1 for assignment in assignments if assignment.status == "submitted")
    return {
        "assigned": len(assignments),
        "submitted": submitted,
        "pending": len(assignments) - submitted,
    }


def _assignment_payload(
    assignment: ReviewAssignment, *, include_decision: bool = False
) -> dict[str, Any]:
    payload = {
        "id": assignment.id,
        "review_set_id": assignment.review_set_id,
        "review_item_id": assignment.review_item_id,
        "status": assignment.status,
        "reviewer": _reviewer_payload(assignment.reviewer),
        "taxonomy_snapshot": dict(assignment.taxonomy_snapshot or {}),
        "assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at else None,
        "submitted_at": assignment.submitted_at.isoformat() if assignment.submitted_at else None,
    }
    if include_decision:
        payload["decision_snapshot"] = dict(assignment.decision_snapshot or {})
    return payload


def _review_set_payload(review_set: ReviewSet, *, reveal_metadata: bool) -> dict[str, Any]:
    assignments = sorted(review_set.assignments, key=lambda assignment: assignment.id)
    return {
        "id": review_set.id,
        "slug": review_set.slug,
        "name": review_set.name,
        "review_type": review_set.review_type,
        "assignment_progress": _assignment_progress(assignments),
        "assignments": [_assignment_payload(assignment) for assignment in assignments],
        "metadata": (
            dict(review_set.metadata_json or {})
            if reveal_metadata
            else _blind_review_set_metadata(review_set.metadata_json or {})
        ),
        "items": [
            _review_item_payload(item, reveal_metadata=reveal_metadata)
            for item in sorted(review_set.items, key=lambda value: value.id)
        ],
    }


def _review_item_payload(item: ReviewItem, *, reveal_metadata: bool) -> dict[str, Any]:
    metadata = dict(item.metadata_json or {})
    answers = list((item.answer_snapshot or {}).get("answers") or [])
    payload = {
        "id": item.id,
        "review_set_id": item.review_set_id,
        "item_key": item.item_key if reveal_metadata else f"review-item-{item.id}",
        "prompt": dict(item.prompt_snapshot or {}),
        "answers": _review_answers_payload(answers, reveal_metadata=reveal_metadata),
        "reviewer_decision": _reviewer_decision_payload(
            dict(item.reviewer_decision or {}), reveal_metadata=reveal_metadata
        ),
    }
    if reveal_metadata:
        payload["reveal_metadata"] = metadata.get("reveal_metadata", {})
    return payload


def _review_answers_payload(
    answers: list[dict[str, Any]], *, reveal_metadata: bool
) -> list[dict[str, Any]]:
    if reveal_metadata:
        return answers
    return [
        {
            "label": answer.get("label"),
            "text": answer.get("text", ""),
        }
        for answer in answers
    ]


def _blind_review_set_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"reveal_metadata", "answer_order", "source_experiment_id"}
    }


def _reviewer_decision_payload(
    reviewer_decision: dict[str, Any], *, reveal_metadata: bool
) -> dict[str, Any]:
    if reveal_metadata:
        return reviewer_decision
    return {}


def _get_or_create_project(session: Session, project_slug: str) -> Project:
    workspace = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if workspace is None:
        workspace = repositories.create_workspace(session, slug="default", name="Default")
        session.flush()
    project = session.scalar(
        select(Project).where(Project.workspace_id == workspace.id, Project.slug == project_slug)
    )
    if project is None:
        project = repositories.create_project(
            session, workspace=workspace, slug=project_slug, name=project_slug
        )
        session.flush()
    return project


def _library_items(session: Session, project: Project, model: type[Any]) -> list[Any]:
    return session.scalars(
        select(model)
        .where(model.project_id == project.id)
        .order_by(model.slug.asc(), model.version.asc(), model.id.asc())
    ).all()


def _resolve_project_artifact(session: Session, project: Project, artifact_ref: str) -> Artifact:
    artifact: Artifact | None = None
    if artifact_ref.isdigit():
        artifact = session.get(Artifact, int(artifact_ref))
        if artifact is not None and artifact.project_id != project.id:
            artifact = None
    if artifact is None:
        artifact = session.scalar(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.slug == artifact_ref)
            .order_by(Artifact.version.desc(), Artifact.id.desc())
        )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return artifact


def _start_artifact_preprocessing(
    session: Session,
    project: Project,
    source_artifact: Artifact,
    request: ArtifactPreprocessingCreate,
) -> Any:
    parser_name = request.parser_name.strip().lower()
    parser_version = request.parser_version or "1.0.0"
    if parser_name == "pdf_text":
        return artifact_processing.preprocess_pdf_text_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            parser_version=parser_version,
        )
    if parser_name in {"pdf_visual", "pdf_page_screenshots"}:
        return artifact_processing.preprocess_pdf_visual_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            parser_version=parser_version,
        )
    if parser_name in {"image_normalization", "image_visual", "ocr_text"}:
        return artifact_processing.preprocess_image_visual_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            parser_version=parser_version,
        )
    if parser_name == "selected_figure":
        return artifact_processing.preprocess_selected_figure_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            page_number=request.page_number or 1,
            region=request.region,
            parser_version=parser_version,
        )
    if parser_name == "table_extraction":
        return artifact_processing.preprocess_table_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            page_number=request.page_number or 1,
            region=request.region,
            table=request.table or {},
            parser_version=parser_version,
        )
    if parser_name == "retrieval_chunks":
        return artifact_processing.preprocess_retrieval_chunks_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            chunks=request.chunks,
            parser_version=parser_version,
        )
    if parser_name == "paper_card":
        return artifact_processing.preprocess_paper_card_artifact(
            session,
            project=project,
            source_artifact=source_artifact,
            citation=request.citation or {"source_artifact_id": source_artifact.id},
            sections=request.sections,
            parser_version=parser_version,
        )
    raise ValueError(f"Unsupported preprocessing parser: {request.parser_name}")


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Resource already exists.") from error


def _manifest_with_local_only_default(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    controls = normalized.get("controls")
    if controls is None:
        normalized["controls"] = {"local_only": True}
    elif isinstance(controls, dict):
        normalized["controls"] = {**controls, "local_only": controls.get("local_only", True)}
    return normalized


def _case_payload(item: Case) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "prompt": item.prompt,
        "prompt_ref": item.prompt_ref,
        "dataset_split": item.dataset_split,
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _system_prompt_payload(item: SystemPrompt) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "prompt": item.prompt,
        "prompt_ref": item.prompt_ref,
        "messages": list(item.messages or []),
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _conversation_warmer_payload(item: ConversationWarmer) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "domain": item.domain,
        "user_level": item.user_level,
        "intent": item.intent,
        "messages": list(item.messages or []),
        "tags": list(item.tags or []),
        "version_note": item.version_note,
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _model_config_payload(item: ModelConfig) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "provider": item.provider,
        "model": item.model,
        "temperature": item.temperature,
        "max_output_tokens": item.max_output_tokens,
        "reasoning_level": item.reasoning_level,
        "capability_flags": dict(item.capability_flags or {}),
        "raw_provider_params": dict(item.raw_provider_params or {}),
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _evaluator_payload(item: Evaluator) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "evaluator_type": item.evaluator_type,
        "definition": dict(item.definition or {}),
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _llm_judge_config_payload(item: LLMJudgeConfig) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "judge_prompt": item.judge_prompt,
        "rubric_dimensions": list(item.rubric_dimensions or []),
        "output_schema": dict(item.output_schema or {}),
        "judge_model_config_slug": item.judge_model_config_slug,
        "judge_model_config_version": item.judge_model_config_version,
        "raw_provider_params": dict(item.raw_provider_params or {}),
        "calibration_status": item.calibration_status,
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _metric_adapter_config_payload(item: MetricAdapterConfig) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "adapter_kind": item.adapter_kind,
        "adapter_version": item.adapter_version,
        "required_inputs": list(item.required_inputs or []),
        "output_schema": dict(item.output_schema or {}),
        "capability_metadata": dict(item.capability_metadata or {}),
        "local_only": item.local_only,
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _benchmark_suite_payload(item: BenchmarkSuite) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "description": item.description,
        "controls": dict(item.controls_json or {}),
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _benchmark_suite_preview_payload(result: dict[str, Any]) -> dict[str, Any]:
    preview = result["preview"]
    manifest = result["manifest"]
    return {
        "suite": _benchmark_suite_payload(result["suite"]),
        "split": result["split"],
        "suite_snapshot": result["suite_snapshot"],
        "manifest": manifest.model_dump(mode="json"),
        "preview": preview.model_dump(mode="json"),
    }


def _artifact_payload(item: Artifact) -> dict[str, Any]:
    return {
        "id": item.id,
        "slug": item.slug,
        "name": item.name,
        "artifact_type": item.artifact_type,
        "uri": item.uri,
        "input_mode": item.input_mode,
        "filename": item.filename,
        "checksum_sha256": item.checksum_sha256,
        "size_bytes": item.size_bytes,
        "mime_type": item.mime_type,
        "storage_uri": item.storage_uri,
        "image_width": item.image_width,
        "image_height": item.image_height,
        "metadata": dict(item.metadata_json or {}),
        "version": item.version,
        "archived": item.archived,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot": item.snapshot,
    }


def _artifact_preprocessing_run_payload(item: Any) -> dict[str, Any]:
    source_artifact = _preprocessing_source_artifact_payload(item.source_artifact_snapshot or {})
    source_artifact["id"] = item.source_artifact_id
    derived_artifact_ids = list(item.derived_artifact_ids or [])
    return {
        "id": item.id,
        "source_artifact_id": item.source_artifact_id,
        "source_artifact": source_artifact,
        "parser_name": item.parser_name,
        "parser_version": item.parser_version,
        "status": item.status,
        "source_checksum_sha256": item.source_checksum_sha256,
        "checksums": dict(item.checksums or {}),
        "derived_artifact_ids": derived_artifact_ids,
        "derived_artifacts": [
            _derived_artifact_payload(
                artifact,
                artifact_id=derived_artifact_ids[index]
                if index < len(derived_artifact_ids)
                else None,
            )
            for index, artifact in enumerate(list(item.derived_artifact_snapshots or []))
        ],
        "error_kind": item.error_kind,
        "error_message": item.error_message,
        "error_metadata": dict(item.error_metadata or {}),
        "extracted_at": item.extracted_at.isoformat() if item.extracted_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _preprocessing_source_artifact_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": None,
        "slug": snapshot.get("id"),
        "name": snapshot.get("name"),
        "artifact_type": snapshot.get("artifact_type"),
        "input_mode": snapshot.get("input_mode"),
        "filename": snapshot.get("filename"),
        "checksum_sha256": snapshot.get("checksum_sha256"),
        "mime_type": snapshot.get("mime_type"),
    }


def _derived_artifact_payload(
    snapshot: dict[str, Any], *, artifact_id: int | None = None
) -> dict[str, Any]:
    has_local_reference = bool(snapshot.get("storage_uri") or snapshot.get("uri"))
    metadata = dict(snapshot.get("metadata") or {})
    return {
        "id": artifact_id or metadata.get("derived_artifact_id"),
        "slug": snapshot.get("id") if isinstance(snapshot.get("id"), str) else snapshot.get("slug"),
        "name": snapshot.get("name"),
        "artifact_type": snapshot.get("artifact_type"),
        "input_mode": snapshot.get("input_mode"),
        "filename": snapshot.get("filename"),
        "checksum_sha256": snapshot.get("checksum_sha256"),
        "size_bytes": snapshot.get("size_bytes"),
        "mime_type": snapshot.get("mime_type"),
        "image_width": snapshot.get("image_width"),
        "image_height": snapshot.get("image_height"),
        "metadata": _derived_metadata_preview(metadata),
        "version": snapshot.get("version"),
        "archived": snapshot.get("archived"),
        "created_at": snapshot.get("created_at"),
        "local_storage": {
            "available": has_local_reference,
            "reference": "local_artifact_storage" if has_local_reference else None,
        },
    }


def _derived_metadata_preview(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_preprocessing_error_metadata(metadata)
    if not isinstance(sanitized, dict):
        return {}
    return _metadata_mapping_preview(sanitized)


_METADATA_PREVIEW_DROP = object()
_CONTENT_METADATA_KEYS = {
    "chunk_text",
    "content",
    "notes",
    "private_notes",
    "raw_content",
    "raw_text",
    "summary",
    "text",
}
_CONTENT_METADATA_KEY_PARTS = ("content", "note", "payload", "prompt", "raw")
_SAFE_STRING_METADATA_KEYS = {
    "checksum_sha256",
    "parser_name",
    "parser_version",
    "source_checksum_sha256",
}
_SAFE_CITATION_METADATA_KEYS = {
    "authors",
    "date",
    "doi",
    "journal",
    "publication",
    "publisher",
    "source_artifact_id",
    "title",
    "venue",
    "year",
}


def _metadata_mapping_preview(metadata: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = key.lower()
        if _is_content_metadata_key(normalized_key):
            continue
        if normalized_key == "sections" and isinstance(value, list):
            preview[key] = [
                {
                    field: section[field]
                    for field in ("title", "start_offset", "end_offset")
                    if isinstance(section, dict) and field in section
                }
                for section in value
            ]
            continue
        if normalized_key == "table" and isinstance(value, dict):
            preview[key] = {
                field: value[field]
                for field in ("columns", "row_count", "column_count", "checksum_sha256")
                if field in value
            }
            continue
        if normalized_key == "citation" and isinstance(value, dict):
            citation_preview = _citation_metadata_preview(value)
            if citation_preview:
                preview[key] = citation_preview
            continue
        preview_value = _metadata_preview_value(normalized_key, value)
        if preview_value is not _METADATA_PREVIEW_DROP:
            preview[key] = preview_value
    return preview


def _metadata_preview_value(normalized_key: str, value: Any) -> Any:
    if isinstance(value, dict):
        nested_preview = _metadata_mapping_preview(value)
        return nested_preview if nested_preview else _METADATA_PREVIEW_DROP
    if isinstance(value, list):
        if not value:
            return []
        if all(_is_non_string_scalar(item) for item in value):
            return value
        return {"count": len(value)}
    if isinstance(value, str):
        if normalized_key in _SAFE_STRING_METADATA_KEYS or normalized_key.endswith(
            "_checksum_sha256"
        ):
            return value
        return _METADATA_PREVIEW_DROP
    return value


def _citation_metadata_preview(citation: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for key, value in citation.items():
        normalized_key = key.lower()
        if normalized_key not in _SAFE_CITATION_METADATA_KEYS:
            continue
        if _is_non_string_scalar(value) or isinstance(value, str):
            preview[key] = value
        elif isinstance(value, list):
            preview[f"{key}_count"] = len(value)
    return preview


def _is_content_metadata_key(normalized_key: str) -> bool:
    return normalized_key in _CONTENT_METADATA_KEYS or any(
        part in normalized_key for part in _CONTENT_METADATA_KEY_PARTS
    )


def _is_non_string_scalar(value: Any) -> bool:
    return value is None or isinstance(value, bool | int | float)


def _experiment_detail_payload(
    experiment: Experiment, *, preview: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = _experiment_payload(experiment)
    payload.update(
        {
            "version": experiment.version,
            "manifest_snapshot": experiment.manifest_snapshot,
            "design_snapshot": experiment.design_snapshot,
            "controls_snapshot": experiment.controls_snapshot,
            "pricing_snapshot": experiment.pricing_snapshot,
        }
    )
    if preview is not None:
        payload["preview"] = preview
    return payload


def _require_project_experiment(
    session: Session, project: Project, experiment_id: int
) -> Experiment:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None or experiment.project_id != project.id:
        raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} does not exist.")
    return experiment


def _require_experiment(session: Session, experiment_id: int) -> Experiment:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} does not exist.")
    return experiment


def _require_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} does not exist.")
    return run
