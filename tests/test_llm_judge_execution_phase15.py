from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.llm_judges import run_llm_judge
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base, JudgeExecution, Score
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_llm_judge_config,
    create_model_config,
    create_project,
    create_workspace,
    record_run_attempt,
    record_score,
)
from model_eval_api.providers import ProviderExecutionConfig, ProviderRequest
from model_eval_api.results_analytics import aggregate_experiment_results


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


@pytest.fixture()
def client(session: Session) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        yield TestClient(api_module.app)
    finally:
        api_module.app.dependency_overrides.clear()


def test_dry_run_judge_records_execution_requests_and_scores_without_metadata_leaks(
    session: Session,
) -> None:
    experiment = _judge_experiment(session)
    _finish_attempts(experiment, ["short answer", "a much longer and better answer"])

    result = run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=True,
        local_only=True,
        position_swap=False,
    )
    session.commit()

    execution = session.get(JudgeExecution, result["judge_execution_id"])
    assert execution is not None
    assert execution.status == "succeeded"
    assert execution.dry_run is True
    assert execution.evaluator_id == "memo_quality_judge_eval"
    assert execution.judge_config_snapshot["version"] == 1
    assert execution.source_run_attempt_ids == [attempt.id for attempt in _attempts(experiment)]
    assert "model_a" not in _json_text(execution.request_payload)
    assert "model_b" not in _json_text(execution.request_payload)
    assert "Answer A" in _json_text(execution.request_payload)
    assert execution.response_payload["comparisons"][0]["dry_run"] is True

    scores = session.scalars(
        select(Score).where(Score.evaluator_type == "llm_judge").order_by(Score.id)
    ).all()
    pairwise_scores = [score for score in scores if score.type == "pairwise_preference"]
    pass_fail_scores = [score for score in scores if score.type == "pass_fail"]
    rubric_scores = [score for score in scores if score.type == "rubric_score"]
    assert [score.type for score in pairwise_scores] == ["pairwise_preference", "pairwise_preference"]
    assert {score.value["outcome"] for score in pairwise_scores} == {"winner", "loser"}
    assert sorted(score.value["passed"] for score in pass_fail_scores) == [False, True]
    assert [score.value["dimension"] for score in rubric_scores] == ["specificity", "specificity"]
    assert all(score.value["judge_execution_id"] == execution.id for score in scores)
    assert all(score.value["judge_config_version"] == 1 for score in scores)
    assert sorted(score.value["answer_token_count"] for score in pairwise_scores) == [2, 6]


def test_live_judge_execution_is_blocked_by_local_only_policy(session: Session) -> None:
    experiment = _judge_experiment(session, controls={"local_only": True})
    _finish_attempts(experiment, ["answer one", "answer two"])

    with pytest.raises(ValueError, match="Local-only mode blocks outbound provider calls"):
        run_llm_judge(
            session,
            experiment_id=experiment.id,
            evaluator_id="memo_quality_judge_eval",
            dry_run=False,
            local_only=True,
        )


def test_experiment_local_only_policy_cannot_be_disabled_by_request(session: Session) -> None:
    experiment = _judge_experiment(session, controls={"local_only": True})
    _finish_attempts(experiment, ["answer one", "answer two"])
    called = False

    def client(_: ProviderRequest) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"output_text": '{"winner": "A"}'}

    with pytest.raises(ValueError, match="Local-only mode blocks outbound provider calls"):
        run_llm_judge(
            session,
            experiment_id=experiment.id,
            evaluator_id="memo_quality_judge_eval",
            dry_run=False,
            local_only=False,
            provider_config=ProviderExecutionConfig(local_only=False, client=client),
        )

    assert called is False


