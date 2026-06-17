from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from model_eval_api.deterministic_evaluators import evaluate_attempt
from model_eval_api.execution_states import AttemptStatus
from model_eval_api.executor import execute_experiment
from model_eval_api.llm_judges import run_llm_judge
from model_eval_api.manifest import expand_manifest, load_manifest_file
from model_eval_api.metric_adapter_execution import run_metric_adapters_for_experiment
from model_eval_api.otel_export import build_experiment_trace
from model_eval_api.persistence import repositories
from model_eval_api.persistence.database import get_engine
from model_eval_api.persistence.models import (
    Base,
    Experiment,
    Project,
    ReviewAssignment,
    ReviewSet,
    Run,
    RunAttempt,
    Score,
    Workspace,
)
from model_eval_api.providers import ProviderExecutionConfig
from model_eval_api.promptfoo import export_experiment_to_promptfoo
from model_eval_api.response_payloads import attempt_output_text
from model_eval_api.results_analytics import aggregate_experiment_results


EXPORT_FORMAT_VERSION = "model_eval_export_v1"
CSV_HEADER = [
    "section",
    "id",
    "parent_id",
    "experiment_id",
    "run_id",
    "attempt_id",
    "case_slug",
    "model_config_slug",
    "system_prompt_slug",
    "warmer_slug",
    "replicate_index",
    "replicate_group_id",
    "attempt_kind",
    "status",
    "type",
    "evaluator_type",
    "criterion",
    "metric_source",
    "source_kind",
    "label",
    "reviewer_id",
    "assignment_status",
    "taxonomy_version",
    "value",
    "explanation",
    "warning",
    "warning_label",
    "cost_usd",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "sample_count",
    "variance",
    "interval_lower",
    "interval_upper",
    "uncertainty_label",
    "suite_slug",
    "suite_split",
    "quality_metric",
    "quality_rate",
    "dominance_status",
    "dominated_by",
    "is_frontier",
    "frontier_key",
    "quality_interval_lower",
    "quality_interval_upper",
    "cost_interval_lower",
    "cost_interval_upper",
    "latency_interval_lower",
    "latency_interval_upper",
    "promptfoo_provider_id",
    "promptfoo_prompt_id",
    "promptfoo_test_description",
    "promptfoo_assertion_types",
]


def ensure_database_schema() -> None:
    Base.metadata.create_all(get_engine())


def run_manifest(
    session: Session,
    manifest_path: Path,
    *,
    dry_run: bool = True,
    local_only: bool = True,
    project_slug: str = "default",
) -> dict[str, Any]:
    manifest = load_manifest_file(manifest_path)
    preview = expand_manifest(manifest)
    project = _get_or_create_project(session, project_slug)
    experiment = session.scalar(
        select(Experiment).where(
            Experiment.project_id == project.id,
            Experiment.slug == manifest.experiment_id,
        )
    )
    if experiment is None:
        experiment = repositories.create_experiment_from_manifest(
            session,
            project=project,
            manifest=manifest,
            preview=preview,
        )
        session.flush()
    elif experiment.manifest_snapshot != repositories.snapshot_manifest(manifest):
        raise ValueError(
            f"Experiment '{manifest.experiment_id}' already exists with a different manifest."
        )
    stored_controls_snapshot = dict(experiment.controls_snapshot or {})
    experiment.controls_snapshot = {**stored_controls_snapshot, "local_only": local_only}
    execute_experiment(
        session,
        experiment.id,
        dry_run=dry_run,
        provider_config=ProviderExecutionConfig(local_only=local_only),
    )
    experiment.controls_snapshot = stored_controls_snapshot
    session.commit()
    attempts = _experiment_attempts(session, experiment)
    return {
        "experiment": _experiment_summary(experiment),
        "dry_run": dry_run,
        "local_only": local_only,
        "preview": preview.model_dump(mode="json"),
        "execution": {
            "runs": len(experiment.runs),
            "attempts": len(attempts),
            "succeeded_attempts": sum(
                1 for attempt in attempts if attempt.status == AttemptStatus.SUCCEEDED.value
            ),
            "failed_attempts": sum(
                1 for attempt in attempts if attempt.status == AttemptStatus.FAILED.value
            ),
            "live_provider_calls": sum(
                1
                for attempt in attempts
                if attempt.status == AttemptStatus.SUCCEEDED.value
                and not (attempt.response_payload or {}).get("dry_run")
            ),
        },
    }


