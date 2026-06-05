from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from model_eval_api.persistence.models import (
    ArtifactPreprocessingRun,
    AuditLog,
    Experiment,
    ReviewAssignment,
    ReviewSet,
    Run,
    RunAttempt,
    Score,
)


TRACE_FORMAT_VERSION = "model_eval_otel_trace_v1"


def build_experiment_trace(session: Session, experiment_ref: int | str) -> dict[str, Any]:
    experiment = _resolve_experiment(session, experiment_ref)
    trace_id = _trace_id(experiment)
    root_key = f"experiment:{experiment.id}"
    spans: list[dict[str, Any]] = [
        _span(
            trace_id=trace_id,
            key=root_key,
            name="model_eval.experiment",
            parent_key=None,
            start_time=experiment.created_at,
            end_time=None,
            attributes={
                "model_eval.experiment.id": experiment.id,
                "model_eval.experiment.slug": experiment.slug,
                "model_eval.experiment.version": experiment.version,
                "model_eval.experiment.status": experiment.status,
                "model_eval.project.id": experiment.project_id,
                "model_eval.run_count": len(experiment.runs),
                "model_eval.attempt_count": sum(len(run.attempts) for run in experiment.runs),
            },
        )
    ]
    spans.extend(_run_spans(trace_id, experiment, parent_key=root_key))
    spans.extend(_score_spans(session, trace_id, experiment))
    spans.extend(_review_spans(session, trace_id, experiment, parent_key=root_key))
    spans.extend(_artifact_preprocessing_spans(session, trace_id, experiment, parent_key=root_key))
    spans.extend(_export_event_spans(session, trace_id, experiment, parent_key=root_key))
    return {"format_version": TRACE_FORMAT_VERSION, "trace_id": trace_id, "spans": spans}


