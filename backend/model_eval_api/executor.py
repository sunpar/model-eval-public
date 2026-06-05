from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from model_eval_api.execution_states import AttemptStatus, ExperimentStatus, RunStatus
from model_eval_api.deterministic_evaluators import record_deterministic_scores_for_attempt
from model_eval_api.persistence.models import (
    Experiment,
    ProviderCallCache,
    Run,
    RunAttempt,
    utc_now,
)
from model_eval_api.persistence.repositories import record_audit_event, record_run_attempt
from model_eval_api.providers import (
    AnthropicAdapter,
    ErrorKind,
    OpenAIAdapter,
    ProviderAdapter,
    ProviderBlockedError,
    ProviderExecutionConfig,
    ProviderRequest,
    ProviderResponse,
    classify_provider_error,
)
from model_eval_api.providers.settings import enforce_provider_config


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    backoff_seconds: int = 1


@dataclass(frozen=True)
class ExecutionControls:
    max_parallel_requests: int = 1
    max_total_cost_usd: float | None = None
    retry_failed: bool = False
    cache_provider_calls: bool = False
    local_only: bool | None = None
    context_budget_tokens: int | None = None
    truncation_policy: str = "fail_on_over_budget"
    data_egress_label: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any] | None) -> ExecutionControls:
        value = snapshot or {}
        return cls(
            max_parallel_requests=max(int(value.get("max_parallel_requests") or 1), 1),
            max_total_cost_usd=_float_or_none(value.get("max_total_cost_usd")),
            retry_failed=bool(value.get("retry_failed") or False),
            cache_provider_calls=bool(value.get("cache_provider_calls") or False),
            local_only=value.get("local_only") if isinstance(value.get("local_only"), bool) else None,
            context_budget_tokens=_int_or_none(
                value.get("context_budget_tokens") or value.get("max_context_tokens")
            ),
            truncation_policy=str(value.get("truncation_policy") or "fail_on_over_budget"),
            data_egress_label=(
                str(value.get("data_egress_label"))
                if value.get("data_egress_label") is not None
                else None
            ),
        )


def default_provider_adapters() -> dict[str, ProviderAdapter]:
    return {
        "openai": OpenAIAdapter(),
        "anthropic": AnthropicAdapter(),
    }


def execute_experiment(
    session: Session,
    experiment_id: int,
    *,
    adapters: dict[str, ProviderAdapter] | None = None,
    provider_config: ProviderExecutionConfig | None = None,
    retry_policy: RetryPolicy | None = None,
    dry_run: bool = True,
) -> Experiment:
    experiment = _require_experiment(session, experiment_id)
    if experiment.status == ExperimentStatus.CANCELED.value:
        _cancel_experiment_attempts(experiment)
        return experiment

    controls = ExecutionControls.from_snapshot(experiment.controls_snapshot)
    experiment.status = ExperimentStatus.RUNNING.value
    record_audit_event(
        session,
        experiment=experiment,
        event_kind="experiment_execution_started",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={"dry_run": dry_run},
    )
    session.flush()
    runs = session.scalars(
        select(Run).where(Run.experiment_id == experiment.id).order_by(Run.id)
    ).all()
    for batch in _chunks(runs, controls.max_parallel_requests):
        for run in batch:
            execute_run(
                session,
                run.id,
                adapters=adapters,
                provider_config=provider_config,
                retry_policy=retry_policy,
                dry_run=dry_run,
            )

    statuses = {
        run.status for run in session.scalars(select(Run).where(Run.experiment_id == experiment.id))
    }
    if experiment.status == ExperimentStatus.CANCELED.value or statuses == {RunStatus.CANCELED.value}:
        experiment.status = ExperimentStatus.CANCELED.value
    elif RunStatus.FAILED.value in statuses:
        experiment.status = ExperimentStatus.FAILED.value
    elif RunStatus.RUNNING.value in statuses:
        experiment.status = ExperimentStatus.RUNNING.value
    elif RunStatus.PENDING.value in statuses:
        experiment.status = ExperimentStatus.QUEUED.value
    else:
        experiment.status = ExperimentStatus.COMPLETE.value
    record_audit_event(
        session,
        experiment=experiment,
        event_kind="experiment_execution_completed",
        entity_type="experiment",
        entity_id=str(experiment.id),
        details={"status": experiment.status},
    )
    return experiment