def run_suite(
    session: Session,
    suite_ref: int | str,
    *,
    split: str | None = None,
    dry_run: bool = True,
    local_only: bool = True,
    project_slug: str = "default",
) -> dict[str, Any]:
    project = _get_or_create_project(session, project_slug)
    result = repositories.run_benchmark_suite(
        session,
        project=project,
        suite_ref=suite_ref,
        split=split,
        dry_run=dry_run,
        local_only=local_only,
    )
    attempts = _experiment_attempts(session, result["experiment_record"])
    return {
        "suite": {
            "id": result["suite"].id,
            "slug": result["suite"].slug,
            "name": result["suite"].name,
            "version": result["suite"].version,
        },
        "split": result["manifest"].design.split,
        "dry_run": dry_run,
        "local_only": local_only,
        "suite_snapshot": result["suite_snapshot"],
        "manifest": result["manifest"].model_dump(mode="json"),
        "preview": result["preview"].model_dump(mode="json"),
        "experiment": result["experiment"],
        "execution": {
            "runs": len(result["experiment_record"].runs),
            "attempts": len(attempts),
            "succeeded_attempts": sum(
                1 for attempt in attempts if attempt.status == AttemptStatus.SUCCEEDED.value
            ),
            "failed_attempts": sum(
                1 for attempt in attempts if attempt.status == AttemptStatus.FAILED.value
            ),
            "live_provider_calls": sum(
                1
                for attempt in attempts
                if attempt.status == AttemptStatus.SUCCEEDED.value
                and not (attempt.response_payload or {}).get("dry_run")
            ),
        },
    }


def compare_experiments(
    session: Session,
    experiment_ref: int | str,
    baseline_ref: int | str,
) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    baseline = _resolve_experiment(session, baseline_ref)
    experiment_summary = aggregate_experiment_results(session, experiment_id=experiment.id)["summary"]
    baseline_summary = aggregate_experiment_results(session, experiment_id=baseline.id)["summary"]
    return {
        "experiment": _experiment_summary(experiment),
        "baseline": _experiment_summary(baseline),
        "summary": {
            "experiment": experiment_summary,
            "baseline": baseline_summary,
        },
        "delta": {
            key: _delta(experiment_summary.get(key), baseline_summary.get(key))
            for key in (
                "pass_rate",
                "win_rate",
                "failure_rate",
                "average_cost_usd",
                "average_latency_ms",
            )
        },
    }


def score_experiment(session: Session, experiment_ref: int | str, evaluator_id: str) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    snapshot = (experiment.evaluator_snapshots or {}).get(evaluator_id)
    if snapshot is None:
        raise ValueError(f"Evaluator '{evaluator_id}' was not found on experiment {experiment.id}.")
    attempts = [
        attempt
        for attempt in _experiment_attempts(session, experiment)
        if attempt.status == AttemptStatus.SUCCEEDED.value
        and not (attempt.response_payload or {}).get("dry_run")
    ]
    recorded = 0
    for attempt in attempts:
        for result in evaluate_attempt(attempt, snapshot):
            value = {**result.value, "evaluator_id": evaluator_id}
            if _score_exists(session, attempt, result.criterion, _version(snapshot), evaluator_id):
                continue
            repositories.record_score(
                session,
                run_attempt=attempt,
                type=result.type,
                evaluator_type="code",
                criterion=result.criterion,
                value=value,
                explanation=result.explanation,
                confidence=result.confidence,
                evaluator_version=_version(snapshot),
            )
            recorded += 1
    session.commit()
    return {
        "experiment_id": experiment.id,
        "evaluator_id": evaluator_id,
        "attempts_evaluated": len(attempts),
        "scores_recorded": recorded,
    }


def judge_experiment(
    session: Session,
    experiment_ref: int | str,
    evaluator_id: str,
    *,
    dry_run: bool = True,
    local_only: bool = True,
    position_swap: bool = True,
) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    payload = run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id=evaluator_id,
        dry_run=dry_run,
        local_only=local_only,
        position_swap=position_swap,
    )
    session.commit()
    return payload