def test_empty_judge_allow_list_blocks_live_provider_execution(session: Session) -> None:
    experiment = _judge_experiment(session, controls={"local_only": False})
    _finish_attempts(experiment, ["answer one", "answer two"])
    called = False

    def client(_: ProviderRequest) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"output_text": '{"winner": "A"}'}

    with pytest.raises(ValueError, match="not in the allow list"):
        run_llm_judge(
            session,
            experiment_id=experiment.id,
            evaluator_id="memo_quality_judge_eval",
            dry_run=False,
            local_only=False,
            position_swap=False,
            provider_config=ProviderExecutionConfig(
                local_only=False,
                allowed_providers=(),
                client=client,
            ),
        )

    assert called is False


def test_context_budget_counts_answer_text_before_judge_execution(session: Session) -> None:
    experiment = _judge_experiment(
        session,
        controls={"context_budget_tokens": 8, "local_only": False},
    )
    _finish_attempts(
        experiment,
        [
            "answer one with several words",
            "answer two with several more words",
        ],
    )

    with pytest.raises(ValueError, match="Context budget exceeded before judge execution"):
        run_llm_judge(
            session,
            experiment_id=experiment.id,
            evaluator_id="memo_quality_judge_eval",
            dry_run=True,
            local_only=True,
            position_swap=False,
        )

    assert session.scalars(select(JudgeExecution)).all() == []


def test_judge_execution_requires_complete_experiment(session: Session) -> None:
    experiment = _judge_experiment(session)
    attempts = _attempts(experiment)
    attempts[0].status = "succeeded"
    attempts[0].response_payload = {"output_text": "partial answer"}
    attempts[0].run.status = "complete"
    experiment.status = "running"
    session.commit()

    with pytest.raises(ValueError, match="requires a complete experiment"):
        run_llm_judge(
            session,
            experiment_id=experiment.id,
            evaluator_id="memo_quality_judge_eval",
            dry_run=True,
            local_only=True,
        )

    assert session.scalars(select(JudgeExecution)).all() == []


def test_live_judge_execution_uses_provider_adapter_when_policy_allows(session: Session) -> None:
    experiment = _judge_experiment(session, controls={"local_only": False})
    _finish_attempts(experiment, ["answer one", "answer two"])
    provider_requests: list[ProviderRequest] = []

    def client(request: ProviderRequest) -> dict[str, Any]:
        provider_requests.append(request)
        assert "model_a" not in _json_text(request.payload)
        assert "model_b" not in _json_text(request.payload)
        assert "Answer A" in _json_text(request.payload)
        return {
            "id": "judge_live_response",
            "output_text": (
                '{"winner": "A", "confidence": 0.91, '
                '"pass_fail": {"A": true, "B": false}, '
                '"rubric_scores": {"A": {"specificity": 5}, "B": {"specificity": 2}}}'
            ),
            "usage": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
        }

    result = run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=False,
        local_only=False,
        position_swap=False,
        provider_config=ProviderExecutionConfig(local_only=False, client=client),
    )
    session.commit()

    execution = session.get(JudgeExecution, result["judge_execution_id"])
    assert execution is not None
    assert execution.dry_run is False
    assert execution.response_payload["comparisons"][0]["dry_run"] is False
    assert execution.response_payload["comparisons"][0]["provider_response_id"] == "judge_live_response"
    assert len(provider_requests) == 1

    scores = session.scalars(
        select(Score).where(Score.evaluator_type == "llm_judge").order_by(Score.id)
    ).all()
    pairwise_scores = [score for score in scores if score.type == "pairwise_preference"]
    pass_fail_scores = [score for score in scores if score.type == "pass_fail"]
    rubric_scores = [score for score in scores if score.type == "rubric_score"]
    assert [score.value["label"] for score in pairwise_scores] == ["A", "B"]
    assert [score.value["outcome"] for score in pairwise_scores] == ["winner", "loser"]
    assert [score.value["passed"] for score in pass_fail_scores] == [True, False]
    assert [score.value["dimension"] for score in rubric_scores] == ["specificity", "specificity"]
    assert {score.confidence for score in pairwise_scores} == {0.91}


