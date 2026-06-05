from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api.executor import (
    AttemptStatus,
    ExecutionControls,
    RetryPolicy,
    RunStatus,
    cancel_run,
    create_retry_attempt_for_run,
    execute_experiment,
    execute_run,
)
from model_eval_api import main as api_module
from model_eval_api.queue import (
    enqueue_deterministic_evaluators,
    enqueue_experiment_execution,
    enqueue_experiment_expansion,
    enqueue_export_generation,
    enqueue_run_execution,
)
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base, Experiment, Run, RunAttempt
from model_eval_api.persistence.models import utc_now
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_project,
    create_workspace,
)
from model_eval_api.providers import (
    ErrorKind,
    ProviderInvalidRequestError,
    ProviderExecutionConfig,
    ProviderRequest,
    ProviderResponse,
    ProviderRetryableError,
    ProviderUsage,
)


class FakeAdapter:
    provider = "openai"

    def __init__(
        self,
        outcomes: list[ProviderResponse | BaseException] | None = None,
        on_execute: Any = None,
    ) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[ProviderRequest] = []
        self.on_execute = on_execute

    def build_request(self, run_snapshot: dict[str, Any]) -> ProviderRequest:
        model_config = run_snapshot["model_config"]
        return ProviderRequest(
            provider=model_config["provider"],
            model=model_config["model"],
            payload={
                "model": model_config["model"],
                "input": run_snapshot["model_input_snapshot"]["final_messages"],
            },
            raw_provider_params=dict(model_config.get("raw_provider_params") or {}),
            normalized_config={
                "provider": model_config["provider"],
                "model": model_config["model"],
                "reasoning_level": model_config.get("reasoning_level"),
            },
        )

    def execute(
        self,
        request: ProviderRequest,
        *,
        config: Any = None,
        dry_run: bool = True,
    ) -> ProviderResponse:
        self.calls.append(request)
        if self.on_execute is not None:
            self.on_execute(request, len(self.calls))
        outcome = (
            self.outcomes.pop(0)
            if self.outcomes
            else ProviderResponse(
                provider="openai",
                model=request.model,
                response_payload={"id": "resp_ok", "text": "memo"},
                provider_response_id="resp_ok",
                output_text="memo",
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                cost_usd=0.01,
                dry_run=dry_run,
            )
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        job = {"fn": fn.__name__, "args": args, "kwargs": kwargs}
        self.jobs.append((fn.__name__, args, kwargs))
        return job


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        yield db


def _experiment(
    session: Session, *, controls: dict[str, Any] | None = None, replicates: int = 1
) -> Experiment:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="exec", name="Executor")
    manifest = parse_manifest(
        {
            "id": "executor_exp",
            "name": "Executor experiment",
            "cases": [{"id": "case", "prompt": "Write memo"}],
            "models": [
                {
                    "id": "openai_model",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {"temperature": 0.2, "reasoning_effort": "low"},
                }
            ],
            "system_prompts": [{"id": "system", "prompt": "Be concise."}],
            "warmers": [{"id": "none", "messages": []}],
            "design": {"replicates": replicates},
            "controls": controls or {},
            "evaluation": {"evaluators": []},
        }
    )
    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    return experiment


def _first_run(session: Session, experiment: Experiment) -> Run:
    run = session.scalars(select(Run).where(Run.experiment_id == experiment.id)).one()
    session.refresh(run)
    return run


def _attempts(session: Session, run: Run) -> list[RunAttempt]:
    return list(
        session.scalars(
            select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.id)
        )
    )


def test_successful_attempt_persists_provider_payload_and_usage(session: Session) -> None:
    experiment = _experiment(session)
    adapter = FakeAdapter()

    execute_experiment(session, experiment.id, adapters={"openai": adapter})
    session.commit()

    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    assert run.status == RunStatus.COMPLETE.value
    assert experiment.status == "complete"
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.SUCCEEDED.value
    assert attempts[0].request_payload["model"] == "gpt-5.5"
    assert attempts[0].response_payload["text"] == "memo"
    assert attempts[0].provider_response_id == "resp_ok"
    assert attempts[0].input_tokens == 10
    assert attempts[0].output_tokens == 5
    assert attempts[0].total_tokens == 15
    assert attempts[0].cost_usd == 0.01
    assert attempts[0].latency_ms is not None