def run_metric_adapters(
    session: Session,
    experiment_ref: int | str,
    *,
    adapter_config_slug: str | None = None,
    adapter_config_version: int | None = None,
    dry_run: bool = False,
    local_only: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    payload = run_metric_adapters_for_experiment(
        session,
        experiment_id=experiment.id,
        adapter_config_slug=adapter_config_slug,
        adapter_config_version=adapter_config_version,
        dry_run=dry_run,
        local_only=local_only,
        force=force,
    )
    session.commit()
    return payload


def export_blind_review_queue(
    session: Session,
    experiment_ref: int | str,
    *,
    random_seed: int | None = 1,
) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    review_set = _first_blind_review_set(session, experiment)
    if review_set is None:
        review_set = repositories.create_review_set_from_completed_experiment(
            session,
            project=experiment.project,
            experiment=experiment,
            slug=f"{experiment.slug}-blind-review",
            name=f"{experiment.name} blind review",
            random_seed=random_seed,
        )
        session.commit()
    payload = {
        "format_version": EXPORT_FORMAT_VERSION,
        "review_set": {
            "id": review_set.id,
            "slug": review_set.slug,
            "name": review_set.name,
            "review_type": review_set.review_type,
            "metadata": _blind_review_metadata(review_set.metadata_json or {}),
        },
        "items": [_blind_review_item(item) for item in sorted(review_set.items, key=lambda item: item.id)],
    }
    repositories.record_audit_event(
        session,
        experiment=experiment,
        event_kind="blind_review_queue_exported",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={"review_set_id": review_set.id, "item_count": len(payload["items"])},
    )
    session.commit()
    return payload


def export_experiment(
    session: Session,
    experiment_ref: int | str,
    export_format: str,
    *,
    case_slug: str | None = None,
    suite_slug: str | None = None,
    suite_split: str | None = None,
    model_config_slug: str | None = None,
    system_prompt_slug: str | None = None,
    warmer_slug: str | None = None,
    evaluator_source: str | None = None,
    reviewer_id: str | None = None,
) -> str:
    experiment = _resolve_experiment(session, experiment_ref)
    analytics_filters = _analytics_filter_kwargs(
        case_slug=case_slug,
        suite_slug=suite_slug,
        suite_split=suite_split,
        model_config_slug=model_config_slug,
        system_prompt_slug=system_prompt_slug,
        warmer_slug=warmer_slug,
        evaluator_source=evaluator_source,
        reviewer_id=reviewer_id,
    )
    if export_format == "json":
        payload = json.dumps(
            _export_payload(session, experiment, analytics_filters=analytics_filters),
            indent=2,
            sort_keys=False,
            default=str,
        )
    elif export_format == "csv":
        payload = _csv_export(session, experiment, analytics_filters=analytics_filters)
    elif export_format == "markdown":
        payload = _markdown_export(session, experiment, analytics_filters=analytics_filters)
    elif export_format == "promptfoo":
        payload = export_experiment_to_promptfoo(experiment).content
    elif export_format == "otel-json":
        repositories.record_audit_event(
            session,
            experiment=experiment,
            event_kind="export_generated",
            entity_type="experiment",
            entity_id=str(experiment.id),
            details={"format": export_format},
        )
        session.flush()
        payload = json.dumps(build_experiment_trace(session, experiment.id), indent=2)
        session.commit()
        return payload
    else:
        raise ValueError("Export format must be markdown, csv, json, promptfoo, or otel-json.")
    repositories.record_audit_event(
        session,
        experiment=experiment,
        event_kind="export_generated",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={"format": export_format},
    )
    session.commit()
    return payload


def export_experiment_response(
    session: Session,
    experiment_ref: int | str,
    export_format: str,
    *,
    case_slug: str | None = None,
    suite_slug: str | None = None,
    suite_split: str | None = None,
    model_config_slug: str | None = None,
    system_prompt_slug: str | None = None,
    warmer_slug: str | None = None,
    evaluator_source: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    if export_format == "promptfoo":
        exported = export_experiment_to_promptfoo(experiment)
        payload = exported.to_payload()
        repositories.record_audit_event(
            session,
            experiment=experiment,
            event_kind="export_generated",
            entity_type="experiment",
            entity_id=str(experiment.id),
            details={"format": export_format},
        )
        session.commit()
        return payload
    return {
        "format": export_format,
        "content": export_experiment(
            session,
            experiment.id,
            export_format,
            case_slug=case_slug,
            suite_slug=suite_slug,
            suite_split=suite_split,
            model_config_slug=model_config_slug,
            system_prompt_slug=system_prompt_slug,
            warmer_slug=warmer_slug,
            evaluator_source=evaluator_source,
            reviewer_id=reviewer_id,
        ),
        "warnings": [],
    }


def export_experiment_trace(session: Session, experiment_ref: int | str) -> dict[str, Any]:
    return build_experiment_trace(session, experiment_ref)


def _analytics_filter_kwargs(
    *,
    case_slug: str | None = None,
    suite_slug: str | None = None,
    suite_split: str | None = None,
    model_config_slug: str | None = None,
    system_prompt_slug: str | None = None,
    warmer_slug: str | None = None,
    evaluator_source: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "case_slug": case_slug,
        "suite_slug": suite_slug,
        "suite_split": suite_split,
        "model_config_slug": model_config_slug,
        "system_prompt_slug": system_prompt_slug,
        "warmer_slug": warmer_slug,
        "evaluator_source": evaluator_source,
        "reviewer_id": reviewer_id,
    }


def _export_payload(
    session: Session, experiment: Experiment, *, analytics_filters: dict[str, str | None]
) -> dict[str, Any]:
    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "experiment": _experiment_summary(experiment),
        "reproducibility": {
            "includes_manifest_snapshot": True,
            "includes_versioned_library_snapshots": True,
            "includes_run_attempt_payloads": True,
            "ordering": "stable_by_database_id",
        },
        "snapshots": {
            "manifest": experiment.manifest_snapshot,
            "design": experiment.design_snapshot,
            "controls": experiment.controls_snapshot,
            "pricing": experiment.pricing_snapshot,
            "cases": experiment.case_snapshots,
            "artifacts": experiment.artifact_snapshots,
            "system_prompts": experiment.system_prompt_snapshots,
            "warmers": experiment.warmer_snapshots,
            "model_configs": experiment.model_config_snapshots,
            "evaluators": experiment.evaluator_snapshots,
        },
        "runs": [_run_payload(run) for run in _experiment_runs(session, experiment)],
        "attempts": [_attempt_payload(attempt) for attempt in _experiment_attempts(session, experiment)],
        "scores": [_score_payload(score) for score in _experiment_scores(session, experiment)],
        "reviews": [_review_set_payload(review_set) for review_set in _experiment_review_sets(session, experiment)],
        "analytics": aggregate_experiment_results(
            session, experiment_id=experiment.id, **analytics_filters
        ),
    }


def _csv_export(
    session: Session, experiment: Experiment, *, analytics_filters: dict[str, str | None]
) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADER, lineterminator="\n")
    writer.writeheader()
    for row in _csv_rows(session, experiment, analytics_filters=analytics_filters):
        writer.writerow({key: row.get(key, "") for key in CSV_HEADER})
    return output.getvalue()


