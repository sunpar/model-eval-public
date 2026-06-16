from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api.deterministic_evaluators import run_deterministic_evaluators
from model_eval_api.executor import execute_experiment
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base, Experiment, RunAttempt, Score
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_project,
    create_workspace,
    record_run_attempt,
)
from model_eval_api.providers import ProviderRequest, ProviderResponse, ProviderUsage
from model_eval_api.queue import run_deterministic_evaluators_job


class FakeAdapter:
    provider = "openai"

    def __init__(self, response: ProviderResponse) -> None:
        self.response = response

    def build_request(self, run_snapshot: dict[str, Any]) -> ProviderRequest:
        model_config = run_snapshot["model_config"]
        return ProviderRequest(
            provider=model_config["provider"],
            model=model_config["model"],
            payload={"model": model_config["model"], "input": []},
            raw_provider_params=dict(model_config.get("raw_provider_params") or {}),
            normalized_config={},
        )

    def execute(
        self,
        request: ProviderRequest,
        *,
        config: Any = None,
        dry_run: bool = True,
    ) -> ProviderResponse:
        return self.response


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


def test_successful_attempt_automatically_persists_deterministic_scores(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "required",
                "type": "deterministic",
                "definition": {
                    "kind": "required_sections",
                    "sections": ["Recommendation", "Risks"],
                },
                "version": 7,
            },
            {
                "id": "budget",
                "type": "deterministic",
                "definition": {"kind": "token_budget", "max_output_tokens": 25},
                "version": 3,
            },
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            },
        ],
    )
    output_text = "Recommendation: Buy copper.\n\nRisks: Supply normalizes faster than expected."
    response = ProviderResponse(
        provider="openai",
        model="gpt-5.5",
        response_payload={"output": [{"content": [{"type": "output_text", "text": output_text}]}]},
        provider_response_id="resp_ok",
        output_text=output_text,
        usage=ProviderUsage(input_tokens=20, output_tokens=12, total_tokens=32),
        cost_usd=0.01,
    )

    execute_experiment(session, experiment.id, adapters={"openai": FakeAdapter(response)})
    session.commit()

    scores = _scores(session)
    assert {(score.criterion, score.evaluator_version) for score in scores} == {
        ("required_sections", 7),
        ("token_budget", 3),
        ("no_empty_output", 1),
    }
    assert {score.evaluator_type for score in scores} == {"code"}
    assert {score.type for score in scores} == {"pass_fail"}
    assert _score(scores, "required_sections").value == {
        "passed": True,
        "missing_sections": [],
        "matched_sections": ["Recommendation", "Risks"],
        "required_sections": ["Recommendation", "Risks"],
        "evaluator_id": "required",
    }
    assert _score(scores, "token_budget").value["output_tokens"] == 12
    assert _score(scores, "token_budget").value["max_output_tokens"] == 25
    assert _score(scores, "token_budget").confidence == 1.0


def test_text_evaluators_read_anthropic_structured_content_payload(session: Session) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            },
            {
                "id": "required",
                "type": "deterministic",
                "definition": {"kind": "required_sections", "sections": ["Thesis", "Risks"]},
            },
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {
        "content": [
            {"type": "text", "text": "Thesis: copper tightens."},
            {"type": "text", "text": "\n\nRisks: mine supply recovers."},
        ]
    }
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert _score(scores, "no_empty_output").value["passed"] is True
    assert _score(scores, "required_sections").value["matched_sections"] == ["Thesis", "Risks"]


def test_evaluators_use_definition_kind_when_type_is_omitted(session: Session) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "definition": {"kind": "no_empty_output"},
            }
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"text": "memo"}
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert result["scores_recorded"] == 1
    assert _score(_scores(session), "no_empty_output").value["passed"] is True


def test_deterministic_evaluators_skip_explicit_non_deterministic_types(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            }
        ],
    )
    snapshots = dict(experiment.evaluator_snapshots or {})
    snapshots["not_empty"] = {**snapshots["not_empty"], "type": "llm_judge"}
    experiment.evaluator_snapshots = snapshots
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"text": "memo"}
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert result["scores_recorded"] == 0
    assert _scores(session) == []