def test_retryable_failure_creates_new_attempt_without_overwriting_failed_attempt(
    session: Session,
) -> None:
    experiment = _experiment(session, controls={"retry_failed": True})
    adapter = FakeAdapter(
        [
            ProviderRetryableError("temporary outage"),
            ProviderResponse(
                provider="openai",
                model="gpt-5.5",
                response_payload={"id": "resp_retry", "text": "memo"},
                provider_response_id="resp_retry",
                output_text="memo",
                usage=ProviderUsage(input_tokens=8, output_tokens=4, total_tokens=12),
                cost_usd=0.02,
            ),
        ]
    )

    execute_experiment(
        session,
        experiment.id,
        adapters={"openai": adapter},
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=3),
    )
    session.commit()

    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    assert len(attempts) == 2
    assert attempts[0].status == AttemptStatus.FAILED.value
    assert attempts[0].error_kind == ErrorKind.RETRYABLE.value
    assert attempts[0].retry_after_seconds == 3
    assert attempts[1].status == AttemptStatus.QUEUED.value
    assert attempts[1].attempt_number == 2
    assert attempts[1].parent_attempt_id == attempts[0].attempt_id
    assert attempts[1].available_at is not None

    attempts[1].available_at = utc_now() - timedelta(seconds=1)
    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=3),
    )
    session.commit()
    attempts = _attempts(session, run)

    assert run.status == RunStatus.COMPLETE.value
    assert attempts[1].status == AttemptStatus.SUCCEEDED.value


def test_replicate_attempts_and_retry_attempts_have_distinct_metadata(session: Session) -> None:
    workspace = create_workspace(session, slug="replicate", name="Replicate")
    project = create_project(session, workspace=workspace, slug="replicate", name="Replicate")
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": "replicate_metadata",
                "name": "Replicate metadata",
                "cases": [{"id": "case", "prompt": "Write memo"}],
                "models": [
                    {
                        "id": "openai_model",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "params": {"temperature": 0.2},
                    }
                ],
                "system_prompts": [{"id": "system", "prompt": "Be concise."}],
                "warmers": [{"id": "none", "messages": []}],
                "design": {"replicates": 2},
                "controls": {"retry_failed": True, "reliability_replicates": 2},
                "evaluation": {"evaluators": []},
            }
        ),
    )
    session.commit()
    run = _first_run(session, experiment)
    adapter = FakeAdapter(
        [
            ProviderRetryableError("temporary outage"),
            ProviderResponse(
                provider="openai",
                model="gpt-5.5",
                response_payload={"id": "resp_second", "text": "memo"},
                provider_response_id="resp_second",
                output_text="memo",
                usage=ProviderUsage(input_tokens=8, output_tokens=4, total_tokens=12),
                cost_usd=0.02,
            ),
        ]
    )

    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=30),
    )
    session.commit()

    attempts = _attempts(session, run)
    assert [attempt.replicate_index for attempt in attempts] == [0, 1, 0]
    assert [attempt.attempt_kind for attempt in attempts] == ["replicate", "replicate", "retry"]
    assert attempts[0].replicate_group_id == attempts[1].replicate_group_id
    assert attempts[2].replicate_group_id == attempts[0].replicate_group_id
    assert attempts[2].parent_attempt_id == attempts[0].attempt_id


def test_nonretryable_failure_stays_failed_without_retry(session: Session) -> None:
    experiment = _experiment(session, controls={"retry_failed": True})
    adapter = FakeAdapter([ProviderInvalidRequestError("bad schema")])

    execute_run(session, _first_run(session, experiment).id, adapters={"openai": adapter})
    session.commit()

    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    assert run.status == RunStatus.FAILED.value
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.FAILED.value
    assert attempts[0].error_kind == ErrorKind.INVALID_REQUEST.value
    assert attempts[0].terminal_failure_reason == "bad schema"


def test_exhausted_retryable_failure_records_terminal_reason(session: Session) -> None:
    experiment = _experiment(session, controls={"retry_failed": True})
    adapter = FakeAdapter(
        [
            ProviderRetryableError("temporary outage"),
            ProviderRetryableError("still down"),
        ]
    )
    run = _first_run(session, experiment)

    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
    )
    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    attempts[1].available_at = utc_now() - timedelta(seconds=1)
    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
    )
    session.commit()

    attempts = _attempts(session, run)
    assert run.status == RunStatus.FAILED.value
    assert attempts[-1].error_kind == ErrorKind.RETRYABLE.value
    assert attempts[-1].terminal_failure_reason == "still down"