def _csv_rows(
    session: Session, experiment: Experiment, *, analytics_filters: dict[str, str | None]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in _experiment_runs(session, experiment):
        rows.append(
            {
                "section": "run",
                "id": run.id,
                "experiment_id": experiment.id,
                "run_id": run.run_id,
                "case_slug": run.case_slug,
                "model_config_slug": run.model_config_slug,
                "system_prompt_slug": run.system_prompt_slug,
                "warmer_slug": run.warmer_slug,
                "status": run.status,
            }
        )
    for attempt in _experiment_attempts(session, experiment):
        rows.append(
            {
                "section": "attempt",
                "id": attempt.id,
                "parent_id": attempt.run_id,
                "experiment_id": experiment.id,
                "run_id": attempt.run.run_id,
                "attempt_id": attempt.attempt_id,
                "case_slug": attempt.run.case_slug,
                "model_config_slug": attempt.run.model_config_slug,
                "system_prompt_slug": attempt.run.system_prompt_slug,
                "warmer_slug": attempt.run.warmer_slug,
                "replicate_index": attempt.replicate_index,
                "replicate_group_id": attempt.replicate_group_id,
                "attempt_kind": attempt.attempt_kind,
                "status": attempt.status,
                "cost_usd": attempt.cost_usd,
                "latency_ms": attempt.latency_ms,
                "input_tokens": attempt.input_tokens,
                "output_tokens": attempt.output_tokens,
                "total_tokens": attempt.total_tokens,
            }
        )
    for score in _experiment_scores(session, experiment):
        rows.append(
            {
                "section": "score",
                "id": score.id,
                "parent_id": score.run_attempt_id,
                "experiment_id": experiment.id,
                "run_id": score.run_attempt.run.run_id,
                "attempt_id": score.run_attempt.attempt_id,
                "case_slug": score.run_attempt.run.case_slug,
                "model_config_slug": score.run_attempt.run.model_config_slug,
                "system_prompt_slug": score.run_attempt.run.system_prompt_slug,
                "warmer_slug": score.run_attempt.run.warmer_slug,
                "type": score.type,
                "evaluator_type": score.evaluator_type,
                "criterion": score.criterion,
                "metric_source": (score.value or {}).get("metric_source"),
                "source_kind": (score.value or {}).get("source_kind"),
                "label": (score.value or {}).get("label"),
                "reviewer_id": (score.value or {}).get("reviewer_id"),
                "taxonomy_version": (score.value or {}).get("taxonomy_version"),
                "value": _json_cell(score.value),
                "explanation": score.explanation,
            }
        )
    for review_set in _experiment_review_sets(session, experiment):
        for item in sorted(review_set.items, key=lambda item: item.id):
            rows.append(
                {
                    "section": "review",
                    "id": item.id,
                    "parent_id": review_set.id,
                    "experiment_id": experiment.id,
                    "type": review_set.review_type,
                    "value": _json_cell(item.reviewer_decision),
                }
            )
        for assignment in sorted(review_set.assignments, key=lambda assignment: assignment.id):
            rows.append(
                {
                    "section": "review_assignment",
                    "id": assignment.id,
                    "parent_id": assignment.review_set_id,
                    "experiment_id": experiment.id,
                    "type": review_set.review_type,
                    "reviewer_id": assignment.reviewer.slug,
                    "assignment_status": assignment.status,
                    "taxonomy_version": (assignment.taxonomy_snapshot or {}).get("version"),
                    "value": _json_cell(assignment.decision_snapshot),
                }
            )
    analytics = aggregate_experiment_results(
        session, experiment_id=experiment.id, **analytics_filters
    )
    rows.append(
        {
            "section": "aggregate_summary",
            "id": "summary",
            "experiment_id": experiment.id,
            "value": _json_cell(analytics["summary"]),
        }
    )
    for index, item in enumerate(analytics["failure_tag_frequency"], start=1):
        rows.append(
            {
                "section": "aggregate_failure_tag",
                "id": index,
                "experiment_id": experiment.id,
                "label": item["tag"],
                "value": _json_cell(item),
            }
        )
    for index, item in enumerate(analytics.get("divergence_summary") or [], start=1):
        rows.append(
            {
                "section": "aggregate_divergence",
                "id": f"{item['criterion']}:{index}",
                "experiment_id": experiment.id,
                "case_slug": item["case_slug"],
                "model_config_slug": item["model_config_slug"],
                "system_prompt_slug": item["system_prompt_slug"],
                "warmer_slug": item["warmer_slug"],
                "type": "divergence_metric",
                "evaluator_type": item["source_kind"],
                "criterion": item["criterion"],
                "metric_source": item["metric_source"],
                "source_kind": item["source_kind"],
                "label": item["label"],
                "value": _json_cell(item),
                "explanation": item.get("warning"),
                "warning": item.get("warning"),
                "warning_label": item.get("warning_label"),
                "sample_count": item["sample_count"],
            }
        )
    for index, item in enumerate(analytics.get("carryover_summary") or [], start=1):
        rows.append(
            {
                "section": "aggregate_carryover",
                "id": f"{item['source_evidence']}:{item['status']}:{index}",
                "experiment_id": experiment.id,
                "case_slug": item["case_slug"],
                "model_config_slug": item["model_config_slug"],
                "system_prompt_slug": item["system_prompt_slug"],
                "warmer_slug": item["warmer_slug"],
                "type": "carryover_audit",
                "evaluator_type": item["source_kind"],
                "criterion": "carryover_status",
                "metric_source": item["source_evidence"],
                "source_kind": item["source_kind"],
                "label": item["status"],
                "value": _json_cell(item),
                "explanation": item.get("warning"),
                "warning": item.get("warning"),
                "warning_label": item.get("warning_label"),
                "sample_count": item["sample_count"],
            }
        )
    for item in analytics.get("cost_quality_frontier") or []:
        quality_interval = dict(item.get("quality_interval") or {})
        cost_interval = dict(item.get("cost_usd_interval") or {})
        latency_interval = dict(item.get("latency_ms_interval") or {})
        rows.append(
            {
                "section": "aggregate_frontier",
                "id": item["frontier_key"],
                "experiment_id": experiment.id,
                "case_slug": item["case_slug"],
                "model_config_slug": item["model_config_slug"],
                "system_prompt_slug": item["system_prompt_slug"],
                "warmer_slug": item["warmer_slug"],
                "type": "cost_quality_frontier",
                "label": item["dominance_status"],
                "value": _json_cell(item),
                "cost_usd": item["average_cost_usd"],
                "latency_ms": item["average_latency_ms"],
                "sample_count": item["attempt_count"],
                "interval_lower": quality_interval.get("lower"),
                "interval_upper": quality_interval.get("upper"),
                "uncertainty_label": quality_interval.get("label"),
                "suite_slug": item["suite_slug"],
                "suite_split": item["suite_split"],
                "quality_metric": item["quality_metric"],
                "quality_rate": item["quality_rate"],
                "dominance_status": item["dominance_status"],
                "dominated_by": item["dominated_by"],
                "is_frontier": item["is_frontier"],
                "frontier_key": item["frontier_key"],
                "quality_interval_lower": quality_interval.get("lower"),
                "quality_interval_upper": quality_interval.get("upper"),
                "cost_interval_lower": cost_interval.get("lower"),
                "cost_interval_upper": cost_interval.get("upper"),
                "latency_interval_lower": latency_interval.get("lower"),
                "latency_interval_upper": latency_interval.get("upper"),
                "promptfoo_provider_id": item["promptfoo_provider_id"],
                "promptfoo_prompt_id": item["promptfoo_prompt_id"],
                "promptfoo_test_description": item["promptfoo_test_description"],
                "promptfoo_assertion_types": _json_cell(item["promptfoo_assertion_types"]),
            }
        )
    rows.extend(_uncertainty_csv_rows(experiment, analytics))
    return rows


def _markdown_export(
    session: Session, experiment: Experiment, *, analytics_filters: dict[str, str | None]
) -> str:
    analytics = aggregate_experiment_results(
        session, experiment_id=experiment.id, **analytics_filters
    )
    summary = analytics["summary"]
    lines = [
        f"# Experiment export: {experiment.name}",
        "",
        "## Summary",
        "",
        f"- Status: `{experiment.status}`",
        f"- Runs: {len(experiment.runs)}",
        f"- Attempts: {summary['attempt_count']}",
        f"- Pass rate: {_rate(summary['pass_rate'])}",
        f"- Win rate: {_rate(summary['win_rate'])}",
        f"- Failure rate: {_rate(summary['failure_rate'])}",
        "",
        "## Configs",
        "",
        f"- Models: {', '.join(sorted(experiment.model_config_snapshots)) or 'none'}",
        f"- System prompts: {', '.join(sorted(experiment.system_prompt_snapshots)) or 'none'}",
        f"- Warmers: {', '.join(sorted(experiment.warmer_snapshots)) or 'none'}",
        "",
        "## Scores",
        "",
    ]
    scores = _experiment_scores(session, experiment)
    if scores:
        for score in scores[:10]:
            lines.append(f"- {score.type} / {score.criterion}: `{_json_cell(score.value)}`")
    else:
        lines.append("- No scores recorded.")
    lines.extend(
        [
            "",
            "## Costs",
            "",
            f"- Average cost: {_currency(summary['average_cost_usd'])}",
            f"- Total tokens: {summary['token_totals']['total_tokens']}",
            "",
            "## Cost-Quality Frontier",
            "",
        ]
    )
    if analytics.get("cost_quality_frontier"):
        for item in analytics["cost_quality_frontier"]:
            lines.append(
                f"- `{item['dominance_status']}` {item['case_slug']}/"
                f"{item['model_config_slug']}/{item['system_prompt_slug']}/"
                f"{item['warmer_slug']}: {item['quality_metric'] or 'quality'} "
                f"{_rate(item['quality_rate'])}, cost "
                f"{_currency(item['average_cost_usd'])}, latency "
                f"{_latency_ms(item['average_latency_ms'])}"
            )
    else:
        lines.append("- No frontier rows recorded.")
    lines.extend(
        [
            "",
            "## Failure Tags",
            "",
        ]
    )
    if analytics["failure_tag_frequency"]:
        for item in analytics["failure_tag_frequency"]:
            lines.append(f"- {item['tag']}: {item['count']} ({_rate(item['rate'])})")
    else:
        lines.append("- No failure tags recorded.")
    lines.extend(["", "## Divergence Metrics", ""])
    if analytics.get("divergence_summary"):
        for item in analytics["divergence_summary"]:
            lines.append(
                f"- `{item['criterion']}` {item['case_slug']}/{item['model_config_slug']}/"
                f"{item['system_prompt_slug']}/{item['warmer_slug']}: "
                f"{item['sample_count']} sample(s), "
                f"{_plain_label(item['source_kind'])}, {item['label']}"
            )
    else:
        lines.append("- No divergence metrics recorded.")
    lines.extend(["", "## Carryover Audit", ""])
    if analytics.get("carryover_summary"):
        for item in analytics["carryover_summary"]:
            lines.append(
                f"- `{item['status']}` {item['case_slug']}/{item['model_config_slug']}/"
                f"{item['system_prompt_slug']}/{item['warmer_slug']}: "
                f"{item['sample_count']} sample(s), "
                f"{_plain_label(item['source_kind'])}"
            )
    else:
        lines.append("- No carryover audit rows recorded.")
    lines.extend(["", "## Key Examples", ""])
    for attempt in _experiment_attempts(session, experiment)[:3]:
        lines.append(
            f"- `{attempt.attempt_id}` {attempt.run.model_config_slug}/{attempt.run.warmer_slug}: "
            f"{attempt_output_text(attempt)[:160] or 'no output'}"
        )
    return "\n".join(lines) + "\n"


def _uncertainty_csv_rows(experiment: Experiment, analytics: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension, values in (analytics.get("nondeterminism_by_dimension") or {}).items():
        for index, item in enumerate(values, start=1):
            interval = dict(item.get("failure_rate_interval") or {})
            rows.append(
                {
                    "section": "aggregate_uncertainty",
                    "id": f"{dimension}:{index}:reliability",
                    "experiment_id": experiment.id,
                    "label": f"{dimension}={item.get(dimension)}",
                    "value": _json_cell(item),
                    "sample_count": interval.get("sample_count"),
                    "variance": interval.get("variance"),
                    "interval_lower": interval.get("lower"),
                    "interval_upper": interval.get("upper"),
                    "uncertainty_label": interval.get("label"),
                }
            )
    return rows


def _get_or_create_project(session: Session, slug: str):
    workspace = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if workspace is None:
        workspace = repositories.create_workspace(session, slug="default", name="Default")
        session.flush()
    project = session.scalar(
        select(Project).where(
            Project.workspace_id == workspace.id,
            Project.slug == slug,
        )
    )
    if project is None:
        project = repositories.create_project(session, workspace=workspace, slug=slug, name=slug.title())
        session.flush()
    return project


def _resolve_experiment(session: Session, ref: int | str) -> Experiment:
    experiment_id = _int_ref(ref)
    if experiment_id is not None:
        experiment = session.get(Experiment, experiment_id)
        if experiment is not None:
            return experiment
        if isinstance(ref, int):
            raise ValueError(f"Experiment '{ref}' was not found.")
    matches = session.scalars(
        select(Experiment).where(Experiment.slug == str(ref)).order_by(Experiment.id)
    ).all()
    if len(matches) > 1:
        raise ValueError(
            f"Experiment slug '{ref}' matches multiple projects; use the numeric experiment id."
        )
    experiment = matches[0] if matches else None
    if experiment is None:
        raise ValueError(f"Experiment '{ref}' was not found.")
    return experiment


def _int_ref(ref: int | str) -> int | None:
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str) and ref.isdigit():
        return int(ref)
    return None


def _experiment_summary(experiment: Experiment) -> dict[str, Any]:
    return {
        "id": experiment.id,
        "slug": experiment.slug,
        "name": experiment.name,
        "status": experiment.status,
        "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
    }


def _experiment_runs(session: Session, experiment: Experiment) -> list[Run]:
    return session.scalars(
        select(Run).where(Run.experiment_id == experiment.id).order_by(Run.id)
    ).all()


def _experiment_attempts(session: Session, experiment: Experiment) -> list[RunAttempt]:
    return session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id).order_by(RunAttempt.id)
    ).all()


