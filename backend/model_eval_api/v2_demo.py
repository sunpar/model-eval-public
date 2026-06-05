from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from model_eval_api import headless
from model_eval_api.artifact_types import ArtifactInputMode
from model_eval_api.copper_demo import (
    _attempts,
    _ensure_library_records,
    _failure_tags_for_quality,
    _populate_synthetic_attempts,
    _rebuild_experiment,
    _repo_path,
    _review_quality,
    _runs,
)
from model_eval_api.copper_seed import copper_memo_seed_payload
from model_eval_api.deterministic_evaluators import run_deterministic_evaluators
from model_eval_api.execution_states import AttemptStatus
from model_eval_api.metric_adapter_execution import run_metric_adapters_for_experiment
from model_eval_api.persistence import repositories
from model_eval_api.persistence.models import (
    Artifact,
    ArtifactPreprocessingRun,
    BenchmarkSuite,
    Experiment,
    FailureTaxonomy,
    LLMJudgeConfig,
    MetricAdapterConfig,
    Project,
    ProviderCallCache,
    ReviewAssignment,
    ReviewSet,
    Reviewer,
    Run,
    RunAttempt,
    Score,
    Workspace,
)
from model_eval_api.results_analytics import aggregate_experiment_results


DEMO_ID = "v2_copper_demo"
DEMO_PROJECT_SLUG = "v2-copper-demo"
SUITE_SLUG = "v2_copper_benchmark_suite"
REVIEW_SET_SLUG = "v2-copper-demo-review"
TAXONOMY_SLUG = "v2_copper_failure_taxonomy"
JUDGE_CONFIG_SLUG = "v2_synthetic_judge"
TEXT_FIXTURE_PATH = "tests/fixtures/v2_demo_copper_context.txt"
IMAGE_FIXTURE_PATH = "tests/fixtures/v2_demo_copper_chart.svg"
EXPORT_FILENAMES = {
    "markdown": ("v2_copper_demo_report.md", ".md"),
    "csv": ("v2_copper_demo_report.csv", ".csv"),
    "json": ("v2_copper_demo_report.json", ".json"),
}
METRIC_ADAPTERS = [
    {
        "slug": "v2_retrieval_precision",
        "name": "V2 Retrieval Precision",
        "adapter_kind": "retrieval_precision",
        "required_inputs": ["answer_text", "retrieved_chunks"],
    },
    {
        "slug": "v2_citation_coverage",
        "name": "V2 Citation Coverage",
        "adapter_kind": "citation_coverage",
        "required_inputs": ["answer_text", "citations"],
    },
]