def test_cancellation_before_execution_marks_queued_attempts_canceled(
    session: Session,
) -> None:
    experiment = _experiment(session)
    run = _first_run(session, experiment)
    experiment.status = "canceled"
    session.commit()
    adapter = FakeAdapter()

    execute_run(session, run.id, adapters={"openai": adapter})
    session.commit()

    attempts = _attempts(session, run)
    assert run.status == RunStatus.CANCELED.value
    assert attempts[0].status == AttemptStatus.CANCELED.value
    assert adapter.calls == []


def test_cost_cap_blocks_provider_call_before_execution(session: Session) -> None:
    experiment = _experiment(session, controls={"max_total_cost_usd": 0})
    adapter = FakeAdapter()

    execute_run(session, _first_run(session, experiment).id, adapters={"openai": adapter})
    session.commit()

    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    assert run.status == RunStatus.SKIPPED.value
    assert attempts[0].status == AttemptStatus.FAILED.value
    assert attempts[0].error_kind == ErrorKind.BLOCKED_BY_CONFIG.value
    assert attempts[0].terminal_failure_reason == "cost_cap_exceeded"
    assert adapter.calls == []


def test_provider_cache_hit_reuses_response_without_second_provider_call(
    session: Session,
) -> None:
    provider_timestamp = datetime(2026, 5, 21, 12, 15, tzinfo=timezone.utc)
    experiment = _experiment(
        session, controls={"cache_provider_calls": True, "max_total_cost_usd": 0.015}
    )
    adapter = FakeAdapter(
        [
            ProviderResponse(
                provider="openai",
                model="gpt-5.5",
                response_payload={"id": "resp_ok", "text": "memo"},
                provider_response_id="resp_ok",
                output_text="memo",
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                cost_usd=0.01,
                provider_metadata={"created_at": provider_timestamp.isoformat()},
            )
        ]
    )

    execute_run(session, _first_run(session, experiment).id, adapters={"openai": adapter})
    first_run = _first_run(session, experiment)
    first_run.status = RunStatus.PENDING.value
    session.add_all(
        [
            RunAttempt(
                run=first_run,
                attempt_id="manual-rerun-1",
                replicate_index=0,
                status=AttemptStatus.QUEUED.value,
                attempt_number=2,
            ),
            RunAttempt(
                run=first_run,
                attempt_id="manual-rerun-2",
                replicate_index=0,
                status=AttemptStatus.QUEUED.value,
                attempt_number=3,
            ),
        ]
    )
    session.commit()

    execute_run(session, first_run.id, adapters={"openai": adapter})
    session.commit()

    attempts = _attempts(session, first_run)
    assert len(adapter.calls) == 1
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.SUCCEEDED.value,
        AttemptStatus.SUCCEEDED.value,
        AttemptStatus.SUCCEEDED.value,
    ]
    assert attempts[1].cache_hit is True
    assert attempts[1].cost_usd == 0.0
    assert attempts[1].provider_timestamp.replace(tzinfo=timezone.utc) == provider_timestamp
    assert attempts[2].cache_hit is True
    assert attempts[2].cost_usd == 0.0
    assert attempts[2].provider_timestamp.replace(tzinfo=timezone.utc) == provider_timestamp


def test_provider_cache_hit_bypasses_cost_cap(session: Session) -> None:
    experiment = _experiment(session, controls={"cache_provider_calls": True})
    adapter = FakeAdapter()

    execute_run(
        session,
        _first_run(session, experiment).id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(local_only=False),
        dry_run=False,
    )
    run = _first_run(session, experiment)
    run.status = RunStatus.PENDING.value
    experiment.controls_snapshot = {"cache_provider_calls": True, "max_total_cost_usd": 0}
    session.add(
        RunAttempt(
            run=run,
            attempt_id="cached-over-cap",
            replicate_index=0,
            status=AttemptStatus.QUEUED.value,
            attempt_number=2,
        )
    )
    session.commit()

    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(local_only=False),
        dry_run=False,
    )
    session.commit()

    attempts = _attempts(session, run)
    assert len(adapter.calls) == 1
    assert attempts[1].status == AttemptStatus.SUCCEEDED.value
    assert attempts[1].cache_hit is True
    assert attempts[1].terminal_failure_reason is None