def test_live_judge_parses_wrapped_json_and_rejects_boolean_confidence(
    session: Session,
) -> None:
    experiment = _judge_experiment(session, controls={"local_only": False})
    _finish_attempts(experiment, ["answer one", "answer two"])

    def client(_: ProviderRequest) -> dict[str, Any]:
        return {
            "id": "judge_wrapped_response",
            "output_text": (
                "Here is the decision:\n"
                "```json\n"
                '{"winner": "A", "confidence": true, "pass_fail": {"A": true, "B": false}}\n'
                "```"
            ),
            "usage": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
        }

    run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=False,
        local_only=False,
        position_swap=False,
        provider_config=ProviderExecutionConfig(local_only=False, client=client),
    )
    session.commit()

    pairwise_scores = session.scalars(
        select(Score)
        .where(Score.evaluator_type == "llm_judge", Score.type == "pairwise_preference")
        .order_by(Score.id)
    ).all()
    assert pairwise_scores
    assert {score.confidence for score in pairwise_scores} == {None}


def test_live_judge_records_label_specific_structured_output(session: Session) -> None:
    experiment = _judge_experiment(session, controls={"local_only": False})
    _finish_attempts(experiment, ["baseline answer", "warmer answer"])

    def client(_: ProviderRequest) -> dict[str, Any]:
        return {
            "id": "judge_structured_response",
            "output_text": (
                '{"winner": "B", "confidence": 0.82, '
                '"pass_fail": {"A": false, "B": true}, '
                '"answer_assessments": {'
                '"A": {"claim_score": 2, "conclusion_score": 3}, '
                '"B": {"claim_score": 5, "conclusion_score": 4, '
                '"carryover": {"status": "reused", "evidence": "Uses warmer evidence.", '
                '"explanation": "Warmer details are reused with support."}}}}'
            ),
            "usage": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
        }

    run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=False,
        local_only=False,
        position_swap=False,
        provider_config=ProviderExecutionConfig(local_only=False, client=client),
    )
    session.commit()

    scores = session.scalars(
        select(Score)
        .where(Score.evaluator_type == "llm_judge", Score.type == "pass_fail")
        .order_by(Score.id)
    ).all()

    assert scores[0].value["structured_output"] == {
        "claim_score": 2,
        "conclusion_score": 3,
    }
    assert scores[1].value["structured_output"]["claim_score"] == 5
    assert scores[1].value["structured_output"]["carryover"]["status"] == "reused"


def test_live_judge_normalizes_answer_assessment_label_keys(session: Session) -> None:
    experiment = _judge_experiment(session, controls={"local_only": False})
    _finish_attempts(experiment, ["baseline answer", "warmer answer"])

    def client(_: ProviderRequest) -> dict[str, Any]:
        return {
            "id": "judge_structured_response",
            "output_text": (
                '{"winner": "B", "confidence": 0.82, '
                '"pass_fail": {"A": false, "B": true}, '
                '"answer_assessments": {'
                '"a": {"claim_score": 2}, '
                '"b": {"claim_score": 5, "carryover": {"status": "reused"}}}}'
            ),
            "usage": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
        }

    run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=False,
        local_only=False,
        position_swap=False,
        provider_config=ProviderExecutionConfig(local_only=False, client=client),
    )
    session.commit()

    scores = session.scalars(
        select(Score)
        .where(Score.evaluator_type == "llm_judge", Score.type == "pass_fail")
        .order_by(Score.id)
    ).all()

    assert scores[0].value["structured_output"] == {"claim_score": 2}
    assert scores[1].value["structured_output"]["claim_score"] == 5
    assert scores[1].value["structured_output"]["carryover"]["status"] == "reused"


def test_judge_attempt_selection_uses_latest_success_and_excludes_dry_runs(
    session: Session,
) -> None:
    experiment = _judge_experiment(session)
    first, second = _finish_attempts(experiment, ["old answer", "answer two"])
    first.response_payload = {"output_text": "old answer", "dry_run": True}
    retry = record_run_attempt(
        session,
        run=first.run,
        attempt_id="retry-success",
        replicate_index=first.replicate_index,
        replicate_group_id=first.replicate_group_id,
        attempt_kind="retry",
        status="succeeded",
        attempt_number=2,
        parent_attempt_id=first.attempt_id,
        response_payload={"output_text": "latest answer"},
        output_tokens=2,
        total_tokens=2,
    )
    session.commit()

    result = run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=True,
        local_only=True,
        position_swap=False,
    )
    session.commit()

    execution = session.get(JudgeExecution, result["judge_execution_id"])
    assert execution is not None
    assert execution.source_run_attempt_ids == sorted([retry.id, second.id])