def build_v2_demo(session: Session, *, export_dir: Path | None = None) -> dict[str, Any]:
    payload = copper_memo_seed_payload()
    project = _get_or_create_project(session)
    _ensure_library_records(session, project=project, payload=payload)
    text_artifact, image_artifact, preprocessing_run = _ensure_demo_artifacts(
        session, project=project
    )
    judge_config = _get_or_create_judge_config(session, project=project)
    metric_configs = _get_or_create_metric_adapter_configs(session, project=project)
    suite = _get_or_create_suite(session, project=project, payload=payload)

    manifest = repositories.benchmark_suite_manifest(suite, split=None)
    manifest.controls.local_only = True
    preview = repositories.preview_benchmark_suite(session, suite=suite, split=None)["preview"]
    experiment = _rebuild_experiment(session, project=project, manifest=manifest, preview=preview)
    _populate_synthetic_attempts(session, experiment)
    _attach_demo_artifact_context(
        experiment,
        text_artifact=text_artifact,
        image_artifact=image_artifact,
        preprocessing_run=preprocessing_run,
    )
    session.flush()

    run_deterministic_evaluators(session, experiment.id)
    metric_result = run_metric_adapters_for_experiment(
        session,
        experiment_id=experiment.id,
        dry_run=False,
        local_only=True,
    )
    taxonomy = _get_or_create_taxonomy(session, project=project)
    reviewers = _get_or_create_reviewers(session, project=project)
    review_set = _create_review_set(
        session,
        project=project,
        experiment=experiment,
        taxonomy=taxonomy,
        reviewers=reviewers,
    )
    session.flush()
    _complete_multi_reviewer_reviews(session, review_set=review_set)
    session.flush()
    _record_synthetic_judge_scores(session, review_set=review_set, judge_config=judge_config)
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    exports = _write_exports(session, experiment=experiment, export_dir=export_dir)
    attempts = _attempts(session, experiment)
    assignments = _review_assignments(session, review_set=review_set)
    scores = _experiment_scores(session, experiment)
    return {
        "demo_id": DEMO_ID,
        "mode": "local_only_synthetic",
        "suite": {"slug": suite.slug, "version": suite.version, "split": "all"},
        "experiment": {
            "id": experiment.id,
            "slug": experiment.slug,
            "name": experiment.name,
            "status": experiment.status,
        },
        "library": {
            "cases": len(payload["cases"]),
            "warmers": len(payload["warmers"]),
            "system_prompts": len(payload["system_prompts"]),
            "model_configs": len(payload["model_configs"]),
            "evaluators": len(payload["evaluators"]),
        },
        "configs": {
            "judge_config": {"slug": judge_config.slug, "version": judge_config.version},
            "metric_adapters": [
                {"slug": config.slug, "version": config.version} for config in metric_configs
            ],
            "text_fixture_artifact": text_artifact.slug,
            "image_fixture_artifact": image_artifact.slug,
        },
        "preview": preview.model_dump(mode="json"),
        "metric_adapter_execution": metric_result,
        "counts": {
            "benchmark_suites": _count_project_records(session, project, BenchmarkSuite),
            "runs": len(_runs(session, experiment)),
            "attempts": len(attempts),
            "succeeded_attempts": sum(
                1 for attempt in attempts if attempt.status == AttemptStatus.SUCCEEDED.value
            ),
            "review_items": len(review_set.items),
            "reviewers": _count_project_records(session, project, Reviewer),
            "review_assignments": len(assignments),
            "review_submissions": sum(
                1 for assignment in assignments if assignment.status == "submitted"
            ),
            "preprocessing_runs": _count_project_records(
                session, project, ArtifactPreprocessingRun
            ),
            "judge_configs": _count_project_records(session, project, LLMJudgeConfig),
            "judge_scores": sum(1 for score in scores if score.evaluator_type == "llm_judge"),
            "metric_adapter_configs": _count_project_records(
                session, project, MetricAdapterConfig
            ),
            "metric_adapter_scores": sum(
                1 for score in scores if score.evaluator_type == "metric_adapter"
            ),
            "divergence_scores": _count_semantic_divergence_scores(scores),
            "live_provider_calls": _count_project_records(session, project, ProviderCallCache),
        },
        "analytics": analytics,
        "exports": exports,
    }


def _get_or_create_project(session: Session) -> Project:
    workspace = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if workspace is None:
        workspace = repositories.create_workspace(session, slug="default", name="Default")
        session.flush()
    project = session.scalar(
        select(Project).where(
            Project.workspace_id == workspace.id,
            Project.slug == DEMO_PROJECT_SLUG,
        )
    )
    if project is None:
        project = repositories.create_project(
            session, workspace=workspace, slug=DEMO_PROJECT_SLUG, name="V2 Copper Demo"
        )
        session.flush()
    return project


def _ensure_demo_artifacts(
    session: Session, *, project: Project
) -> tuple[Artifact, Artifact, ArtifactPreprocessingRun]:
    text_path = _repo_path(TEXT_FIXTURE_PATH)
    image_path = _repo_path(IMAGE_FIXTURE_PATH)
    text_artifact = _get_or_create_file_artifact(
        session,
        project=project,
        path=text_path,
        slug="v2_demo_copper_context",
        name="V2 copper synthetic context fixture",
        artifact_type="text",
        input_mode=ArtifactInputMode.DIRECT_FILE,
        metadata={"fixture": True, "safe_committed": True},
    )
    image_artifact = _get_or_create_file_artifact(
        session,
        project=project,
        path=image_path,
        slug="v2_demo_copper_chart",
        name="V2 copper synthetic chart fixture",
        artifact_type="image",
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
        metadata={"fixture": True, "safe_committed": True},
        image_width=320,
        image_height=180,
    )
    preprocessing_run = session.scalar(
        select(ArtifactPreprocessingRun)
        .where(
            ArtifactPreprocessingRun.project_id == project.id,
            ArtifactPreprocessingRun.source_artifact_id == text_artifact.id,
            ArtifactPreprocessingRun.parser_name == "retrieval_chunks",
            ArtifactPreprocessingRun.parser_version == "v2-demo-1",
            ArtifactPreprocessingRun.status == "completed",
        )
        .order_by(ArtifactPreprocessingRun.id)
    )
    if preprocessing_run is not None:
        return text_artifact, image_artifact, preprocessing_run

    retrieval_payload = _retrieval_chunk_payload(text_artifact)
    retrieval_bytes = _stable_json_bytes(retrieval_payload)
    retrieval_chunk = _get_or_create_artifact(
        session,
        project=project,
        slug="v2_demo_copper_context_chunk_1",
        name="V2 copper context retrieval chunk",
        artifact_type="retrieval_chunk",
        uri=text_path.as_uri(),
        input_mode=ArtifactInputMode.RETRIEVAL_CHUNKS,
        filename="v2_demo_copper_context_chunk_1.json",
        checksum_sha256=hashlib.sha256(retrieval_bytes).hexdigest(),
        size_bytes=len(retrieval_bytes),
        mime_type="application/json",
        metadata=retrieval_payload,
    )
    preprocessing_run = repositories.create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=text_artifact,
        parser_name="retrieval_chunks",
        parser_version="v2-demo-1",
        local_storage_uri=text_path.as_uri(),
    )
    repositories.complete_artifact_preprocessing_run(
        session,
        preprocessing_run=preprocessing_run,
        derived_artifacts=[retrieval_chunk],
        local_storage_uri=text_path.as_uri(),
        output_checksums={"retrieval_chunks": {"1": retrieval_chunk.checksum_sha256}},
    )
    session.flush()
    return text_artifact, image_artifact, preprocessing_run


