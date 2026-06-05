from __future__ import annotations

from collections.abc import Generator
import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from model_eval_api.execution_states import AttemptStatus, RunStatus
from model_eval_api.executor import execute_experiment, execute_run
from model_eval_api.headless import export_experiment
from model_eval_api.manifest import parse_manifest
from model_eval_api.otel_export import build_experiment_trace
from model_eval_api.persistence.database import create_database_engine
from model_eval_api.persistence.models import AuditLog, Base, Run, RunAttempt
from model_eval_api.persistence.repositories import (
    complete_artifact_preprocessing_run,
    create_artifact,
    create_artifact_preprocessing_run,
    create_experiment_from_manifest,
    create_project,
    create_workspace,
    record_score,
)
from model_eval_api.providers import (
    ErrorKind,
    OpenAIAdapter,
    ProviderExecutionConfig,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)


class RecordingAdapter:
    provider = "openai"

    def __init__(self, response: ProviderResponse | None = None) -> None:
        self.calls: list[ProviderRequest] = []
        self.response = response

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
        return self.response or ProviderResponse(
            provider="openai",
            model=request.model,
            response_payload={"id": "resp_phase13", "output_text": "Result"},
            provider_response_id="resp_phase13",
            output_text="Result",
            usage=ProviderUsage(input_tokens=11, output_tokens=7, total_tokens=18),
            cost_usd=0.00016,
            dry_run=dry_run,
            provider_metadata={"system_fingerprint": "fp_phase13"},
        )


def test_project_provider_allow_list_blocks_before_adapter_execution(
    session: Session,
) -> None:
    experiment = _experiment(session, project_allow_list=["anthropic"])
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(session, run.id, adapters={"openai": adapter})
    session.commit()

    attempt = _first_attempt(session, run.id)
    assert adapter.calls == []
    assert run.status == RunStatus.FAILED.value
    assert attempt.status == AttemptStatus.FAILED.value
    assert attempt.error_kind == ErrorKind.BLOCKED_BY_CONFIG.value
    assert attempt.terminal_failure_reason == "provider_blocked"

    logs = _audit_logs(session)
    assert [log.event_kind for log in logs] == [
        "experiment_created",
        "provider_call_blocked",
    ]
    assert logs[-1].entity_type == "run_attempt"
    assert logs[-1].details["provider"] == "openai"
    assert "request_payload" not in logs[-1].details
    assert "final_messages" not in logs[-1].details


def test_disjoint_project_and_runtime_allow_lists_block_all_providers(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        project_allow_list=["openai"],
        controls={"local_only": False},
    )
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(
            local_only=False,
            allowed_providers={"anthropic"},
        ),
    )
    session.commit()

    attempt = _first_attempt(session, run.id)
    assert adapter.calls == []
    assert run.status == RunStatus.FAILED.value
    assert attempt.status == AttemptStatus.FAILED.value
    assert attempt.error_kind == ErrorKind.BLOCKED_BY_CONFIG.value


def test_provider_policy_block_marks_all_replicates_terminal(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        controls={"local_only": False},
        replicates=2,
    )
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(
        session,
        run.id,
        adapters={"openai": adapter},
        provider_config=ProviderExecutionConfig(
            local_only=False,
            denied_providers={"openai"},
        ),
    )
    session.commit()

    attempts = list(
        session.scalars(select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.id))
    )
    assert adapter.calls == []
    assert run.status == RunStatus.FAILED.value
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.FAILED.value,
        AttemptStatus.FAILED.value,
    ]
    assert {attempt.terminal_failure_reason for attempt in attempts} == {"provider_blocked"}


def test_missing_provider_adapter_marks_all_replicates_terminal(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        controls={"local_only": False},
        replicates=2,
    )
    run = _first_run(session, experiment.id)

    execute_run(session, run.id, adapters={})
    session.commit()

    attempts = list(
        session.scalars(select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.id))
    )
    assert run.status == RunStatus.FAILED.value
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.FAILED.value,
        AttemptStatus.FAILED.value,
    ]
    assert {attempt.terminal_failure_reason for attempt in attempts} == {"provider_blocked"}


def test_context_budget_overage_fails_before_provider_call_and_reports_messages(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        controls={
            "context_budget_tokens": 4,
            "truncation_policy": "fail_on_over_budget",
        },
        case_prompt="One two three four five six",
    )
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(session, run.id, adapters={"openai": adapter})
    session.commit()

    session.refresh(run)
    attempt = _first_attempt(session, run.id)
    assert adapter.calls == []
    assert run.status == RunStatus.FAILED.value
    assert run.truncation_policy == "fail_on_over_budget"
    assert run.context_report["over_budget"] is True
    assert run.context_report["estimated_tokens"] > run.context_report["budget_tokens"]
    assert run.context_report["included_messages"] == []
    assert run.context_report["dropped_messages"]
    assert attempt.status == AttemptStatus.FAILED.value
    assert attempt.error_kind == ErrorKind.BLOCKED_BY_CONFIG.value
    assert attempt.terminal_failure_reason == "context_budget_exceeded"