def _experiment_scores(session: Session, experiment: Experiment) -> list[Score]:
    return session.scalars(
        select(Score)
        .join(RunAttempt)
        .join(Run)
        .where(Run.experiment_id == experiment.id)
        .order_by(Score.id)
    ).all()


def _experiment_review_sets(session: Session, experiment: Experiment) -> list[ReviewSet]:
    return session.scalars(
        select(ReviewSet)
        .options(
            selectinload(ReviewSet.items),
            selectinload(ReviewSet.assignments).selectinload(ReviewAssignment.reviewer),
        )
        .where(ReviewSet.experiment_id == experiment.id)
        .order_by(ReviewSet.id)
    ).all()


def _run_payload(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_id": run.run_id,
        "case_slug": run.case_slug,
        "model_config_slug": run.model_config_slug,
        "system_prompt_slug": run.system_prompt_slug,
        "warmer_slug": run.warmer_slug,
        "status": run.status,
        "data_egress_label": run.data_egress_label,
        "context_report": run.context_report,
        "truncation_policy": run.truncation_policy,
        "run_snapshot": run.run_snapshot,
        "model_input_snapshot": run.model_input_snapshot,
    }


def _attempt_payload(attempt: RunAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "run_id": attempt.run_id,
        "attempt_id": attempt.attempt_id,
        "replicate_index": attempt.replicate_index,
        "replicate_group_id": attempt.replicate_group_id,
        "attempt_kind": attempt.attempt_kind,
        "status": attempt.status,
        "provider": attempt.provider,
        "model": attempt.model,
        "request_payload": attempt.request_payload,
        "response_payload": attempt.response_payload,
        "provider_response_id": attempt.provider_response_id,
        "provider_timestamp": attempt.provider_timestamp.isoformat()
        if attempt.provider_timestamp
        else None,
        "pricing_snapshot": attempt.pricing_snapshot,
        "provider_metadata": attempt.provider_metadata,
        "system_fingerprint": attempt.system_fingerprint,
        "error_kind": attempt.error_kind,
        "terminal_failure_reason": attempt.terminal_failure_reason,
        "latency_ms": attempt.latency_ms,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "total_tokens": attempt.total_tokens,
        "cost_usd": attempt.cost_usd,
        "cache_hit": attempt.cache_hit,
    }