def _get_or_create_file_artifact(
    session: Session,
    *,
    project: Project,
    path: Path,
    slug: str,
    name: str,
    artifact_type: str,
    input_mode: ArtifactInputMode,
    metadata: dict[str, Any],
    image_width: int | None = None,
    image_height: int | None = None,
) -> Artifact:
    data = path.read_bytes()
    return _get_or_create_artifact(
        session,
        project=project,
        slug=slug,
        name=name,
        artifact_type=artifact_type,
        uri=path.as_uri(),
        input_mode=input_mode,
        filename=path.name,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        metadata=metadata,
        image_width=image_width,
        image_height=image_height,
    )


def _get_or_create_artifact(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    artifact_type: str,
    uri: str,
    input_mode: ArtifactInputMode,
    filename: str,
    checksum_sha256: str,
    size_bytes: int,
    mime_type: str,
    metadata: dict[str, Any],
    image_width: int | None = None,
    image_height: int | None = None,
) -> Artifact:
    existing = session.scalar(
        select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.slug == slug,
            Artifact.version == 1,
        )
    )
    if existing is not None:
        return existing
    artifact = repositories.create_artifact(
        session,
        project=project,
        slug=slug,
        name=name,
        artifact_type=artifact_type,
        uri=uri,
        input_mode=input_mode,
        filename=filename,
        checksum_sha256=checksum_sha256,
        size_bytes=size_bytes,
        mime_type=mime_type,
        storage_uri=uri,
        image_width=image_width,
        image_height=image_height,
        metadata=metadata,
    )
    session.flush()
    return artifact


def _retrieval_chunk_payload(source_artifact: Artifact) -> dict[str, Any]:
    return {
        "chunk_id": "copper_context_1",
        "chunk_index": 1,
        "chunk_text": _repo_path(TEXT_FIXTURE_PATH).read_text(encoding="utf-8"),
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": "retrieval_chunks",
        "parser_version": "v2-demo-1",
    }


def _get_or_create_judge_config(session: Session, *, project: Project) -> LLMJudgeConfig:
    existing = session.scalar(
        select(LLMJudgeConfig).where(
            LLMJudgeConfig.project_id == project.id,
            LLMJudgeConfig.slug == JUDGE_CONFIG_SLUG,
            LLMJudgeConfig.version == 1,
        )
    )
    if existing is not None:
        return existing
    judge_config = repositories.create_llm_judge_config(
        session,
        project=project,
        slug=JUDGE_CONFIG_SLUG,
        name="V2 synthetic copper memo judge",
        judge_prompt=(
            "Score synthetic copper investment memo pairs for thesis quality, grounding, "
            "risk coverage, and trade expression. This demo never calls a provider."
        ),
        rubric_dimensions=[
            {"id": "claim_quality", "scale": "0_to_1"},
            {"id": "grounding", "scale": "0_to_1"},
            {"id": "risk_coverage", "scale": "0_to_1"},
        ],
        output_schema={"type": "object"},
        judge_model_config_slug="openai_gpt_high",
        raw_provider_params={"local_only": True, "synthetic": True},
        calibration_status="local_synthetic",
    )
    session.flush()
    return judge_config