def test_successful_replicates_continue_until_all_attempts_finish(session: Session) -> None:
    experiment = _experiment(session, replicates=2)
    run = _first_run(session, experiment)
    adapter = FakeAdapter()

    execute_run(session, run.id, adapters={"openai": adapter})
    session.commit()

    attempts = _attempts(session, run)
    assert len(adapter.calls) == 2
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.SUCCEEDED.value,
        AttemptStatus.SUCCEEDED.value,
    ]
    assert run.status == RunStatus.COMPLETE.value


def test_provider_timestamp_parsing_handles_milliseconds_and_out_of_range_values(
    session: Session,
) -> None:
    provider_timestamp = datetime(2026, 5, 21, 12, 15, tzinfo=timezone.utc)
    experiment = _experiment(session, controls={"local_only": False})
    adapter = FakeAdapter(
        [
            ProviderResponse(
                provider="openai",
                model="gpt-5.5",
                response_payload={"id": "resp_ok", "text": "memo"},
                provider_response_id="resp_ok",
                output_text="memo",
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                cost_usd=0.01,
                provider_metadata={"created_at": int(provider_timestamp.timestamp() * 1000)},
            )
        ]
    )

    execute_run(
        session,
        _first_run(session, experiment).id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(local_only=False),
        dry_run=False,
    )
    session.commit()

    attempt = _attempts(session, _first_run(session, experiment))[0]
    assert attempt.status == AttemptStatus.SUCCEEDED.value
    assert attempt.provider_timestamp.replace(tzinfo=timezone.utc) == provider_timestamp

    rerun = _first_run(session, experiment)
    rerun.status = RunStatus.PENDING.value
    session.add(
        RunAttempt(
            run=rerun,
            attempt_id="bad-provider-timestamp",
            replicate_index=0,
            status=AttemptStatus.QUEUED.value,
            attempt_number=2,
        )
    )
    session.commit()
    fallback_adapter = FakeAdapter(
        [
            ProviderResponse(
                provider="openai",
                model="gpt-5.5",
                response_payload={"id": "resp_bad_timestamp", "text": "memo"},
                provider_response_id="resp_bad_timestamp",
                output_text="memo",
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                cost_usd=0.01,
                provider_metadata={"created_at": 10**30},
            )
        ]
    )

    execute_run(
        session,
        rerun.id,
        adapters={"openai": fallback_adapter},
        provider_config=ProviderExecutionConfig(local_only=False),
        dry_run=False,
    )
    session.commit()

    fallback_attempt = _attempts(session, rerun)[1]
    assert fallback_attempt.status == AttemptStatus.SUCCEEDED.value
    assert fallback_attempt.provider_timestamp == fallback_attempt.completed_at


def test_provider_cache_hit_bypasses_local_only_policy(
    session: Session,
) -> None:
    experiment = _experiment(session, controls={"cache_provider_calls": True})
    adapter = FakeAdapter()

    execute_run(
        session,
        _first_run(session, experiment).id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(local_only=False),
        dry_run=False,
    )
    first_run = _first_run(session, experiment)
    first_run.status = RunStatus.PENDING.value
    session.add(
        RunAttempt(
            run=first_run,
            attempt_id="cache-only-rerun",
            replicate_index=0,
            status=AttemptStatus.QUEUED.value,
            attempt_number=2,
        )
    )
    session.commit()

    execute_run(
        session,
        first_run.id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(local_only=True),
        dry_run=False,
    )
    session.commit()

    attempts = _attempts(session, first_run)
    assert len(adapter.calls) == 1
    assert attempts[1].status == AttemptStatus.SUCCEEDED.value
    assert attempts[1].cache_hit is True