def _score_payload(score: Score) -> dict[str, Any]:
    return {
        "id": score.id,
        "run_attempt_id": score.run_attempt_id,
        "type": score.type,
        "evaluator_type": score.evaluator_type,
        "criterion": score.criterion,
        "value": score.value,
        "explanation": score.explanation,
        "confidence": score.confidence,
        "evaluator_version": score.evaluator_version,
    }


def _review_set_payload(review_set: ReviewSet) -> dict[str, Any]:
    return {
        "id": review_set.id,
        "slug": review_set.slug,
        "name": review_set.name,
        "review_type": review_set.review_type,
        "metadata": review_set.metadata_json,
        "assignments": [
            _review_assignment_payload(assignment)
            for assignment in sorted(review_set.assignments, key=lambda item: item.id)
        ],
        "items": [
            {
                "id": item.id,
                "item_key": item.item_key,
                "prompt_snapshot": item.prompt_snapshot,
                "answer_snapshot": item.answer_snapshot,
                "metadata": item.metadata_json,
                "reviewer_decision": item.reviewer_decision,
            }
            for item in sorted(review_set.items, key=lambda item: item.id)
        ],
    }


def _review_assignment_payload(assignment: ReviewAssignment) -> dict[str, Any]:
    return {
        "id": assignment.id,
        "review_set_id": assignment.review_set_id,
        "review_item_id": assignment.review_item_id,
        "reviewer_id": assignment.reviewer.slug,
        "status": assignment.status,
        "taxonomy_snapshot": assignment.taxonomy_snapshot,
        "decision_snapshot": assignment.decision_snapshot,
    }