def test_context_budget_overage_marks_all_replicates_terminal(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        controls={
            "context_budget_tokens": 4,
            "truncation_policy": "fail_on_over_budget",
        },
        case_prompt="One two three four five six",
        replicates=2,
    )
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(session, run.id, adapters={"openai": adapter})
    session.commit()

    attempts = list(
        session.scalars(select(RunAttempt).where(RunAttempt.run_id == run.id).order_by(RunAttempt.id))
    )
    assert adapter.calls == []
    assert run.status == RunStatus.FAILED.value
    assert [attempt.status for attempt in attempts] == [
        AttemptStatus.FAILED.value,
        AttemptStatus.FAILED.value,
    ]
    assert {
        attempt.terminal_failure_reason for attempt in attempts
    } == {"context_budget_exceeded"}


def test_successful_attempt_stores_reproducibility_metadata_and_pricing_snapshot(
    session: Session,
) -> None:
    experiment = _experiment(session, controls={"local_only": False})
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(session, run.id, adapters={"openai": adapter}, dry_run=False)
    session.commit()

    attempt = _first_attempt(session, run.id)
    assert attempt.status == AttemptStatus.SUCCEEDED.value
    assert attempt.provider == "openai"
    assert attempt.model == "gpt-5.5"
    assert attempt.provider_response_id == "resp_phase13"
    assert attempt.provider_timestamp is not None
    assert attempt.request_payload["model"] == "gpt-5.5"
    assert attempt.response_payload["output_text"] == "Result"
    assert attempt.pricing_snapshot["provider"] == "openai"
    assert attempt.pricing_snapshot["model"] == "gpt-5.5"
    assert attempt.provider_metadata["system_fingerprint"] == "fp_phase13"
    assert attempt.system_fingerprint == "fp_phase13"

    session.refresh(run)
    assert run.data_egress_label == "provider:openai"
    assert run.context_report["over_budget"] is False
    assert [log.event_kind for log in _audit_logs(session)] == [
        "experiment_created",
        "provider_call_started",
        "provider_call_succeeded",
    ]


def test_inline_manifest_provider_names_are_normalized_before_execution(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        controls={"local_only": False},
        provider=" OpenAI ",
    )
    run = _first_run(session, experiment.id)
    adapter = RecordingAdapter()

    execute_run(session, run.id, adapters={"openai": adapter}, dry_run=False)
    session.commit()

    attempt = _first_attempt(session, run.id)
    assert adapter.calls[0].provider == "openai"
    assert run.run_snapshot["model_config"]["provider"] == "openai"
    assert attempt.provider == "openai"
    assert attempt.status == AttemptStatus.SUCCEEDED.value


def test_local_only_controls_block_live_provider_calls_and_label_local_egress(
    session: Session,
) -> None:
    experiment = _experiment(session, controls={"local_only": True})
    run = _first_run(session, experiment.id)
    client_called = False

    def live_client(_: ProviderRequest) -> dict[str, Any]:
        nonlocal client_called
        client_called = True
        return {"id": "should_not_happen"}

    execute_run(
        session,
        run.id,
        adapters={"openai": OpenAIAdapter()},
        provider_config=ProviderExecutionConfig(local_only=False, client=live_client),
        dry_run=False,
    )
    session.commit()

    session.refresh(run)
    attempt = _first_attempt(session, run.id)
    assert client_called is False
    assert run.data_egress_label == "local_only"
    assert attempt.status == AttemptStatus.FAILED.value
    assert attempt.error_kind == ErrorKind.BLOCKED_BY_CONFIG.value
    assert attempt.terminal_failure_reason == "provider_blocked"


def test_execute_experiment_and_export_write_audit_events(session: Session) -> None:
    experiment = _experiment(session, controls={"local_only": False})

    execute_experiment(
        session,
        experiment.id,
        adapters={"openai": RecordingAdapter()},
        provider_config=ProviderExecutionConfig(local_only=False),
    )
    export_experiment(session, experiment.id, "json")

    events = [log.event_kind for log in _audit_logs(session)]
    assert "experiment_created" in events
    assert "experiment_execution_started" in events
    assert "experiment_execution_completed" in events
    assert "export_generated" in events