def execute_run(
    session: Session,
    run_id: int,
    *,
    adapters: dict[str, ProviderAdapter] | None = None,
    provider_config: ProviderExecutionConfig | None = None,
    retry_policy: RetryPolicy | None = None,
    dry_run: bool = True,
) -> Run:
    run = _require_run(session, run_id)
    controls = ExecutionControls.from_snapshot(run.experiment.controls_snapshot)
    if run.experiment.status == ExperimentStatus.CANCELED.value or run.status == RunStatus.CANCELED.value:
        _cancel_run_attempts(run)
        return run

    try:
        adapter = _adapter_for_run(
            run,
            default_provider_adapters() if adapters is None else adapters,
        )
    except ProviderBlockedError as error:
        attempt = _next_queued_attempt(run)
        if attempt is not None:
            _stamp_attempt_provider_metadata(attempt, run)
            _apply_failure(attempt, error, elapsed_ms=time.perf_counter())
            attempt.terminal_failure_reason = "provider_blocked"
            _mark_remaining_queued_attempts_provider_blocked(run)
            record_audit_event(
                session,
                run_attempt=attempt,
                event_kind="provider_call_blocked",
                entity_type="run_attempt",
                entity_id=attempt.attempt_id,
                details={
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "reason": "provider_not_configured",
                },
            )
        run.status = RunStatus.FAILED.value
        return run
    retry_policy = retry_policy or RetryPolicy()
    execution_config = _provider_config(controls, run.experiment.project, provider_config)
    run.status = RunStatus.RUNNING.value
    session.flush()

    while True:
        _refresh_run_cancel_state(session, run)
        attempt = _next_queued_attempt(run)
        if attempt is None:
            break
        if run.experiment.status == ExperimentStatus.CANCELED.value or run.status == RunStatus.CANCELED.value:
            _cancel_run_attempts(run)
            break
        _prepare_run_safety_report(
            run,
            controls,
            execution_config=execution_config,
            dry_run=dry_run,
        )
        _stamp_attempt_provider_metadata(attempt, run)
        if run.truncation_policy != "fail_on_over_budget":
            _mark_blocked_by_context_policy(attempt, "unsupported_truncation_policy")
            _mark_remaining_queued_attempts_blocked(run, "unsupported_truncation_policy")
            record_audit_event(
                session,
                run_attempt=attempt,
                event_kind="provider_call_blocked",
                entity_type="run_attempt",
                entity_id=attempt.attempt_id,
                details={
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "reason": "unsupported_truncation_policy",
                    "truncation_policy": run.truncation_policy,
                },
            )
            run.status = RunStatus.FAILED.value
            break
        if (run.context_report or {}).get("over_budget") is True:
            _mark_blocked_by_context_policy(attempt, "context_budget_exceeded")
            _mark_remaining_queued_attempts_blocked(run, "context_budget_exceeded")
            record_audit_event(
                session,
                run_attempt=attempt,
                event_kind="provider_call_blocked",
                entity_type="run_attempt",
                entity_id=attempt.attempt_id,
                details={
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "reason": "context_budget_exceeded",
                    "estimated_tokens": run.context_report.get("estimated_tokens"),
                    "budget_tokens": run.context_report.get("budget_tokens"),
                },
            )
            run.status = RunStatus.FAILED.value
            break

        request = adapter.build_request(run.run_snapshot)
        cache_key = provider_cache_key(request, run.model_input_snapshot)
        cached = _cached_response(session, run.experiment, cache_key) if controls.cache_provider_calls else None
        if cached is not None:
            _apply_cached_response(attempt, cached, cache_key)
            record_audit_event(
                session,
                run_attempt=attempt,
                event_kind="provider_call_cache_hit",
                entity_type="run_attempt",
                entity_id=attempt.attempt_id,
                details={
                    "provider": cached.provider,
                    "model": cached.model,
                    "cache_key": cache_key,
                },
            )
            record_deterministic_scores_for_attempt(session, attempt)
            continue
        if _cost_cap_reached(session, run.experiment, controls):
            _mark_blocked_by_cost_cap(attempt)
            record_audit_event(
                session,
                run_attempt=attempt,
                event_kind="provider_call_blocked",
                entity_type="run_attempt",
                entity_id=attempt.attempt_id,
                details={
                    "provider": attempt.provider,
                    "model": attempt.model,
                    "reason": "cost_cap_exceeded",
                    "max_total_cost_usd": controls.max_total_cost_usd,
                },
            )
            run.status = RunStatus.SKIPPED.value
            break
        try:
            enforce_provider_config(request, execution_config, dry_run=dry_run)
        except ProviderBlockedError as error:
            _apply_failure(attempt, error, elapsed_ms=time.perf_counter())
            attempt.terminal_failure_reason = "provider_blocked"
            _mark_remaining_queued_attempts_provider_blocked(run)
            record_audit_event(
                session,
                run_attempt=attempt,
                event_kind="provider_call_blocked",
                entity_type="run_attempt",
                entity_id=attempt.attempt_id,
                details={
                    "provider": request.provider,
                    "model": request.model,
                    "reason": "provider_policy",
                    "dry_run": dry_run,
                    "local_only": execution_config.local_only,
                },
            )
            run.status = RunStatus.FAILED.value
            break

        record_audit_event(
            session,
            run_attempt=attempt,
            event_kind="provider_call_started",
            entity_type="run_attempt",
            entity_id=attempt.attempt_id,
            details={
                "provider": request.provider,
                "model": request.model,
                "dry_run": dry_run,
                "local_only": execution_config.local_only,
                "data_egress_label": run.data_egress_label,
            },
        )
        _run_provider_attempt(
            session,
            run,
            attempt,
            request,
            adapter,
            execution_config,
            dry_run=dry_run,
            cache_key=cache_key,
            cache_enabled=controls.cache_provider_calls,
        )
        if attempt.status == AttemptStatus.SUCCEEDED.value:
            record_deterministic_scores_for_attempt(session, attempt)
            continue
        if attempt.status == AttemptStatus.FAILED.value and _should_retry(
            attempt, controls, retry_policy
        ):
            _create_retry_attempt(session, run, attempt, retry_policy)
            session.flush()
            continue
        if attempt.status == AttemptStatus.FAILED.value and not attempt.terminal_failure_reason:
            attempt.terminal_failure_reason = attempt.error_message
        break

    if run.status not in {RunStatus.SKIPPED.value, RunStatus.CANCELED.value}:
        run.status = _run_status_from_attempts(run)
    return run