def _first_blind_review_set(session: Session, experiment: Experiment) -> ReviewSet | None:
    return session.scalar(
        select(ReviewSet)
        .where(
            ReviewSet.experiment_id == experiment.id,
            ReviewSet.review_type.in_(["blind", "blind_pairwise"]),
        )
        .order_by(ReviewSet.id)
    )


def _blind_review_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in ("blind", "failure_tags", "random_seed")
        if key in metadata
    }


def _blind_review_item(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "item_key": f"review-item-{item.id}",
        "prompt": dict(item.prompt_snapshot or {}),
        "answers": [
            {"label": answer.get("label"), "text": answer.get("text", "")}
            for answer in (item.answer_snapshot or {}).get("answers") or []
        ],
    }


def _score_exists(
    session: Session,
    attempt: RunAttempt,
    criterion: str,
    evaluator_version: int | None,
    evaluator_id: str,
) -> bool:
    scores = session.scalars(
        select(Score).where(
            Score.run_attempt_id == attempt.id,
            Score.evaluator_type == "code",
            Score.criterion == criterion,
            Score.evaluator_version == evaluator_version,
        )
    ).all()
    return any((score.value or {}).get("evaluator_id") == evaluator_id for score in scores)


def _version(snapshot: dict[str, Any]) -> int | None:
    value = snapshot.get("version")
    return value if isinstance(value, int) else None


def _delta(value: Any, baseline: Any) -> float | None:
    if isinstance(value, (int, float)) and isinstance(baseline, (int, float)):
        return float(value) - float(baseline)
    return None


def _json_cell(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str)


def _rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{round(value * 100)}%"


def _currency(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.3f}" if value < 1 else f"${value:.2f}"


def _latency_ms(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:g}ms"


def _plain_label(value: str) -> str:
    return value.replace("_", " ")