def _get_or_create_metric_adapter_configs(
    session: Session, *, project: Project
) -> list[MetricAdapterConfig]:
    configs: list[MetricAdapterConfig] = []
    for definition in METRIC_ADAPTERS:
        existing = session.scalar(
            select(MetricAdapterConfig).where(
                MetricAdapterConfig.project_id == project.id,
                MetricAdapterConfig.slug == definition["slug"],
                MetricAdapterConfig.version == 1,
            )
        )
        if existing is not None:
            configs.append(existing)
            continue
        config = repositories.create_metric_adapter_config(
            session,
            project=project,
            slug=str(definition["slug"]),
            name=str(definition["name"]),
            adapter_kind=str(definition["adapter_kind"]),
            adapter_version="local-v2-demo-1",
            required_inputs=list(definition["required_inputs"]),
            output_schema={"type": "object"},
            capability_metadata={"demo_id": DEMO_ID},
            local_only=True,
        )
        session.flush()
        configs.append(config)
    return configs


def _get_or_create_suite(
    session: Session, *, project: Project, payload: dict[str, Any]
) -> BenchmarkSuite:
    existing = session.scalar(
        select(BenchmarkSuite).where(
            BenchmarkSuite.project_id == project.id,
            BenchmarkSuite.slug == SUITE_SLUG,
            BenchmarkSuite.version == 1,
        )
    )
    if existing is not None:
        return existing
    suite = repositories.create_benchmark_suite(
        session,
        project=project,
        slug=SUITE_SLUG,
        name="V2 Copper Benchmark Suite",
        description="Local-only synthetic V2 demo suite extending the copper memo scenario.",
        case_ids=[item["id"] for item in payload["cases"]],
        model_config_ids=[item["id"] for item in payload["model_configs"]],
        system_prompt_ids=[item["id"] for item in payload["system_prompts"]],
        warmer_ids=[item["id"] for item in payload["warmers"]],
        evaluator_ids=[item["id"] for item in payload["evaluators"]],
        controls={
            "local_only": True,
            "random_seed": 25,
            "randomize_run_order": False,
            "replicates": 2,
        },
    )
    session.flush()
    return suite


def _attach_demo_artifact_context(
    experiment: Experiment,
    *,
    text_artifact: Artifact,
    image_artifact: Artifact,
    preprocessing_run: ArtifactPreprocessingRun,
) -> None:
    retrieval_chunk = dict(preprocessing_run.derived_artifact_snapshots[0]["metadata"])
    context = {
        "retrieved_chunks": [
            {
                "chunk_id": "copper_context_1",
                "chunk_text": retrieval_chunk["chunk_text"],
                "source_artifact_id": text_artifact.id,
            }
        ],
        "citations": [
            {
                "id": "copper_context_1",
                "source_artifact_id": text_artifact.id,
                "title": "Synthetic copper context",
            }
        ],
        "reference_answers": [
            {
                "id": "synthetic_copper_reference",
                "text": "A strong memo links synthetic inventories, treatment charges, and risk.",
            }
        ],
        "derived_artifacts": [
            {
                "id": "v2_demo_copper_context_chunk_1",
                "artifact_id": preprocessing_run.derived_artifact_ids[0],
                "input_mode": ArtifactInputMode.RETRIEVAL_CHUNKS.value,
            },
            {
                "id": image_artifact.slug,
                "artifact_id": image_artifact.id,
                "input_mode": image_artifact.input_mode,
            },
        ],
    }
    for run in experiment.runs:
        run.model_input_snapshot = {
            **dict(run.model_input_snapshot or {}),
            "artifact_inputs": [
                {
                    "source_artifact_id": text_artifact.id,
                    "derived_artifact_id": preprocessing_run.derived_artifact_ids[0],
                    "input_mode": ArtifactInputMode.RETRIEVAL_CHUNKS.value,
                },
                {
                    "source_artifact_id": image_artifact.id,
                    "input_mode": image_artifact.input_mode,
                },
            ],
            "derived_bundle": {
                "preprocessing_run_id": preprocessing_run.id,
                "derived_artifact_ids": list(preprocessing_run.derived_artifact_ids),
            },
        }
        run.context_report = {
            **dict(run.context_report or {}),
            "artifact_context": {
                "source_artifact_ids": [text_artifact.id, image_artifact.id],
                "preprocessing_run_id": preprocessing_run.id,
                "local_only": True,
            },
        }
        for attempt in run.attempts:
            response_payload = dict(attempt.response_payload or {})
            output_text = str(response_payload.get("output_text") or "")
            response_payload.update(
                {
                    **context,
                    "output_text": f"{output_text}\nEvidence\nSynthetic context [copper_context_1].",
                }
            )
            attempt.response_payload = response_payload