def create_retry_attempt_for_run(session: Session, run_id: int) -> RunAttempt:
    run = _require_run(session, run_id)
    queued_retry = _queued_retry_attempt(run)
    if queued_retry is not None:
        return queued_retry
    latest = _latest_attempt(run)
    if latest is None:
        raise ValueError("Run has no attempts to retry.")
    if latest.status != AttemptStatus.FAILED.value:
        raise ValueError("Run latest attempt is not failed.")
    retry_attempt = _create_retry_attempt(session, run, latest, RetryPolicy())
    run.status = RunStatus.PENDING.value
    if run.experiment.status != ExperimentStatus.CANCELED.value:
        run.experiment.status = ExperimentStatus.QUEUED.value
    session.flush()
    record_audit_event(
        session,
        run_attempt=retry_attempt,
        event_kind="retry_requested",
        entity_type="run_attempt",
        entity_id=retry_attempt.attempt_id,
        details={
            "parent_attempt_id": latest.attempt_id,
            "attempt_number": retry_attempt.attempt_number,
        },
    )
    session.flush()
    return retry_attempt


def cancel_experiment(session: Session, experiment_id: int) -> Experiment:
    experiment = _require_experiment(session, experiment_id)
    experiment.status = ExperimentStatus.CANCELED.value
    _cancel_experiment_attempts(experiment)
    record_audit_event(
        session,
        experiment=experiment,
        event_kind="experiment_canceled",
        entity_type="experiment",
        entity_id=str(experiment.id),
    )
    session.flush()
    return experiment


def cancel_run(session: Session, run_id: int) -> Run:
    run = _require_run(session, run_id)
    _cancel_run_attempts(run)
    record_audit_event(
        session,
        run=run,
        event_kind="run_canceled",
        entity_type="run",
        entity_id=str(run.id),
    )
    session.flush()
    return run


