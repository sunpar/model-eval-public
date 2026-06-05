from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from model_eval_api.artifact_types import ArtifactInputMode
from model_eval_api.execution_states import AttemptStatus
from model_eval_api.metric_adapters import (
    MetricAdapter,
    get_metric_adapter,
    validate_metric_adapter_inputs,
)
from model_eval_api.persistence import repositories
from model_eval_api.persistence.models import (
    Artifact,
    Experiment,
    MetricAdapterConfig,
    Run,
    RunAttempt,
    Score,
)
from model_eval_api.response_payloads import attempt_output_text


def run_metric_adapters_for_experiment(
    session: Session,
    *,
    experiment_id: int,
    adapter_config_slug: str | None = None,
    adapter_config_version: int | None = None,
    dry_run: bool = False,
    local_only: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment {experiment_id} does not exist.")

    configs = _metric_adapter_configs(
        session,
        experiment=experiment,
        adapter_config_slug=adapter_config_slug,
        adapter_config_version=adapter_config_version,
    )
    attempts = _scorable_attempts(session, experiment_id)
    skipped: list[dict[str, Any]] = []
    scores_recorded = 0
    planned_scores = 0

    for attempt in attempts:
        inputs = _metric_inputs_for_attempt(session, attempt)
        for config in configs:
            skip = _config_policy_skip(config, attempt=attempt, local_only=local_only)
            if skip is not None:
                skipped.append(skip)
                continue

            adapter = get_metric_adapter(config.adapter_kind)
            validation = validate_metric_adapter_inputs(
                _required_inputs_for_config(config, adapter),
                inputs,
            )
            if not validation["valid"]:
                skipped.append(
                    _skip(
                        config,
                        attempt,
                        "missing_required_inputs",
                        missing_inputs=validation["missing"],
                    )
                )
                continue

            snapshot_checksum = _snapshot_checksum(config.snapshot)
            if not force and _metric_adapter_score_exists(
                session,
                attempt=attempt,
                config=config,
                snapshot_checksum=snapshot_checksum,
            ):
                skipped.append(_skip(config, attempt, "duplicate_adapter_score"))
                continue

            if dry_run:
                planned_scores += 1
                continue

            result = adapter.evaluate(inputs)
            value = {
                **result.value,
                "adapter_config": {"id": config.slug, "version": config.version},
                "adapter_config_snapshot": dict(config.snapshot or {}),
                "adapter_config_snapshot_checksum": snapshot_checksum,
                "input_validation": validation,
            }
            repositories.record_score(
                session,
                run_attempt=attempt,
                type=result.type,
                evaluator_type="metric_adapter",
                criterion=result.criterion,
                value=value,
                explanation=result.explanation,
                confidence=result.confidence,
                evaluator_version=config.version,
            )
            scores_recorded += 1

    return {
        "experiment_id": experiment.id,
        "adapter_config_slug": adapter_config_slug,
        "adapter_config_version": adapter_config_version,
        "dry_run": dry_run,
        "local_only": local_only,
        "force": force,
        "adapter_configs_considered": len(configs),
        "attempts_evaluated": len(attempts),
        "planned_scores": planned_scores,
        "scores_recorded": scores_recorded,
        "skipped": skipped,
    }


def _metric_adapter_configs(
    session: Session,
    *,
    experiment: Experiment,
    adapter_config_slug: str | None,
    adapter_config_version: int | None,
) -> list[MetricAdapterConfig]:
    configs = [
        config
        for config in repositories.list_metric_adapter_configs(session, project=experiment.project)
        if not config.archived
    ]
    if adapter_config_slug is not None:
        configs = [config for config in configs if config.slug == adapter_config_slug]
        if adapter_config_version is not None:
            configs = [config for config in configs if config.version == adapter_config_version]
        elif configs:
            latest = max(config.version for config in configs)
            configs = [config for config in configs if config.version == latest]
    elif adapter_config_version is not None:
        raise ValueError("adapter_config_version requires adapter_config_slug.")
    if not configs:
        label = adapter_config_slug or "metric adapter configs"
        raise ValueError(f"No metric adapter config found for {label}.")
    return configs


def _scorable_attempts(session: Session, experiment_id: int) -> list[RunAttempt]:
    return session.scalars(
        select(RunAttempt)
        .join(Run)
        .where(
            Run.experiment_id == experiment_id,
            RunAttempt.status == AttemptStatus.SUCCEEDED.value,
        )
        .options(selectinload(RunAttempt.run))
        .order_by(RunAttempt.id)
    ).all()


def _metric_inputs_for_attempt(session: Session, attempt: RunAttempt) -> dict[str, Any]:
    response_payload = dict(attempt.response_payload or {})
    retrieved_chunks = _payload_list(response_payload.get("retrieved_chunks"))
    citations = _payload_list(response_payload.get("citations"))
    derived_artifacts = _payload_list(response_payload.get("derived_artifacts"))
    reference_answers = response_payload.get("reference_answers")

    for artifact in _derived_artifacts_for_attempt(session, attempt):
        metadata = dict(artifact.metadata_json or {})
        artifact_payload = {
            "id": artifact.slug,
            "artifact_id": artifact.id,
            "input_mode": artifact.input_mode,
            "metadata": metadata,
        }
        derived_artifacts.append(artifact_payload)
        if artifact.input_mode == ArtifactInputMode.RETRIEVAL_CHUNKS.value:
            retrieved_chunks.append(metadata)
        elif artifact.input_mode == ArtifactInputMode.PAPER_CARDS.value:
            citation = metadata.get("citation")
            if isinstance(citation, dict):
                citations.append({"id": artifact.slug, **citation})
            summary = metadata.get("summary")
            if isinstance(summary, str) and summary.strip():
                if isinstance(reference_answers, list):
                    reference_answers = [*reference_answers, summary]
                elif isinstance(reference_answers, str) and reference_answers.strip():
                    reference_answers = [reference_answers, summary]
                else:
                    reference_answers = [summary]

    return {
        "answer_text": attempt_output_text(attempt),
        "retrieved_chunks": retrieved_chunks,
        "citations": citations,
        "reference_answers": reference_answers,
        "derived_artifacts": derived_artifacts,
    }


def _derived_artifacts_for_attempt(session: Session, attempt: RunAttempt) -> list[Artifact]:
    artifact_ids = _derived_artifact_ids(dict(attempt.run.model_input_snapshot or {}))
    if not artifact_ids:
        return []
    return session.scalars(
        select(Artifact).where(Artifact.id.in_(artifact_ids)).order_by(Artifact.id)
    ).all()


def _derived_artifact_ids(model_input_snapshot: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    bundle = model_input_snapshot.get("derived_bundle")
    if isinstance(bundle, dict):
        ids.extend(_int_id(value) for value in bundle.get("derived_artifact_ids") or [])
    for item in model_input_snapshot.get("artifact_inputs") or []:
        if isinstance(item, dict):
            ids.append(_int_id(item.get("derived_artifact_id")))
    return sorted({value for value in ids if value is not None})


def _config_policy_skip(
    config: MetricAdapterConfig, *, attempt: RunAttempt, local_only: bool
) -> dict[str, Any] | None:
    if (attempt.response_payload or {}).get("dry_run") is True:
        return _skip(config, attempt, "provider_dry_run_attempt")
    if local_only and not config.local_only:
        return _skip(config, attempt, "non_local_adapter_blocked")
    if not config.local_only:
        return _skip(config, attempt, "external_adapter_not_implemented")
    return None


def _metric_adapter_score_exists(
    session: Session,
    *,
    attempt: RunAttempt,
    config: MetricAdapterConfig,
    snapshot_checksum: str,
) -> bool:
    return (
        session.scalar(
            select(Score.id)
            .where(
                Score.run_attempt_id == attempt.id,
                Score.evaluator_type == "metric_adapter",
                Score.criterion == config.adapter_kind,
                Score.evaluator_version == config.version,
            )
            .where(Score.value["adapter_config_snapshot_checksum"].as_string() == snapshot_checksum)
            .limit(1)
        )
        is not None
    )


def _snapshot_checksum(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_inputs_for_config(config: MetricAdapterConfig, adapter: MetricAdapter) -> list[str]:
    required_inputs: list[str] = []
    for field in [*adapter.required_inputs, *(config.required_inputs or [])]:
        normalized = str(field).strip()
        if normalized and normalized not in required_inputs:
            required_inputs.append(normalized)
    return required_inputs


def _payload_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _int_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _skip(
    config: MetricAdapterConfig,
    attempt: RunAttempt,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "adapter_config_id": config.slug,
        "adapter_config_version": config.version,
        "attempt_id": attempt.attempt_id,
        "reason": reason,
        **extra,
    }
