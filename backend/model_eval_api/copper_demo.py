from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from model_eval_api import headless
from model_eval_api.deterministic_evaluators import run_deterministic_evaluators
from model_eval_api.execution_states import AttemptStatus, ExperimentStatus, RunStatus
from model_eval_api.manifest import expand_manifest, load_manifest_file
from model_eval_api.persistence import repositories
from model_eval_api.persistence.models import (
    Case,
    ConversationWarmer,
    Evaluator,
    Experiment,
    ModelConfig,
    Project,
    ReviewSet,
    Run,
    RunAttempt,
    SystemPrompt,
    Workspace,
)
from model_eval_api.results_analytics import aggregate_experiment_results
from model_eval_api.copper_seed import copper_memo_seed_payload


DEMO_PROJECT_SLUG = "copper-memo-demo"
DEMO_REVIEWER_ID = "phase12-synthetic-human"
EXPORT_FILENAMES = {
    "markdown": ("copper_memo_demo_report.md", ".md"),
    "csv": ("copper_memo_demo_report.csv", ".csv"),
    "json": ("copper_memo_demo_report.json", ".json"),
}


def build_copper_memo_demo(session: Session, *, export_dir: Path | None = None) -> dict[str, Any]:
    payload = copper_memo_seed_payload()
    project = _get_or_create_project(session)
    _ensure_library_records(session, project=project, payload=payload)
    manifest = load_manifest_file(_repo_path(payload["source_manifest"]))
    manifest.id = payload["experiment"]["id"]
    manifest.controls.local_only = True
    preview = expand_manifest(manifest)
    experiment = _rebuild_experiment(session, project=project, manifest=manifest, preview=preview)

    _populate_synthetic_attempts(session, experiment)
    session.flush()
    run_deterministic_evaluators(session, experiment.id)
    review_set = _create_demo_review_set(session, project=project, experiment=experiment)
    session.flush()
    _complete_sample_reviews(session, review_set=review_set)
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    exports = _write_exports(session, experiment=experiment, export_dir=export_dir)
    attempts = _attempts(session, experiment)
    review_items = list(review_set.items)
    return {
        "demo_id": payload["demo_id"],
        "mode": "local_only_synthetic",
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
        "preview": preview.model_dump(mode="json"),
        "counts": {
            "runs": len(_runs(session, experiment)),
            "attempts": len(attempts),
            "succeeded_attempts": sum(
                1 for attempt in attempts if attempt.status == AttemptStatus.SUCCEEDED.value
            ),
            "review_items": len(review_items),
            "review_decisions": sum(1 for item in review_items if item.reviewer_decision),
            "live_provider_calls": 0,
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
            session, workspace=workspace, slug=DEMO_PROJECT_SLUG, name="Copper Memo Demo"
        )
        session.flush()
    return project


def _repo_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[2] / path


def _ensure_library_records(session: Session, *, project: Project, payload: dict[str, Any]) -> None:
    for case in payload["cases"]:
        _get_or_create_case(session, project=project, case=case)
    for warmer in payload["warmers"]:
        _get_or_create_warmer(session, project=project, warmer=warmer)
    for system_prompt in payload["system_prompts"]:
        _get_or_create_system_prompt(session, project=project, system_prompt=system_prompt)
    for model_config in payload["model_configs"]:
        _get_or_create_model_config(session, project=project, model_config=model_config)
    for evaluator in payload["evaluators"]:
        _get_or_create_evaluator(session, project=project, evaluator=evaluator)
    session.flush()


def _get_or_create_case(session: Session, *, project: Project, case: dict[str, Any]) -> Case:
    existing = _versioned_record(session, Case, project=project, slug=case["id"], version=case["version"])
    if existing is not None:
        return existing
    return repositories.create_case(
        session,
        project=project,
        slug=case["id"],
        name=case["name"],
        prompt=case["prompt"],
        version=case["version"],
    )


def _get_or_create_warmer(
    session: Session, *, project: Project, warmer: dict[str, Any]
) -> ConversationWarmer:
    existing = _versioned_record(
        session, ConversationWarmer, project=project, slug=warmer["id"], version=warmer["version"]
    )
    if existing is not None:
        return existing
    return repositories.create_conversation_warmer(
        session,
        project=project,
        slug=warmer["id"],
        name=warmer["name"],
        domain=warmer.get("domain"),
        user_level=warmer.get("user_level"),
        intent=warmer.get("intent"),
        messages=warmer.get("messages") or [],
        tags=warmer.get("tags") or [],
        version=warmer["version"],
    )


def _get_or_create_system_prompt(
    session: Session, *, project: Project, system_prompt: dict[str, Any]
) -> SystemPrompt:
    existing = _versioned_record(
        session,
        SystemPrompt,
        project=project,
        slug=system_prompt["id"],
        version=system_prompt["version"],
    )
    if existing is not None:
        return existing
    return repositories.create_system_prompt(
        session,
        project=project,
        slug=system_prompt["id"],
        name=system_prompt["name"],
        prompt=system_prompt["prompt"],
        version=system_prompt["version"],
    )


def _get_or_create_model_config(
    session: Session, *, project: Project, model_config: dict[str, Any]
) -> ModelConfig:
    existing = _versioned_record(
        session,
        ModelConfig,
        project=project,
        slug=model_config["id"],
        version=model_config["version"],
    )
    if existing is not None:
        return existing
    return repositories.create_model_config(
        session,
        project=project,
        slug=model_config["id"],
        name=model_config["id"].replace("_", " ").title(),
        provider=model_config["provider"],
        model=model_config["model"],
        temperature=model_config.get("temperature"),
        reasoning_level=model_config.get("reasoning_level"),
        raw_provider_params=model_config.get("raw_provider_params") or {},
        version=model_config["version"],
    )


def _get_or_create_evaluator(
    session: Session, *, project: Project, evaluator: dict[str, Any]
) -> Evaluator:
    existing = _versioned_record(
        session, Evaluator, project=project, slug=evaluator["id"], version=evaluator["version"]
    )
    if existing is not None:
        return existing
    return repositories.create_evaluator(
        session,
        project=project,
        slug=evaluator["id"],
        name=evaluator["name"],
        evaluator_type=evaluator["evaluator_type"],
        definition=evaluator["definition"],
        version=evaluator["version"],
    )


def _versioned_record(
    session: Session, model: type[Any], *, project: Project, slug: str, version: int
) -> Any | None:
    return session.scalar(
        select(model).where(
            model.project_id == project.id,
            model.slug == slug,
            model.version == version,
        )
    )


def _rebuild_experiment(session: Session, *, project: Project, manifest: Any, preview: Any) -> Experiment:
    existing = session.scalar(
        select(Experiment).where(
            Experiment.project_id == project.id,
            Experiment.slug == manifest.experiment_id,
        )
    )
    if existing is None:
        experiment = repositories.create_experiment_from_manifest(
            session, project=project, manifest=manifest, preview=preview
        )
        session.flush()
        return experiment

    for review_set in session.scalars(
        select(ReviewSet).where(ReviewSet.experiment_id == existing.id)
    ).all():
        session.delete(review_set)
    existing.status = ExperimentStatus.DRAFT.value
    existing.runs.clear()
    session.flush()
    experiment = repositories.update_draft_experiment_from_manifest(
        session, project=project, experiment=existing, manifest=manifest, preview=preview
    )
    session.flush()
    session.refresh(experiment)
    return experiment


def _populate_synthetic_attempts(session: Session, experiment: Experiment) -> None:
    for run_index, run in enumerate(_runs(session, experiment), start=1):
        run.status = RunStatus.COMPLETE.value
        model_score = 8 if run.model_config_slug == "openai_gpt_high" else 7
        warmer_score = {
            "none": 0,
            "copper_expert_user_v2": 3,
            "copper_low_knowledge_user_v1": 1,
            "copper_adversarial_user_v1": 2,
        }[run.warmer_slug]
        system_score = 1 if run.system_prompt_slug == "expert_investment_analyst_v3" else 0
        quality_score = model_score + warmer_score + system_score
        for attempt in sorted(run.attempts, key=lambda item: item.replicate_index):
            output_tokens = 520 + (quality_score * 11) + (attempt.replicate_index * 7)
            input_tokens = 360 + (run_index * 3)
            attempt.status = AttemptStatus.SUCCEEDED.value
            attempt.attempt_number = 1
            attempt.request_payload = {
                "local_only": True,
                "synthetic_demo": True,
                "case_slug": run.case_slug,
                "model_config_slug": run.model_config_slug,
                "system_prompt_slug": run.system_prompt_slug,
                "warmer_slug": run.warmer_slug,
            }
            attempt.response_payload = {
                "output_text": _synthetic_output(run=run, quality_score=quality_score),
                "synthetic_demo": True,
                "local_only": True,
                "quality_score": quality_score,
            }
            attempt.provider_response_id = f"synthetic-{attempt.attempt_id}"
            attempt.latency_ms = 850 + (run_index * 19) + ((attempt.replicate_index + 1) * 31)
            attempt.input_tokens = input_tokens
            attempt.output_tokens = output_tokens
            attempt.total_tokens = input_tokens + output_tokens
            attempt.cost_usd = round(
                (0.0018 if run.model_config_slug == "openai_gpt_high" else 0.0024)
                + (output_tokens / 1_000_000),
                6,
            )
    experiment.status = ExperimentStatus.COMPLETE.value


def _create_demo_review_set(
    session: Session, *, project: Project, experiment: Experiment
) -> ReviewSet:
    return repositories.create_review_set_from_completed_experiment(
        session,
        project=project,
        experiment=experiment,
        slug=f"{experiment.slug}-blind-review",
        name=f"{experiment.name} blind review",
        random_seed=12,
    )


def _synthetic_output(*, run: Run, quality_score: int) -> str:
    warmer_note = {
        "none": "baseline framing with limited prior context",
        "copper_expert_user_v2": "market-structure framing with inventory transmission",
        "copper_low_knowledge_user_v1": "clear explanation while preserving investment specificity",
        "copper_adversarial_user_v1": "skeptical framing that pressure-tests consensus bullishness",
    }[run.warmer_slug]
    return "\n".join(
        [
            "Thesis",
            f"The memo uses {warmer_note}.",
            f"The synthetic quality marker is {quality_score}; higher markers should win reviews.",
            "Variant View",
            "The memo separates mine disruption, concentrate availability, smelter behavior, refined inventories, and trade expression.",
            "Risks",
            "Key risks include faster mine restart, demand softness, crowded positioning, and spot/futures basis confusion.",
            "Watch Items",
            "Track treatment charges, exchange inventories, Chile port data, Chinese import appetite, and equity dispersion.",
        ]
    )


def _complete_sample_reviews(session: Session, *, review_set: ReviewSet) -> None:
    for item in sorted(review_set.items, key=lambda review_item: review_item.id):
        answers = list((item.answer_snapshot or {}).get("answers") or [])
        if len(answers) != 2:
            continue
        scored = [
            (answer["label"], _review_quality(session.get(RunAttempt, int(answer["run_attempt_id"]))))
            for answer in answers
        ]
        winner = max(scored, key=lambda item_score: (item_score[1], item_score[0]))[0]
        pass_fail = {label: quality >= 10 for label, quality in scored}
        failure_tags = {
            label: _failure_tags_for_quality(quality) if not pass_fail[label] else []
            for label, quality in scored
        }
        rubric_notes = {
            label: f"synthetic quality score {quality}; {'passes' if pass_fail[label] else 'needs sharper memo mechanics'}"
            for label, quality in scored
        }
        repositories.record_review_decision(
            session,
            review_item=item,
            reviewer_id=DEMO_REVIEWER_ID,
            winner=winner,
            pass_fail=pass_fail,
            failure_tags=failure_tags,
            rubric_notes=rubric_notes,
            notes="Synthetic local review for the copper memo Phase 12 demo.",
            confidence=0.9,
        )


def _review_quality(attempt: RunAttempt | None) -> int:
    if attempt is None:
        return 0
    value = (attempt.response_payload or {}).get("quality_score")
    return int(value) if isinstance(value, int | float) else 0


def _failure_tags_for_quality(quality: int) -> list[str]:
    if quality <= 8:
        return ["too generic", "missed transmission mechanism"]
    if quality == 9:
        return ["weak trade expression"]
    return ["weak risks"]


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


def _attempts(session: Session, experiment: Experiment) -> list[RunAttempt]:
    return session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id).order_by(RunAttempt.id)
    ).all()


def _runs(session: Session, experiment: Experiment) -> list[Run]:
    return session.scalars(
        select(Run).where(Run.experiment_id == experiment.id).order_by(Run.id)
    ).all()