def test_judge_request_text_uses_common_provider_payload_shapes(session: Session) -> None:
    experiment = _judge_experiment(session)
    first, second = _finish_attempts(experiment, ["placeholder one", "placeholder two"])
    first.response_payload = {"choices": [{"message": {"content": "chat completion answer"}}]}
    second.response_payload = {
        "content": [{"type": "text", "text": "anthropic content answer"}]
    }
    session.commit()

    result = run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=True,
        local_only=True,
        position_swap=False,
    )
    session.commit()

    execution = session.get(JudgeExecution, result["judge_execution_id"])
    assert execution is not None
    payload_text = _json_text(execution.request_payload)
    assert "chat completion answer" in payload_text
    assert "anthropic content answer" in payload_text


def test_pairwise_judging_records_original_and_position_swapped_decisions(
    session: Session,
) -> None:
    experiment = _judge_experiment(session)
    _finish_attempts(experiment, ["first answer", "second answer"])

    result = run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=True,
        local_only=True,
        position_swap=True,
    )
    session.commit()

    execution = session.get(JudgeExecution, result["judge_execution_id"])
    assert execution is not None
    comparisons = execution.response_payload["comparisons"]
    assert len(comparisons) == 2
    assert comparisons[0]["swap_index"] == 0
    assert comparisons[1]["swap_index"] == 1
    assert comparisons[0]["answer_order"] == list(reversed(comparisons[1]["answer_order"]))
    scores = session.scalars(
        select(Score).where(Score.evaluator_type == "llm_judge")
    ).all()
    assert len([score for score in scores if score.type == "pairwise_preference"]) == 4
    assert len(scores) == 12


def test_duplicate_judge_execution_is_prevented(session: Session) -> None:
    experiment = _judge_experiment(session)
    _finish_attempts(experiment, ["answer one", "answer two"])

    run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=True,
        local_only=True,
        position_swap=False,
    )

    with pytest.raises(ValueError, match="already exists"):
        run_llm_judge(
            session,
            experiment_id=experiment.id,
            evaluator_id="memo_quality_judge_eval",
            dry_run=True,
            local_only=True,
            position_swap=False,
        )


def test_duplicate_judge_execution_integrity_error_maps_to_conflict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_integrity_error(*_: Any, **__: Any) -> dict[str, Any]:
        raise IntegrityError("insert", {}, Exception("unique"))

    monkeypatch.setattr(api_module, "run_llm_judge", raise_integrity_error)

    response = client.post(
        "/monitor/experiments/1/judges/memo_quality_judge_eval/run",
        json={"dry_run": True, "local_only": True, "position_swap": False},
    )

    assert response.status_code == 409