def test_text_evaluators_read_chat_completion_content_parts(session: Session) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            },
            {
                "id": "required",
                "type": "deterministic",
                "definition": {"kind": "required_sections", "sections": ["Thesis", "Risks"]},
            },
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Thesis: copper tightens."},
                        {"type": "text", "text": "\n\nRisks: supply recovers."},
                    ]
                }
            }
        ]
    }
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert _score(scores, "no_empty_output").value["passed"] is True
    assert _score(scores, "required_sections").value["matched_sections"] == ["Thesis", "Risks"]


def test_deterministic_evaluators_are_idempotent_and_record_failures(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "investment_memo_required_sections_v1",
                "type": "code",
                "definition": {
                    "kind": "investment_memo_required_sections",
                    "sections": ["Recommendation", "Transmission mechanism", "Risks"],
                },
                "version": 5,
            },
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"text": "Recommendation: Hold copper."}
    session.commit()

    first_result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    second_result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert first_result["attempts_evaluated"] == 1
    assert first_result["scores_recorded"] == 1
    assert second_result["attempts_evaluated"] == 1
    assert second_result["scores_recorded"] == 0
    assert len(scores) == 1
    score = scores[0]
    assert score.evaluator_type == "code"
    assert score.evaluator_version == 5
    assert score.value["passed"] is False
    assert score.value["missing_sections"] == ["Transmission mechanism", "Risks"]
    assert "Missing required sections" in (score.explanation or "")


def test_deterministic_evaluators_skip_provider_dry_run_attempts(session: Session) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            }
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"dry_run": True, "request_payload": {"model": "gpt-5.5"}}
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert result["attempts_evaluated"] == 1
    assert result["scores_recorded"] == 0
    assert _scores(session) == []


def test_token_budget_evaluator_records_configuration_error_when_budget_missing(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "budget",
                "type": "deterministic",
                "definition": {"kind": "token_budget"},
            }
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"text": "memo"}
    attempt.output_tokens = 8
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    score = _score(_scores(session), "token_budget")
    assert score.type == "configuration_error"
    assert score.value == {
        "passed": None,
        "output_tokens": 8,
        "max_output_tokens": None,
        "error": "missing_max_output_tokens",
        "evaluator_id": "budget",
    }
    assert score.confidence == 0.0
    assert "no max output token budget configured" in (score.explanation or "")


def test_json_schema_evaluator_and_citation_placeholder_persist_typed_scores(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "schema",
                "type": "deterministic",
                "definition": {
                    "kind": "json_schema",
                    "schema": {
                        "type": "object",
                        "required": ["rating", "passed"],
                        "properties": {
                            "rating": {"type": "integer"},
                            "passed": {"type": "boolean"},
                        },
                    },
                },
            },
            {
                "id": "citation_required",
                "type": "deterministic",
                "definition": {"kind": "citation_required"},
            },
        ],
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"text": '{"rating": 4, "passed": true}'}
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert _score(scores, "json_schema").value == {
        "passed": True,
        "errors": [],
        "evaluator_id": "schema",
    }
    placeholder = _score(scores, "citation_required")
    assert placeholder.type == "placeholder"
    assert placeholder.value == {
        "status": "not_implemented",
        "passed": None,
        "evaluator_id": "citation_required",
    }
    assert placeholder.confidence == 0.0