def _run_spans(trace_id: str, experiment: Experiment, *, parent_key: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for run in sorted(experiment.runs, key=lambda item: item.id):
        run_key = f"run:{run.id}"
        spans.append(
            _span(
                trace_id=trace_id,
                key=run_key,
                name="model_eval.run",
                parent_key=parent_key,
                start_time=run.created_at,
                end_time=None,
                attributes={
                    "model_eval.run.db_id": run.id,
                    "model_eval.run.id": run.run_id,
                    "model_eval.experiment.id": run.experiment_id,
                    "model_eval.case.slug": run.case_slug,
                    "model_eval.model_config.slug": run.model_config_slug,
                    "model_eval.system_prompt.slug": run.system_prompt_slug,
                    "model_eval.warmer.slug": run.warmer_slug,
                    "model_eval.run.status": run.status,
                    "model_eval.data_egress_label": run.data_egress_label,
                    "model_eval.truncation_policy": run.truncation_policy,
                },
            )
        )
        for attempt in sorted(run.attempts, key=lambda item: item.id):
            spans.append(_attempt_span(trace_id, attempt, parent_key=run_key))
    return spans


def _attempt_span(trace_id: str, attempt: RunAttempt, *, parent_key: str) -> dict[str, Any]:
    return _span(
        trace_id=trace_id,
        key=f"attempt:{attempt.id}",
        name="model_eval.run_attempt",
        parent_key=parent_key,
        start_time=attempt.started_at or attempt.created_at,
        end_time=attempt.completed_at,
        attributes={
            "model_eval.run_attempt.db_id": attempt.id,
            "model_eval.run_attempt.id": attempt.attempt_id,
            "model_eval.run.db_id": attempt.run_id,
            "model_eval.run_attempt.status": attempt.status,
            "model_eval.run_attempt.replicate_index": attempt.replicate_index,
            "model_eval.run_attempt.replicate_group_id": attempt.replicate_group_id,
            "model_eval.run_attempt.kind": attempt.attempt_kind,
            "model_eval.run_attempt.number": attempt.attempt_number,
            "model_eval.run_attempt.parent_attempt_id": attempt.parent_attempt_id,
            "model_eval.run_attempt.retry_after_seconds": attempt.retry_after_seconds,
            "model_eval.provider": attempt.provider,
            "model_eval.model": attempt.model,
            "model_eval.provider_response_id": attempt.provider_response_id,
            "model_eval.system_fingerprint": attempt.system_fingerprint
            or (attempt.provider_metadata or {}).get("system_fingerprint"),
            "model_eval.cache_hit": attempt.cache_hit,
            "model_eval.error_kind": attempt.error_kind,
            "model_eval.latency_ms": attempt.latency_ms,
            "model_eval.input_tokens": attempt.input_tokens,
            "model_eval.output_tokens": attempt.output_tokens,
            "model_eval.total_tokens": attempt.total_tokens,
            "model_eval.cost_usd": attempt.cost_usd,
        },
    )


def _score_spans(session: Session, trace_id: str, experiment: Experiment) -> list[dict[str, Any]]:
    scores = session.scalars(
        select(Score)
        .join(RunAttempt)
        .join(Run)
        .options(selectinload(Score.run_attempt).selectinload(RunAttempt.run))
        .where(Run.experiment_id == experiment.id)
        .order_by(Score.id)
    ).all()
    spans: list[dict[str, Any]] = []
    for score in scores:
        evaluator_kind = _score_span_name(score)
        if evaluator_kind is None:
            continue
        spans.append(
            _span(
                trace_id=trace_id,
                key=f"score:{score.id}",
                name=evaluator_kind,
                parent_key=f"attempt:{score.run_attempt_id}",
                start_time=score.created_at,
                end_time=None,
                attributes={
                    "model_eval.score.id": score.id,
                    "model_eval.run_attempt.db_id": score.run_attempt_id,
                    "model_eval.score.type": score.type,
                    "model_eval.evaluator.type": score.evaluator_type,
                    "model_eval.evaluator.criterion": score.criterion,
                    "model_eval.evaluator.version": score.evaluator_version,
                    "model_eval.score.confidence": score.confidence,
                },
            )
        )
    return spans


def _score_span_name(score: Score) -> str | None:
    evaluator_type = score.evaluator_type
    if evaluator_type == "llm_judge":
        return "model_eval.judge_evaluator"
    if evaluator_type == "human":
        return None
    return "model_eval.deterministic_evaluator"


def _review_spans(
    session: Session, trace_id: str, experiment: Experiment, *, parent_key: str
) -> list[dict[str, Any]]:
    review_sets = session.scalars(
        select(ReviewSet)
        .options(
            selectinload(ReviewSet.items),
            selectinload(ReviewSet.assignments).selectinload(ReviewAssignment.reviewer),
        )
        .where(ReviewSet.experiment_id == experiment.id)
        .order_by(ReviewSet.id)
    ).all()
    spans: list[dict[str, Any]] = []
    for review_set in review_sets:
        review_set_key = f"review_set:{review_set.id}"
        spans.append(
            _span(
                trace_id=trace_id,
                key=review_set_key,
                name="model_eval.human_review_set",
                parent_key=parent_key,
                start_time=review_set.created_at,
                end_time=None,
                attributes={
                    "model_eval.review_set.id": review_set.id,
                    "model_eval.review_set.slug": review_set.slug,
                    "model_eval.review_set.type": review_set.review_type,
                    "model_eval.review_item_count": len(review_set.items),
                    "model_eval.review_assignment_count": len(review_set.assignments),
                },
            )
        )
        for item in sorted(review_set.items, key=lambda value: value.id):
            item_key = f"review_item:{item.id}"
            spans.append(
                _span(
                    trace_id=trace_id,
                    key=item_key,
                    name="model_eval.human_review_item",
                    parent_key=review_set_key,
                    start_time=item.created_at,
                    end_time=None,
                    attributes={
                        "model_eval.review_item.id": item.id,
                        "model_eval.review_set.id": item.review_set_id,
                        "model_eval.run_attempt.db_id": item.run_attempt_id,
                    },
                )
            )
        for assignment in sorted(review_set.assignments, key=lambda value: value.id):
            spans.append(
                _span(
                    trace_id=trace_id,
                    key=f"review_assignment:{assignment.id}",
                    name="model_eval.human_review_assignment",
                    parent_key=f"review_item:{assignment.review_item_id}",
                    start_time=assignment.assigned_at,
                    end_time=assignment.submitted_at,
                    attributes={
                        "model_eval.review_assignment.id": assignment.id,
                        "model_eval.review_set.id": assignment.review_set_id,
                        "model_eval.review_item.id": assignment.review_item_id,
                        "model_eval.reviewer.id": assignment.reviewer_id,
                        "model_eval.review_assignment.status": assignment.status,
                        "model_eval.failure_taxonomy.version": (
                            assignment.taxonomy_snapshot or {}
                        ).get("version"),
                    },
                )
            )
    return spans


def _artifact_preprocessing_spans(
    session: Session, trace_id: str, experiment: Experiment, *, parent_key: str
) -> list[dict[str, Any]]:
    artifact_ids = {
        str(snapshot.get("id"))
        for snapshot in (experiment.artifact_snapshots or {}).values()
        if snapshot.get("id") is not None
    }
    if not artifact_ids:
        return []
    runs = session.scalars(
        select(ArtifactPreprocessingRun)
        .where(ArtifactPreprocessingRun.project_id == experiment.project_id)
        .order_by(ArtifactPreprocessingRun.id)
    ).all()
    spans: list[dict[str, Any]] = []
    for run in runs:
        source = dict(run.source_artifact_snapshot or {})
        derived = [dict(item) for item in (run.derived_artifact_snapshots or [])]
        linked_ids = {str(source.get("id"))}
        linked_ids.update(str(item.get("id")) for item in derived if item.get("id") is not None)
        if not artifact_ids.intersection(linked_ids):
            continue
        spans.append(
            _span(
                trace_id=trace_id,
                key=f"artifact_preprocessing:{run.id}",
                name="model_eval.artifact_preprocessing_run",
                parent_key=parent_key,
                start_time=run.created_at,
                end_time=run.completed_at,
                attributes={
                    "model_eval.artifact_preprocessing.id": run.id,
                    "model_eval.project.id": run.project_id,
                    "model_eval.source_artifact.id": run.source_artifact_id,
                    "model_eval.parser.name": run.parser_name,
                    "model_eval.parser.version": run.parser_version,
                    "model_eval.artifact_preprocessing.status": run.status,
                    "model_eval.derived_artifact_count": len(run.derived_artifact_ids or []),
                    "model_eval.error_kind": run.error_kind,
                },
            )
        )
    return spans


def _export_event_spans(
    session: Session, trace_id: str, experiment: Experiment, *, parent_key: str
) -> list[dict[str, Any]]:
    audit_logs = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.experiment_id == experiment.id,
            AuditLog.event_kind.in_(["export_generated", "blind_review_queue_exported"]),
        )
        .order_by(AuditLog.id)
    ).all()
    return [
        _span(
            trace_id=trace_id,
            key=f"audit_log:{audit_log.id}",
            name="model_eval.export_event",
            parent_key=parent_key,
            start_time=audit_log.created_at,
            end_time=None,
            attributes={
                "model_eval.audit_log.id": audit_log.id,
                "model_eval.audit.event_kind": audit_log.event_kind,
                "model_eval.audit.entity_type": audit_log.entity_type,
                "model_eval.audit.entity_id": audit_log.entity_id,
                "model_eval.export.format": (audit_log.details or {}).get("format"),
            },
        )
        for audit_log in audit_logs
    ]


def _span(
    *,
    trace_id: str,
    key: str,
    name: str,
    parent_key: str | None,
    start_time: Any,
    end_time: Any,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "span_id": _span_id(key),
        "parent_span_id": _span_id(parent_key) if parent_key else None,
        "name": name,
        "kind": "INTERNAL",
        "start_time": _isoformat(start_time),
        "end_time": _isoformat(end_time),
        "attributes": _attributes(attributes),
    }


def _attributes(values: dict[str, Any]) -> dict[str, str | int | float | bool]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, (str, int, float, bool))
    }


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
    if matches:
        return matches[0]
    raise ValueError(f"Experiment '{ref}' was not found.")


def _trace_id(experiment: Experiment) -> str:
    return _hex_digest(f"trace:experiment:{experiment.id}:{experiment.slug}", length=32)


def _span_id(key: str) -> str:
    return _hex_digest(f"span:{key}", length=16)


def _hex_digest(value: str, *, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _isoformat(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat()
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _int_ref(ref: int | str) -> int | None:
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str) and ref.isdigit():
        return int(ref)
    return None