def _get_or_create_taxonomy(session: Session, *, project: Project) -> FailureTaxonomy:
    existing = session.scalar(
        select(FailureTaxonomy).where(
            FailureTaxonomy.project_id == project.id,
            FailureTaxonomy.slug == TAXONOMY_SLUG,
            FailureTaxonomy.version == 1,
        )
    )
    if existing is not None:
        return existing
    taxonomy = repositories.create_failure_taxonomy(
        session,
        project=project,
        slug=TAXONOMY_SLUG,
        name="V2 copper failure taxonomy",
        tags=[
            "too generic",
            "missed transmission mechanism",
            "weak trade expression",
            "weak risks",
            "citation gap",
        ],
    )
    session.flush()
    return taxonomy


def _get_or_create_reviewers(session: Session, *, project: Project) -> list[Reviewer]:
    reviewers = [
        repositories.create_reviewer(
            session,
            project=project,
            slug="v2_alice",
            name="V2 Alice",
            email="v2-alice@example.invalid",
        ),
        repositories.create_reviewer(
            session,
            project=project,
            slug="v2_bob",
            name="V2 Bob",
            email="v2-bob@example.invalid",
        ),
    ]
    session.flush()
    return reviewers


def _create_review_set(
    session: Session,
    *,
    project: Project,
    experiment: Experiment,
    taxonomy: FailureTaxonomy,
    reviewers: list[Reviewer],
) -> ReviewSet:
    return repositories.create_review_set_from_completed_experiment(
        session,
        project=project,
        experiment=experiment,
        slug=REVIEW_SET_SLUG,
        name="V2 copper demo multi-reviewer calibration",
        random_seed=25,
        failure_taxonomy_slug=taxonomy.slug,
        reviewer_slugs=[reviewer.slug for reviewer in reviewers],
    )


def _complete_multi_reviewer_reviews(session: Session, *, review_set: ReviewSet) -> None:
    item_index_by_id = {item.id: index for index, item in enumerate(review_set.items, start=1)}
    for assignment in _review_assignments(session, review_set=review_set):
        item = assignment.review_item
        answers = list((item.answer_snapshot or {}).get("answers") or [])
        if len(answers) != 2:
            continue
        scored = [
            (answer["label"], _review_quality(session.get(RunAttempt, int(answer["run_attempt_id"]))))
            for answer in answers
        ]
        preferred = max(scored, key=lambda item_score: (item_score[1], item_score[0]))[0]
        alternate = min(scored, key=lambda item_score: (item_score[1], item_score[0]))[0]
        item_index = item_index_by_id.get(item.id, 0)
        reviewer_slug = assignment.reviewer.slug
        invert = reviewer_slug == "v2_bob" and item_index % 4 == 0
        winner = alternate if invert else preferred
        default_pass_fail = {label: quality >= 10 for label, quality in scored}
        pass_fail = (
            {label: not passed for label, passed in default_pass_fail.items()}
            if invert
            else default_pass_fail
        )
        failure_tags = {
            label: _failure_tags_for_quality(_quality_for_label(scored, label))
            for label, passed in pass_fail.items()
            if not passed
        }
        rubric_notes = {
            label: (
                f"synthetic reviewer {reviewer_slug} quality "
                f"{_quality_for_label(scored, label)}"
            )
            for label, _ in scored
        }
        repositories.record_assignment_decision(
            session,
            assignment=assignment,
            winner=winner,
            pass_fail=pass_fail,
            failure_tags=failure_tags,
            rubric_notes=rubric_notes,
            notes=f"Synthetic V2 local review by {reviewer_slug}.",
            confidence=0.72 if invert else 0.9,
        )