def test_json_schema_evaluator_extracts_wrapped_json_object(session: Session) -> None:
    experiment = _json_schema_experiment(
        session,
        schema={
            "type": "object",
            "required": ["rating", "passed"],
            "properties": {
                "rating": {"type": "integer"},
                "passed": {"type": "boolean"},
            },
        },
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {
        "text": (
            "Here is the structured result:\n"
            "```json\n"
            '{"rating": 4, "passed": true}\n'
            "```"
        )
    }
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert _score(_scores(session), "json_schema").value == {
        "passed": True,
        "errors": [],
        "evaluator_id": "schema",
    }


def test_json_schema_evaluator_prefers_fenced_json_over_prose_braces(
    session: Session,
) -> None:
    experiment = _json_schema_experiment(
        session,
        schema={
            "type": "object",
            "required": ["rating", "passed"],
            "properties": {
                "rating": {"type": "integer"},
                "passed": {"type": "boolean"},
            },
        },
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {
        "text": (
            "Use the shape {rating, passed}.\n"
            "```json\n"
            '{"rating": 5, "passed": true}\n'
            "```"
        )
    }
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert _score(_scores(session), "json_schema").value == {
        "passed": True,
        "errors": [],
        "evaluator_id": "schema",
    }


def test_json_schema_evaluator_preserves_invalid_json_failure_for_wrapped_output(
    session: Session,
) -> None:
    experiment = _json_schema_experiment(session, schema={"type": "object"})
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {
        "text": (
            "Here is the structured result:\n"
            "```json\n"
            '{"rating": }\n'
            "```"
        )
    }
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    score = _score(_scores(session), "json_schema")
    assert score.value["passed"] is False
    assert score.value["evaluator_id"] == "schema"
    assert score.value["errors"][0].startswith("Invalid JSON:")
    assert "Output is not valid JSON" in (score.explanation or "")


def test_json_schema_integer_accepts_integral_float(session: Session) -> None:
    experiment = _json_schema_experiment(
        session,
        schema={
            "type": "object",
            "required": ["rating"],
            "properties": {"rating": {"type": "integer"}},
        },
    )
    attempt = _first_attempt(session, experiment)
    attempt.status = "succeeded"
    attempt.response_payload = {"text": '{"rating": 1.0}'}
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert _score(_scores(session), "json_schema").value == {
        "passed": True,
        "errors": [],
        "evaluator_id": "schema",
    }


def test_deterministic_queue_job_runs_evaluator_service(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            }
        ],
    )
    attempt = _first_attempt(session, experiment)
    experiment.status = "complete"
    attempt.status = "succeeded"
    attempt.response_payload = {"text": "memo"}
    session.commit()
    session_factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("model_eval_api.queue.get_session_factory", lambda: session_factory)

    result = run_deterministic_evaluators_job(experiment.id)

    assert result == {
        "job": "deterministic_evaluators",
        "experiment_id": experiment.id,
        "attempts_evaluated": 1,
        "scores_recorded": 1,
        "status": "complete",
    }
    assert _score(_scores(session), "no_empty_output").value["passed"] is True