def test_calibration_and_verbosity_bias_summaries_compare_judge_to_human_scores(
    session: Session,
) -> None:
    experiment = _judge_experiment(session)
    short, long = _finish_attempts(
        experiment,
        ["short answer", "a much longer answer with more detail and evidence"],
    )
    record_score(
        session,
        run_attempt=long,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "winner", "reviewer_id": "human"},
    )
    record_score(
        session,
        run_attempt=short,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "loser", "reviewer_id": "human"},
    )
    record_score(
        session,
        run_attempt=long,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True, "reviewer_id": "human"},
    )
    record_score(
        session,
        run_attempt=long,
        type="rubric_notes",
        evaluator_type="human",
        criterion="blind_pairwise_rubric_notes",
        value={"note": "specific enough", "reviewer_id": "human"},
    )

    run_llm_judge(
        session,
        experiment_id=experiment.id,
        evaluator_id="memo_quality_judge_eval",
        dry_run=True,
        local_only=True,
        position_swap=False,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    calibration = analytics["judge_calibration"][0]
    assert calibration["evaluator_id"] == "memo_quality_judge_eval"
    assert calibration["pairwise_comparison_count"] == 2
    assert calibration["pass_fail_comparison_count"] == 1
    assert calibration["rubric_comparison_count"] == 1
    assert calibration["comparison_count"] == 3
    assert calibration["rubric_agreement_count"] == 0
    assert calibration["agreement_count"] == 3
    assert calibration["agreement_count"] + calibration["disagreement_count"] == calibration[
        "comparison_count"
    ]
    assert calibration["agreement_rate"] == 1.0
    assert calibration["disagreement_count"] == 0
    assert calibration["low_confidence_count"] == 0

    judge_scores = session.scalars(
        select(Score).where(Score.evaluator_type == "llm_judge").order_by(Score.id)
    ).all()
    assert {"pairwise_preference", "pass_fail", "rubric_score"} <= {
        score.type for score in judge_scores
    }

    verbosity = analytics["judge_verbosity_bias"][0]
    assert verbosity["evaluator_id"] == "memo_quality_judge_eval"
    assert verbosity["longer_answer_win_rate"] == 1.0
    assert verbosity["winner_average_tokens"] > verbosity["loser_average_tokens"]


def test_api_and_cli_style_payload_for_running_judge(client: TestClient, session: Session) -> None:
    experiment = _judge_experiment(session)
    _finish_attempts(experiment, ["answer one", "answer two"])

    response = client.post(
        f"/monitor/experiments/{experiment.id}/judges/memo_quality_judge_eval/run",
        json={"dry_run": True, "local_only": True, "position_swap": False},
    )

    assert response.status_code == 200
    assert response.json()["scores_recorded"] == 6
    assert response.json()["status"] == "succeeded"


def _judge_experiment(
    session: Session, *, controls: dict[str, Any] | None = None
):
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="phase15", name="Phase 15")
    create_model_config(
        session,
        project=project,
        slug="model_a",
        name="Model A",
        provider="openai",
        model="gpt-5.5",
    )
    create_model_config(
        session,
        project=project,
        slug="model_b",
        name="Model B",
        provider="openai",
        model="gpt-5.5",
    )
    manifest = parse_manifest(
        {
            "id": "phase15_exp",
            "name": "Phase 15 Experiment",
            "cases": [{"id": "case", "prompt": "Write memo"}],
            "models": ["model_a", "model_b"],
            "system_prompts": [{"id": "system", "prompt": "Be precise."}],
            "warmers": [{"id": "warmer", "messages": []}],
            "design": {"type": "full_factorial", "replicates": 1},
            "controls": controls or {},
            "evaluation": {
                "evaluators": [
                    {
                        "id": "memo_quality_judge_eval",
                        "type": "llm_judge",
                        "definition": {"judge_config_id": "memo_quality_judge"},
                    }
                ]
            },
        }
    )
    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Choose the better memo. Return JSON.",
        rubric_dimensions=[{"name": "specificity"}],
        output_schema={
            "type": "object",
            "properties": {"winner": {"type": "string"}},
            "required": ["winner"],
        },
        judge_model_config_slug="model_a",
        raw_provider_params={"temperature": 0},
    )
    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    return experiment


def _attempts(experiment) -> list[Any]:
    return [
        attempt
        for run in sorted(experiment.runs, key=lambda item: item.model_config_slug)
        for attempt in sorted(run.attempts, key=lambda item: item.id)
    ]


def _finish_attempts(experiment, outputs: list[str]):
    attempts = _attempts(experiment)
    assert len(attempts) == len(outputs)
    for attempt, output in zip(attempts, outputs, strict=True):
        attempt.status = "succeeded"
        attempt.response_payload = {"output_text": output}
        attempt.output_tokens = len(output.split())
        attempt.total_tokens = attempt.output_tokens
        attempt.run.status = "complete"
    experiment.status = "complete"
    return attempts


def _json_text(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True)