def _record_synthetic_judge_scores(
    session: Session, *, review_set: ReviewSet, judge_config: LLMJudgeConfig
) -> None:
    for item_index, item in enumerate(sorted(review_set.items, key=lambda value: value.id), start=1):
        answers = list((item.answer_snapshot or {}).get("answers") or [])
        if len(answers) != 2:
            continue
        scored = [
            (answer["label"], session.get(RunAttempt, int(answer["run_attempt_id"])))
            for answer in answers
        ]
        quality_by_label = {
            label: _review_quality(attempt) for label, attempt in scored if attempt is not None
        }
        winner = max(quality_by_label.items(), key=lambda item_score: (item_score[1], item_score[0]))[
            0
        ]
        confidence = 0.42 if item_index % 5 == 0 else 0.82
        for label, attempt in scored:
            if attempt is None:
                continue
            quality = quality_by_label[label]
            passed = quality >= 10
            repositories.record_score(
                session,
                run_attempt=attempt,
                type="pairwise_preference",
                evaluator_type="llm_judge",
                criterion="llm_judge_pairwise_preference",
                value={
                    "label": label,
                    "outcome": "winner" if label == winner else "loser",
                    "comparison_id": item.item_key,
                    "evaluator_id": judge_config.slug,
                    "judge_config": {"id": judge_config.slug, "version": judge_config.version},
                    "answer_token_count": attempt.output_tokens,
                    "source_kind": "local_synthetic",
                },
                confidence=confidence,
                evaluator_version=judge_config.version,
            )
            repositories.record_score(
                session,
                run_attempt=attempt,
                type="pass_fail",
                evaluator_type="llm_judge",
                criterion="llm_judge_pass_fail",
                value={
                    "label": label,
                    "passed": passed,
                    "comparison_id": item.item_key,
                    "evaluator_id": judge_config.slug,
                    "judge_config": {"id": judge_config.slug, "version": judge_config.version},
                    "source_kind": "local_synthetic",
                },
                confidence=confidence,
                evaluator_version=judge_config.version,
            )
            repositories.record_score(
                session,
                run_attempt=attempt,
                type="rubric_score",
                evaluator_type="llm_judge",
                criterion="llm_judge_rubric",
                value={
                    "label": label,
                    "dimension": "claim_conclusion_quality",
                    "score": round(quality / 12, 4),
                    "comparison_id": _judge_signal_comparison_id(attempt),
                    "evaluator_id": judge_config.slug,
                    "judge_config": {"id": judge_config.slug, "version": judge_config.version},
                    "dimensions": {
                        "claim_quality": round(quality / 12, 4),
                        "grounding": 0.85 if passed else 0.45,
                        "risk_coverage": 0.8 if quality >= 10 else 0.5,
                    },
                    "source_kind": "local_synthetic",
                },
                confidence=confidence,
                evaluator_version=judge_config.version,
            )


def _write_exports(
    session: Session, *, experiment: Experiment, export_dir: Path | None
) -> list[dict[str, Any]]:
    if export_dir is None:
        return []
    export_dir.mkdir(parents=True, exist_ok=True)
    exports = []
    for export_format, (filename, extension) in EXPORT_FILENAMES.items():
        content = headless.export_experiment(
            session, experiment.id, "markdown" if export_format == "markdown" else export_format
        )
        path = export_dir / filename
        if export_format == "json":
            json.loads(content)
        path.write_text(content, encoding="utf-8")
        exports.append(
            {
                "format": export_format,
                "filename": filename,
                "path": str(path),
                "extension": extension,
            }
        )
    return exports


def _review_assignments(session: Session, *, review_set: ReviewSet) -> list[ReviewAssignment]:
    return session.scalars(
        select(ReviewAssignment)
        .where(ReviewAssignment.review_set_id == review_set.id)
        .order_by(ReviewAssignment.id)
    ).all()


def _experiment_scores(session: Session, experiment: Experiment) -> list[Score]:
    return session.scalars(
        select(Score).join(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
    ).all()


def _count_project_records(session: Session, project: Project, model: type[Any]) -> int:
    return len(session.scalars(select(model).where(model.project_id == project.id)).all())


def _count_semantic_divergence_scores(scores: list[Score]) -> int:
    return sum(
        1
        for score in scores
        if score.type == "divergence"
        and score.evaluator_type == "code"
        and score.criterion == "divergence_semantic_overlap"
    )


def _judge_signal_comparison_id(attempt: RunAttempt) -> str:
    run = attempt.run
    return ":".join(
        [
            run.case_slug,
            run.model_config_slug,
            run.system_prompt_slug,
            str(attempt.replicate_index),
        ]
    )


def _quality_for_label(scored: list[tuple[str, int]], label: str) -> int:
    return next(quality for scored_label, quality in scored if scored_label == label)


def _stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