def test_explicit_empty_runtime_allow_list_blocks_all_providers(
    session: Session,
) -> None:
    experiment = _experiment(session, controls={"local_only": False})
    adapter = FakeAdapter()

    execute_run(
        session,
        _first_run(session, experiment).id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(local_only=False, allowed_providers=()),
        dry_run=False,
    )
    session.commit()

    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    assert adapter.calls == []
    assert attempts[0].status == AttemptStatus.FAILED.value
    assert attempts[0].error_kind == ErrorKind.BLOCKED_BY_CONFIG.value


def test_cancellation_is_checked_before_queued_retry_attempt(session: Session) -> None:
    experiment = _experiment(session, controls={"retry_failed": True})

    def cancel_after_first_call(_: ProviderRequest, call_count: int) -> None:
        if call_count == 1:
            experiment.status = "canceled"

    adapter = FakeAdapter(
        [ProviderRetryableError("temporary outage"), ProviderResponse(provider="openai", model="gpt-5.5")],
        on_execute=cancel_after_first_call,
    )

    execute_run(
        session,
        _first_run(session, experiment).id,
        adapters={"openai": adapter},
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
    )
    session.commit()

    run = _first_run(session, experiment)
    attempts = _attempts(session, run)
    assert len(adapter.calls) == 1
    assert run.status == RunStatus.CANCELED.value
    assert attempts[0].status == AttemptStatus.FAILED.value
    assert attempts[1].status == AttemptStatus.CANCELED.value


def test_external_cancellation_is_reloaded_before_queued_retry_attempt(tmp_path) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'cancel.sqlite'}",
        connect_args={"check_same_thread": False},
        isolation_level="AUTOCOMMIT",
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as primary, session_factory() as canceling:
        experiment = _experiment(primary, controls={"retry_failed": True})
        run = _first_run(primary, experiment)

        def cancel_after_first_call(_: ProviderRequest, call_count: int) -> None:
            if call_count == 1:
                external = canceling.get(Experiment, experiment.id)
                assert external is not None
                external.status = "canceled"
                canceling.commit()

        adapter = FakeAdapter(
            [
                ProviderRetryableError("temporary outage"),
                ProviderResponse(provider="openai", model="gpt-5.5"),
            ],
            on_execute=cancel_after_first_call,
        )

        execute_run(
            primary,
            run.id,
            adapters={"openai": adapter},
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0),
        )
        primary.commit()

        attempts = _attempts(primary, run)
        assert len(adapter.calls) == 1
        assert run.status == RunStatus.CANCELED.value
        assert attempts[0].status == AttemptStatus.FAILED.value
        assert attempts[1].status == AttemptStatus.CANCELED.value


def test_run_status_reflects_latest_rerun_attempt(session: Session) -> None:
    experiment = _experiment(session)
    adapter = FakeAdapter()
    run = _first_run(session, experiment)
    execute_run(session, run.id, adapters={"openai": adapter})
    run.status = RunStatus.PENDING.value
    session.add(
        RunAttempt(
            run=run,
            attempt_id="manual-rerun-failure",
            replicate_index=0,
            status=AttemptStatus.QUEUED.value,
            attempt_number=2,
        )
    )
    session.commit()

    failing_adapter = FakeAdapter([ProviderInvalidRequestError("bad rerun")])
    execute_run(session, run.id, adapters={"openai": failing_adapter})
    session.commit()

    assert _first_run(session, experiment).status == RunStatus.FAILED.value
    assert _attempts(session, run)[-1].terminal_failure_reason == "bad rerun"


def test_cancel_run_leaves_terminal_run_state_unchanged(session: Session) -> None:
    experiment = _experiment(session)
    run = _first_run(session, experiment)
    execute_run(session, run.id, adapters={"openai": FakeAdapter()})
    session.commit()

    cancel_run(session, run.id)
    session.commit()

    attempts = _attempts(session, run)
    assert _first_run(session, experiment).status == RunStatus.COMPLETE.value
    assert attempts[0].status == AttemptStatus.SUCCEEDED.value


def test_manual_retry_requires_failed_latest_attempt(session: Session) -> None:
    experiment = _experiment(session)
    run = _first_run(session, experiment)
    execute_run(session, run.id, adapters={"openai": FakeAdapter()})
    session.commit()

    with pytest.raises(ValueError, match="latest attempt is not failed"):
        create_retry_attempt_for_run(session, run.id)