def provider_cache_key(request: ProviderRequest, model_input_snapshot: dict[str, Any]) -> str:
    payload = {
        "provider": request.provider,
        "model": request.model,
        "payload": request.payload,
        "raw_provider_params": request.raw_provider_params,
        "normalized_config": request.normalized_config,
        "model_input_snapshot": model_input_snapshot,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _run_provider_attempt(
    session: Session,
    run: Run,
    attempt: RunAttempt,
    request: ProviderRequest,
    adapter: ProviderAdapter,
    provider_config: ProviderExecutionConfig,
    *,
    dry_run: bool,
    cache_key: str,
    cache_enabled: bool,
) -> None:
    attempt.status = AttemptStatus.RUNNING.value
    attempt.started_at = utc_now()
    attempt.provider = request.provider
    attempt.model = request.model
    attempt.request_payload = request.payload
    attempt.pricing_snapshot = _pricing_snapshot_for_request(run.experiment, request)
    attempt.cache_key = cache_key
    start = time.perf_counter()
    try:
        response = adapter.execute(request, config=provider_config, dry_run=dry_run)
    except BaseException as error:
        _apply_failure(attempt, error, elapsed_ms=start)
        record_audit_event(
            session,
            run_attempt=attempt,
            event_kind="provider_call_failed",
            entity_type="run_attempt",
            entity_id=attempt.attempt_id,
            details={
                "provider": request.provider,
                "model": request.model,
                "error_kind": attempt.error_kind,
                "dry_run": dry_run,
            },
        )
        return
    _apply_response(attempt, response, elapsed_ms=start)
    record_audit_event(
        session,
        run_attempt=attempt,
        event_kind="provider_call_succeeded",
        entity_type="run_attempt",
        entity_id=attempt.attempt_id,
        details={
            "provider": request.provider,
            "model": request.model,
            "provider_response_id": response.provider_response_id,
            "dry_run": dry_run,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
            "cost_usd": response.cost_usd,
        },
    )
    if cache_enabled:
        _store_cached_response(
            session,
            run.experiment,
            cache_key,
            request,
            response,
            provider_timestamp=attempt.provider_timestamp,
        )


def _apply_response(attempt: RunAttempt, response: ProviderResponse, *, elapsed_ms: float) -> None:
    attempt.status = AttemptStatus.SUCCEEDED.value
    attempt.response_payload = response.response_payload
    attempt.provider_response_id = response.provider_response_id
    attempt.completed_at = utc_now()
    attempt.provider_timestamp = _provider_timestamp_from_response(response) or attempt.completed_at
    attempt.provider_metadata = dict(response.provider_metadata or {})
    attempt.system_fingerprint = _system_fingerprint(response.provider_metadata)
    attempt.latency_ms = _elapsed_ms(elapsed_ms)
    attempt.input_tokens = response.usage.input_tokens
    attempt.output_tokens = response.usage.output_tokens
    attempt.total_tokens = response.usage.total_tokens
    attempt.cost_usd = response.cost_usd


def _apply_failure(attempt: RunAttempt, error: BaseException, *, elapsed_ms: float) -> None:
    kind = classify_provider_error(error)
    attempt.status = AttemptStatus.FAILED.value
    attempt.error_kind = kind.value
    attempt.error_message = str(error)
    attempt.completed_at = utc_now()
    attempt.provider_timestamp = attempt.completed_at
    attempt.latency_ms = _elapsed_ms(elapsed_ms)
    if kind is not ErrorKind.RETRYABLE:
        attempt.terminal_failure_reason = str(error)


def _apply_cached_response(
    attempt: RunAttempt, cached: ProviderCallCache, cache_key: str
) -> None:
    attempt.status = AttemptStatus.SUCCEEDED.value
    attempt.started_at = utc_now()
    attempt.completed_at = attempt.started_at
    attempt.latency_ms = 0
    attempt.provider = cached.provider
    attempt.model = cached.model
    attempt.request_payload = cached.request_payload
    attempt.response_payload = cached.response_payload
    attempt.provider_response_id = cached.provider_response_id
    attempt.provider_timestamp = cached.provider_timestamp or cached.created_at
    attempt.provider_metadata = dict(cached.provider_metadata or {})
    attempt.system_fingerprint = cached.system_fingerprint
    attempt.pricing_snapshot = _pricing_snapshot_for_cached_response(attempt.run.experiment, cached)
    attempt.input_tokens = cached.input_tokens
    attempt.output_tokens = cached.output_tokens
    attempt.total_tokens = cached.total_tokens
    attempt.cost_usd = 0.0
    attempt.cache_key = cache_key
    attempt.cache_hit = True


def _create_retry_attempt(
    session: Session, run: Run, failed_attempt: RunAttempt, retry_policy: RetryPolicy
) -> RunAttempt:
    attempt_number = (failed_attempt.attempt_number or 1) + 1
    delay_seconds = retry_policy.backoff_seconds * (2 ** max(attempt_number - 2, 0))
    failed_attempt.retry_after_seconds = delay_seconds
    available_at = utc_now() + timedelta(seconds=delay_seconds)
    return record_run_attempt(
        session,
        run=run,
        attempt_id=_retry_attempt_id(run, failed_attempt, attempt_number),
        replicate_index=failed_attempt.replicate_index,
        replicate_group_id=failed_attempt.replicate_group_id,
        attempt_kind="retry",
        status=AttemptStatus.QUEUED.value,
        attempt_number=attempt_number,
        parent_attempt_id=failed_attempt.attempt_id,
        available_at=available_at,
    )


def _store_cached_response(
    session: Session,
    experiment: Experiment,
    cache_key: str,
    request: ProviderRequest,
    response: ProviderResponse,
    *,
    provider_timestamp: datetime | None,
) -> None:
    if _cached_response(session, experiment, cache_key) is not None:
        return
    cache = ProviderCallCache(
        project_id=experiment.project_id,
        cache_key=cache_key,
        provider=request.provider,
        model=request.model,
        request_payload=request.payload,
        response_payload=response.response_payload,
        provider_response_id=response.provider_response_id,
        provider_timestamp=provider_timestamp,
        provider_metadata=response.provider_metadata,
        system_fingerprint=_system_fingerprint(response.provider_metadata),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        total_tokens=response.usage.total_tokens,
        cost_usd=response.cost_usd,
    )
    try:
        with session.begin_nested():
            session.add(cache)
            session.flush()
    except IntegrityError:
        pass


def _cached_response(
    session: Session, experiment: Experiment, cache_key: str
) -> ProviderCallCache | None:
    return session.scalar(
        select(ProviderCallCache).where(
            ProviderCallCache.project_id == experiment.project_id,
            ProviderCallCache.cache_key == cache_key,
        )
    )


def _refresh_run_cancel_state(session: Session, run: Run) -> None:
    session.flush()
    session.refresh(run, attribute_names=["status"])
    session.refresh(run.experiment, attribute_names=["status"])


def _adapter_for_run(run: Run, adapters: dict[str, ProviderAdapter]) -> ProviderAdapter:
    provider = run.run_snapshot["model_config"]["provider"]
    if provider not in adapters:
        raise ProviderBlockedError(f"Provider '{provider}' is not configured.")
    return adapters[provider]


def _provider_config(
    controls: ExecutionControls,
    project: Any,
    provider_config: ProviderExecutionConfig | None,
) -> ProviderExecutionConfig:
    base = provider_config or ProviderExecutionConfig.from_env()
    project_allowed = _policy_tuple(getattr(project, "provider_allow_list", None))
    project_denied = _policy_tuple(getattr(project, "provider_deny_list", None))
    allowed = _intersect_allowed(base.allowed_providers, project_allowed)
    denied = tuple(sorted({*(_policy_tuple(base.denied_providers)), *project_denied}))
    if controls.local_only is None:
        local_only = base.local_only
    else:
        local_only = controls.local_only
    return ProviderExecutionConfig(
        local_only=local_only,
        allowed_providers=allowed,
        denied_providers=denied,
        client=base.client,
    )


def _prepare_run_safety_report(
    run: Run,
    controls: ExecutionControls,
    *,
    execution_config: ProviderExecutionConfig,
    dry_run: bool,
) -> None:
    provider = run.run_snapshot["model_config"]["provider"]
    messages = list((run.model_input_snapshot or {}).get("final_messages") or [])
    budget_tokens = controls.context_budget_tokens
    estimated_tokens = sum(_estimate_message_tokens(message) for message in messages)
    over_budget = budget_tokens is not None and estimated_tokens > budget_tokens
    if controls.truncation_policy != "fail_on_over_budget" or over_budget:
        included_messages: list[dict[str, Any]] = []
        dropped_messages = _message_reports(messages)
    else:
        included_messages = _message_reports(messages)
        dropped_messages = []
    run.truncation_policy = controls.truncation_policy
    run.data_egress_label = _data_egress_label(
        controls,
        provider=provider,
        execution_config=execution_config,
        dry_run=dry_run,
    )
    run.context_report = {
        "estimator": "word_count_v1",
        "estimated_tokens": estimated_tokens,
        "budget_tokens": budget_tokens,
        "over_budget": over_budget,
        "truncation_policy": run.truncation_policy,
        "included_messages": included_messages,
        "dropped_messages": dropped_messages,
    }


def _stamp_attempt_provider_metadata(attempt: RunAttempt, run: Run) -> None:
    model_config = run.run_snapshot.get("model_config") or {}
    attempt.provider = model_config.get("provider")
    attempt.model = model_config.get("model")
    request = ProviderRequest(
        provider=str(attempt.provider or ""),
        model=str(attempt.model or ""),
        payload={},
    )
    attempt.pricing_snapshot = _pricing_snapshot_for_request(run.experiment, request)


def _mark_blocked_by_context_policy(attempt: RunAttempt, reason: str) -> None:
    attempt.status = AttemptStatus.FAILED.value
    attempt.error_kind = ErrorKind.BLOCKED_BY_CONFIG.value
    attempt.error_message = reason
    attempt.terminal_failure_reason = reason
    attempt.completed_at = utc_now()
    attempt.provider_timestamp = attempt.completed_at


def _mark_remaining_queued_attempts_blocked(run: Run, reason: str) -> None:
    for attempt in _iter_queued_attempts(run):
        _stamp_attempt_provider_metadata(attempt, run)
        _mark_blocked_by_context_policy(attempt, reason)


def _mark_remaining_queued_attempts_provider_blocked(run: Run) -> None:
    for attempt in _iter_queued_attempts(run):
        _stamp_attempt_provider_metadata(attempt, run)
        attempt.status = AttemptStatus.FAILED.value
        attempt.error_kind = ErrorKind.BLOCKED_BY_CONFIG.value
        attempt.error_message = "Provider blocked by execution policy."
        attempt.terminal_failure_reason = "provider_blocked"
        attempt.completed_at = utc_now()
        attempt.provider_timestamp = attempt.completed_at


def _iter_queued_attempts(run: Run) -> list[RunAttempt]:
    queued_states = {AttemptStatus.QUEUED.value, "pending"}
    return [attempt for attempt in run.attempts if attempt.status in queued_states]


def _cost_cap_reached(
    session: Session, experiment: Experiment, controls: ExecutionControls
) -> bool:
    if controls.max_total_cost_usd is None:
        return False
    current_cost = sum(
        attempt.cost_usd or 0.0
        for attempt in session.scalars(
            select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
        )
        if not attempt.cache_hit
    )
    return current_cost >= controls.max_total_cost_usd


def _mark_blocked_by_cost_cap(attempt: RunAttempt) -> None:
    attempt.status = AttemptStatus.FAILED.value
    attempt.error_kind = ErrorKind.BLOCKED_BY_CONFIG.value
    attempt.error_message = "Cost cap exceeded before provider call."
    attempt.terminal_failure_reason = "cost_cap_exceeded"
    attempt.completed_at = utc_now()
    attempt.provider_timestamp = attempt.completed_at


def _should_retry(
    attempt: RunAttempt, controls: ExecutionControls, retry_policy: RetryPolicy
) -> bool:
    return (
        controls.retry_failed
        and attempt.error_kind == ErrorKind.RETRYABLE.value
        and (attempt.attempt_number or 1) < retry_policy.max_attempts
    )


def _next_queued_attempt(run: Run) -> RunAttempt | None:
    queued_states = {AttemptStatus.QUEUED.value, "pending"}
    now = utc_now()
    return next(
        (
            attempt
            for attempt in sorted(run.attempts, key=lambda candidate: candidate.id or 0)
            if attempt.status in queued_states
            and (attempt.available_at is None or attempt.available_at <= now)
        ),
        None,
    )


def _latest_attempt(run: Run) -> RunAttempt | None:
    return max(run.attempts, key=lambda attempt: attempt.id or 0, default=None)


def _queued_retry_attempt(run: Run) -> RunAttempt | None:
    retry_states = {AttemptStatus.QUEUED.value, "pending", AttemptStatus.RUNNING.value}
    return next(
        (
            attempt
            for attempt in sorted(run.attempts, key=lambda candidate: candidate.id or 0)
            if attempt.attempt_kind == "retry" and attempt.status in retry_states
        ),
        None,
    )


def _run_status_from_attempts(run: Run) -> str:
    latest = _latest_attempt(run)
    if latest is None:
        return RunStatus.PENDING.value
    if latest.status == AttemptStatus.SUCCEEDED.value:
        return RunStatus.COMPLETE.value
    if latest.status == AttemptStatus.FAILED.value:
        return RunStatus.FAILED.value
    if latest.status == AttemptStatus.CANCELED.value:
        return RunStatus.CANCELED.value
    if latest.status == AttemptStatus.RUNNING.value:
        return RunStatus.RUNNING.value
    return RunStatus.PENDING.value


def _cancel_experiment_attempts(experiment: Experiment) -> None:
    for run in experiment.runs:
        _cancel_run_attempts(run)


def _cancel_run_attempts(run: Run) -> None:
    canceled_any = False
    for attempt in run.attempts:
        if attempt.status in {AttemptStatus.QUEUED.value, "pending", AttemptStatus.RUNNING.value}:
            attempt.status = AttemptStatus.CANCELED.value
            attempt.completed_at = utc_now()
            canceled_any = True
    if canceled_any or run.status in {RunStatus.PENDING.value, RunStatus.RUNNING.value}:
        run.status = RunStatus.CANCELED.value


def _retry_attempt_id(run: Run, failed_attempt: RunAttempt, attempt_number: int) -> str:
    base_id = f"{failed_attempt.attempt_id}-retry-{attempt_number}"
    existing = {attempt.attempt_id for attempt in run.attempts}
    if base_id not in existing:
        return base_id
    return f"{base_id}-{len(existing) + 1}"


def _require_experiment(session: Session, experiment_id: int) -> Experiment:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment {experiment_id} does not exist.")
    return experiment


def _require_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run {run_id} does not exist.")
    return run


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if type(value) is int and value > 0:
        return value
    return None


def _elapsed_ms(start: float) -> int:
    return max(int((time.perf_counter() - start) * 1000), 0)


def _chunks(values: list[Run], size: int) -> list[list[Run]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _policy_tuple(values: Any) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(sorted({str(value).strip().lower() for value in values if str(value).strip()}))


def _intersect_allowed(
    base_allowed: Any, project_allowed: tuple[str, ...]
) -> tuple[str, ...] | None:
    normalized_base = _policy_tuple(base_allowed)
    if base_allowed is not None and project_allowed:
        return tuple(sorted(set(normalized_base) & set(project_allowed)))
    if base_allowed is not None:
        return normalized_base
    if project_allowed:
        return project_allowed
    return None


def _provider_timestamp_from_response(response: ProviderResponse) -> datetime | None:
    metadata = response.provider_metadata or {}
    payload = response.response_payload or {}
    for source in (metadata, payload):
        for key in ("created_at", "createdAt", "created"):
            parsed = _parse_provider_timestamp(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _parse_provider_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = float(value)
        if abs(timestamp) >= 100_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _data_egress_label(
    controls: ExecutionControls,
    *,
    provider: str,
    execution_config: ProviderExecutionConfig,
    dry_run: bool,
) -> str:
    if controls.data_egress_label:
        return controls.data_egress_label
    if execution_config.local_only:
        return "local_only"
    if dry_run:
        return "local_dry_run"
    return f"provider:{provider}"


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content")
    if content is None:
        content = message.get("content_ref", "")
    words = str(content).split()
    return max(len(words), 1 if content else 0)


def _message_reports(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "role": message.get("role"),
            "estimated_tokens": _estimate_message_tokens(message),
            "content_source": "content_ref" if message.get("content_ref") else "inline",
        }
        for index, message in enumerate(messages)
    ]


def _pricing_snapshot_for_request(
    experiment: Experiment, request: ProviderRequest
) -> dict[str, Any]:
    key = f"{request.provider}/{request.model}"
    snapshot = experiment.pricing_snapshot or {}
    entry = dict(snapshot.get("models", {}).get(key, {}))
    if snapshot.get("version") is not None:
        entry["version"] = snapshot["version"]
    return entry


def _pricing_snapshot_for_cached_response(
    experiment: Experiment, cached: ProviderCallCache
) -> dict[str, Any]:
    key = f"{cached.provider}/{cached.model}"
    snapshot = experiment.pricing_snapshot or {}
    entry = dict(snapshot.get("models", {}).get(key, {}))
    if snapshot.get("version") is not None:
        entry["version"] = snapshot["version"]
    return entry


def _system_fingerprint(provider_metadata: dict[str, Any] | None) -> str | None:
    metadata = provider_metadata or {}
    value = metadata.get("system_fingerprint") or metadata.get("model_revision")
    return str(value) if value is not None else None