def test_deterministic_queue_job_waits_for_experiment_completion(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment = _experiment(
        session,
        evaluators=[
            {
                "id": "not_empty",
                "type": "deterministic",
                "definition": {"kind": "no_empty_output"},
            }
        ],
    )
    attempt = _first_attempt(session, experiment)
    experiment.status = "running"
    attempt.status = "succeeded"
    attempt.response_payload = {"text": "memo"}
    session.commit()
    session_factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("model_eval_api.queue.get_session_factory", lambda: session_factory)

    result = run_deterministic_evaluators_job(experiment.id)

    assert result == {
        "job": "deterministic_evaluators",
        "experiment_id": experiment.id,
        "attempts_evaluated": 0,
        "scores_recorded": 0,
        "status": "running",
    }
    assert _scores(session) == []


def test_records_deterministic_divergence_scores_against_no_warmer_baseline(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "think like an analyst"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    baseline.status = "succeeded"
    baseline.response_payload = {
        "text": "# Thesis\nCopper supply tightens.\n\n# Risks\nMine supply may recover."
    }
    comparison.status = "succeeded"
    comparison.response_payload = {
        "text": "# Thesis\nCopper supply could tighten.\n\n# Watch Items\nThis will definitely rally."
    }
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    second_result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert result["scores_recorded"] == 4
    assert second_result["scores_recorded"] == 0
    assert len(scores) == 4
    assert {score.criterion for score in scores} == {
        "divergence_confidence_language",
        "divergence_section_structure",
        "divergence_semantic_overlap",
        "divergence_token_length",
    }
    for score in scores:
        assert score.type == "divergence"
        assert score.evaluator_type == "code"
        assert score.run_attempt_id == comparison.id
        assert score.value["comparison_scope"] == "case_model_system_prompt_warmer"
        assert score.value["baseline_attempt_id"] == baseline.attempt_id
        assert score.value["comparison_attempt_id"] == comparison.attempt_id
        assert "metric_source" in score.value
        assert "value" in score.value
        assert score.value["label"] in {"low", "medium", "high", "unavailable"}
        assert isinstance(score.value["warning"], str)
    semantic = _score(scores, "divergence_semantic_overlap")
    assert semantic.value["metric_source"] == "deterministic_semantic_overlap"
    assert "uncalibrated" in semantic.value["warning"]
    confidence = _score(scores, "divergence_confidence_language")
    assert confidence.value["metric_source"] == "deterministic_confidence_language"
    assert confidence.value["details"]["comparison"]["unsupported_certainty_markers"] == [
        "definitely",
        "will",
    ]


def test_divergence_scores_explain_missing_baseline(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[{"id": "analyst", "messages": [{"role": "user", "content": "warm"}]}],
    )
    comparison = _attempt(experiment, "analyst")
    comparison.status = "succeeded"
    comparison.response_payload = {"text": "Analyst-only answer"}
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert result["scores_recorded"] == 4
    assert {score.value["label"] for score in scores} == {"unavailable"}
    assert {score.value["baseline_attempt_id"] for score in scores} == {None}
    assert all("no no-warmer baseline" in score.value["warning"] for score in scores)


def test_divergence_scores_explain_missing_output_text(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    baseline.status = "succeeded"
    baseline.response_payload = {}
    comparison.status = "succeeded"
    comparison.response_payload = {"text": "Analyst answer with text."}
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert result["scores_recorded"] == 4
    assert {score.value["label"] for score in scores} == {"unavailable"}
    assert {score.value["baseline_attempt_id"] for score in scores} == {baseline.attempt_id}
    assert all(score.value["warning"] == "Baseline output is missing text." for score in scores)


def test_token_length_divergence_uses_local_text_not_provider_token_counts(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    for attempt in (baseline, comparison):
        attempt.status = "succeeded"
        attempt.response_payload = {"text": "Same short answer with identical local text."}
    baseline.output_tokens = 10
    comparison.output_tokens = 100
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    token_length = _score(_scores(session), "divergence_token_length")
    assert token_length.value["value"] == 0.0
    assert token_length.value["label"] == "low"
    assert token_length.value["details"]["baseline_tokens"] == token_length.value["details"][
        "comparison_tokens"
    ]


def test_divergence_scores_record_unavailable_for_failed_one_sided_comparison(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    baseline.status = "succeeded"
    baseline.response_payload = {"text": "Baseline answer with text."}
    comparison.status = "failed"
    comparison.response_payload = {}
    session.commit()

    result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert result["scores_recorded"] == 4
    assert {score.run_attempt_id for score in scores} == {comparison.id}
    assert {score.value["label"] for score in scores} == {"unavailable"}
    assert all(score.value["warning"] == "Comparison output is missing text." for score in scores)


def test_confidence_language_divergence_label_uses_delta_not_marker_presence(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    for attempt in (baseline, comparison):
        attempt.status = "succeeded"
        attempt.response_payload = {"text": "This will definitely rally."}
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    confidence = _score(_scores(session), "divergence_confidence_language")
    assert confidence.value["value"] == 0.0
    assert confidence.value["label"] == "low"


def test_divergence_scores_use_latest_same_replicate_baseline_with_text(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    baseline.status = "failed"
    baseline.response_payload = {}
    comparison.status = "succeeded"
    comparison.response_payload = {"text": "Comparison answer with text."}
    retry = record_run_attempt(
        session,
        run=baseline.run,
        attempt_id="baseline-retry",
        replicate_index=baseline.replicate_index,
        replicate_group_id=baseline.replicate_group_id,
        attempt_kind="retry",
        parent_attempt_id=baseline.attempt_id,
        attempt_number=2,
        status="succeeded",
        response_payload={"text": "New baseline answer with text."},
    )
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert {score.value["baseline_attempt_id"] for score in scores} == {retry.attempt_id}


def test_divergence_scores_do_not_fall_back_to_unmatched_replicate_baseline(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
        replicates=2,
    )
    baseline = _attempts(experiment, "none")[0]
    comparison = _attempts(experiment, "analyst")[1]
    baseline.status = "succeeded"
    baseline.response_payload = {"text": "Only the other replicate has a baseline."}
    comparison.status = "succeeded"
    comparison.response_payload = {"text": "Comparison answer with text."}
    session.commit()

    run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    scores = _scores(session)
    assert {score.run_attempt_id for score in scores} == {comparison.id}
    assert {score.value["baseline_attempt_id"] for score in scores} == {None}
    assert all("no no-warmer baseline" in score.value["warning"] for score in scores)


def test_divergence_score_reruns_do_not_duplicate_existing_criteria(
    session: Session,
) -> None:
    experiment = _experiment(
        session,
        evaluators=[],
        warmers=[
            {"id": "none", "messages": []},
            {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
        ],
    )
    baseline = _attempt(experiment, "none")
    comparison = _attempt(experiment, "analyst")
    baseline.status = "succeeded"
    baseline.response_payload = {"text": "Initial baseline answer."}
    comparison.status = "succeeded"
    comparison.response_payload = {"text": "Comparison answer."}
    session.commit()

    first_result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    retry = record_run_attempt(
        session,
        run=baseline.run,
        attempt_id="newer-baseline",
        replicate_index=baseline.replicate_index,
        replicate_group_id=baseline.replicate_group_id,
        attempt_kind="retry",
        parent_attempt_id=baseline.attempt_id,
        attempt_number=2,
        status="succeeded",
        response_payload={"text": "Newer baseline answer."},
    )
    session.commit()
    second_result = run_deterministic_evaluators(session, experiment_id=experiment.id)
    session.commit()

    assert retry.id is not None
    assert first_result["scores_recorded"] == 4
    assert second_result["scores_recorded"] == 0
    scores = _scores(session)
    assert len(scores) == 4
    assert len({score.criterion for score in scores}) == 4


def _experiment(
    session: Session,
    *,
    evaluators: list[dict[str, Any]],
    warmers: list[dict[str, Any]] | None = None,
    replicates: int = 1,
) -> Experiment:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="deterministic", name="Eval")
    manifest = parse_manifest(
        {
            "id": "deterministic_exp",
            "name": "Deterministic experiment",
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
            "warmers": warmers or [{"id": "none", "messages": []}],
            "design": {"replicates": replicates},
            "evaluation": {"evaluators": evaluators},
        }
    )
    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    return experiment


def _json_schema_experiment(session: Session, *, schema: dict[str, Any]) -> Experiment:
    return _experiment(
        session,
        evaluators=[
            {
                "id": "schema",
                "type": "deterministic",
                "definition": {
                    "kind": "json_schema",
                    "schema": schema,
                },
            }
        ],
    )


def _first_attempt(session: Session, experiment: Experiment) -> RunAttempt:
    return session.scalars(
        select(RunAttempt).where(RunAttempt.run.has(experiment_id=experiment.id))
    ).one()


def _attempt(experiment: Experiment, warmer_slug: str) -> RunAttempt:
    for run in experiment.runs:
        if run.warmer_slug == warmer_slug:
            return run.attempts[0]
    raise AssertionError(f"attempt not found for warmer {warmer_slug}")


def _attempts(experiment: Experiment, warmer_slug: str) -> list[RunAttempt]:
    for run in experiment.runs:
        if run.warmer_slug == warmer_slug:
            return sorted(run.attempts, key=lambda attempt: attempt.replicate_index)
    raise AssertionError(f"attempts not found for warmer {warmer_slug}")


def _scores(session: Session) -> list[Score]:
    return list(session.scalars(select(Score).order_by(Score.criterion, Score.id)))


def _score(scores: list[Score], criterion: str) -> Score:
    return next(score for score in scores if score.criterion == criterion)