def test_manual_retry_reuses_existing_queued_retry(session: Session) -> None:
    experiment = _experiment(session)
    run = _first_run(session, experiment)
    attempt = _attempts(session, run)[0]
    attempt.status = AttemptStatus.FAILED.value
    attempt.error_kind = ErrorKind.RETRYABLE.value
    run.status = RunStatus.FAILED.value
    first_retry = create_retry_attempt_for_run(session, run.id)
    second_retry = create_retry_attempt_for_run(session, run.id)
    session.commit()

    assert second_retry is first_retry
    assert [attempt.attempt_kind for attempt in _attempts(session, run)] == [
        "replicate",
        "retry",
    ]


def test_queue_wiring_exposes_phase5_job_types_without_redis() -> None:
    queue = FakeQueue()

    assert enqueue_experiment_expansion(1, queue=queue)["fn"] == "expand_experiment_job"
    assert enqueue_run_execution(2, queue=queue)["fn"] == "execute_run_job"
    assert enqueue_deterministic_evaluators(1, queue=queue)["fn"] == (
        "run_deterministic_evaluators_job"
    )
    assert enqueue_export_generation(1, queue=queue)["fn"] == "generate_export_job"
    jobs = enqueue_experiment_execution(3, queue=queue)

    assert len(jobs) == 4
    assert [name for name, _, _ in queue.jobs][-4:] == [
        "expand_experiment_job",
        "execute_experiment_job",
        "run_deterministic_evaluators_job",
        "generate_export_job",
    ]
    assert jobs[1]["kwargs"]["depends_on"] == jobs[0]
    assert jobs[2]["kwargs"]["depends_on"] == jobs[1]
    assert jobs[3]["kwargs"]["depends_on"] == jobs[2]


def test_execution_controls_normalize_manifest_limits() -> None:
    controls = ExecutionControls.from_snapshot(
        {
            "max_parallel_requests": 0,
            "max_total_cost_usd": 1,
            "retry_failed": True,
            "cache_provider_calls": True,
            "local_only": False,
        }
    )

    assert controls.max_parallel_requests == 1
    assert controls.max_total_cost_usd == 1.0
    assert controls.retry_failed is True
    assert controls.cache_provider_calls is True
    assert controls.local_only is False


def test_monitor_api_lists_and_mutates_execution_state(session: Session) -> None:
    experiment = _experiment(session)
    run = _first_run(session, experiment)
    attempt = _attempts(session, run)[0]
    attempt.status = AttemptStatus.FAILED.value
    attempt.error_kind = ErrorKind.UNKNOWN.value
    attempt.error_message = "failed"
    attempt.request_payload = {"model": "gpt-5.5"}
    attempt.response_payload = {"provider_response_id": "resp_failed"}
    attempt.latency_ms = 1200
    run.status = RunStatus.FAILED.value
    experiment.status = "failed"
    session.commit()

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)

        experiments = client.get("/monitor/experiments")
        assert experiments.status_code == 200
        assert experiments.json()[0]["project_slug"] == experiment.project.slug
        assert experiments.json()[0]["status"] == "failed"

        runs = client.get(f"/monitor/experiments/{experiment.id}/runs")
        assert runs.status_code == 200
        assert runs.json()[0]["status"] == RunStatus.FAILED.value

        attempts = client.get(f"/monitor/runs/{run.id}/attempts")
        assert attempts.status_code == 200
        assert attempts.json()[0]["error_message"] == "failed"
        assert attempts.json()[0]["request_payload"]["model"] == "gpt-5.5"
        assert attempts.json()[0]["response_payload"]["provider_response_id"] == "resp_failed"
        assert attempts.json()[0]["latency_ms"] == 1200

        failures = client.get(f"/monitor/experiments/{experiment.id}/failures")
        assert failures.status_code == 200
        assert failures.json()[0]["attempt_id"] == attempt.attempt_id

        retry = client.post(f"/monitor/runs/{run.id}/retry")
        assert retry.status_code == 200
        assert retry.json()["status"] == AttemptStatus.QUEUED.value

        cancel_run = client.post(f"/monitor/runs/{run.id}/cancel")
        assert cancel_run.status_code == 200
        assert cancel_run.json()["status"] == RunStatus.CANCELED.value

        cancel = client.post(f"/monitor/experiments/{experiment.id}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "canceled"
    finally:
        api_module.app.dependency_overrides.clear()