def test_otel_trace_redacts_prompt_artifact_payload_and_output_values(
    session: Session,
) -> None:
    sensitive_values = [
        "SECRET_PROMPT_23A",
        "SECRET_MANIFEST_23A",
        "SECRET_WARMER_23A",
        "SECRET_REQUEST_23A",
        "SECRET_RESPONSE_23A",
        "SECRET_OUTPUT_23A",
        "SECRET_TOKEN_23A",
        "SECRET_TERMINAL_23A",
        "SECRET_SCORE_LABEL_23A",
        "SECRET_SCORE_SOURCE_KIND_23A",
        "SECRET_SCORE_METRIC_23A",
        "SECRET_SCORE_SCOPE_23A",
        "SECRET_CHECKSUM_23A",
        "SECRET_SCREENSHOT_23A.png",
        "SECRET_OCR_23A",
        "SECRET_ARTIFACT_PATH_23A",
    ]
    experiment = _experiment(session, case_prompt="SECRET_PROMPT_23A")
    run = _first_run(session, experiment.id)
    attempt = _first_attempt(session, run.id)
    source = create_artifact(
        session,
        project=experiment.project,
        slug="private_screenshot",
        name="Private Screenshot",
        artifact_type="image",
        filename="SECRET_SCREENSHOT_23A.png",
        checksum_sha256="SECRET_CHECKSUM_23A",
        metadata={
            "ocr_text": "SECRET_OCR_23A",
            "local_path": "SECRET_ARTIFACT_PATH_23A",
        },
    )
    preprocessing = create_artifact_preprocessing_run(
        session,
        project=experiment.project,
        source_artifact=source,
        parser_name="pdf_visual",
        parser_version="1",
    )
    derived = create_artifact(
        session,
        project=experiment.project,
        slug="private_screenshot_page",
        name="Private Screenshot Page",
        artifact_type="image",
        filename="SECRET_SCREENSHOT_23A.png",
        metadata={"ocr_text": "SECRET_OCR_23A"},
    )
    complete_artifact_preprocessing_run(
        session,
        preprocessing_run=preprocessing,
        derived_artifacts=[derived],
    )
    experiment.manifest_snapshot = {
        **experiment.manifest_snapshot,
        "secret": "SECRET_MANIFEST_23A",
    }
    experiment.artifact_snapshots = {"private_screenshot": dict(source.snapshot)}
    experiment.warmer_snapshots = {
        "secret_warmer": {
            "id": "secret_warmer",
            "messages": [{"role": "user", "content": "SECRET_WARMER_23A"}],
        }
    }
    attempt.request_payload = {
        "input": [{"role": "user", "content": "SECRET_REQUEST_23A"}],
        "api_key": "SECRET_TOKEN_23A",
    }
    attempt.response_payload = {
        "output_text": "SECRET_OUTPUT_23A",
        "raw": "SECRET_RESPONSE_23A",
    }
    attempt.provider_metadata = {
        "system_fingerprint": "fp-safe",
        "token": "SECRET_TOKEN_23A",
    }
    attempt.terminal_failure_reason = "SECRET_TERMINAL_23A"
    attempt.input_tokens = 7
    attempt.output_tokens = 11
    attempt.total_tokens = 18
    attempt.cost_usd = 0.02
    record_score(
        session,
        run_attempt=attempt,
        type="pass_fail",
        evaluator_type="code",
        criterion="safe_criterion",
        value={
            "label": "SECRET_SCORE_LABEL_23A",
            "source_kind": "SECRET_SCORE_SOURCE_KIND_23A",
            "metric_source": "SECRET_SCORE_METRIC_23A",
            "comparison_scope": "SECRET_SCORE_SCOPE_23A",
        },
    )
    session.commit()

    encoded = json.dumps(build_experiment_trace(session, experiment.id), sort_keys=True)

    for value in sensitive_values:
        assert value not in encoded
    assert "request_payload" not in encoded
    assert "response_payload" not in encoded
    assert "model_eval.total_tokens" in encoded
    assert "model_eval.artifact_preprocessing_run" in encoded


def _experiment(
    session: Session,
    *,
    controls: dict[str, Any] | None = None,
    case_prompt: str = "Write a short memo.",
    project_allow_list: list[str] | None = None,
    replicates: int = 1,
    provider: str = "openai",
) -> Any:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(
        session,
        workspace=workspace,
        slug="phase13",
        name="Phase 13",
        provider_allow_list=project_allow_list or [],
    )
    manifest = parse_manifest(
        {
            "id": "phase13_exp",
            "name": "Phase 13 experiment",
            "cases": [{"id": "case", "prompt": case_prompt}],
            "models": [
                {
                    "id": "openai_model",
                    "provider": provider,
                    "model": "gpt-5.5",
                    "params": {"temperature": 0.2},
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


def _first_run(session: Session, experiment_id: int) -> Run:
    return session.scalars(select(Run).where(Run.experiment_id == experiment_id)).one()


def _first_attempt(session: Session, run_id: int) -> RunAttempt:
    return session.scalars(select(RunAttempt).where(RunAttempt.run_id == run_id)).one()


def _audit_logs(session: Session) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        yield db
