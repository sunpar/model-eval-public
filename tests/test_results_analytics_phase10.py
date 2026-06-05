from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_project,
    create_review_set_from_completed_experiment,
    create_reviewer,
    create_workspace,
    record_run_attempt,
    record_score,
)
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


def test_aggregates_attempt_rates_tags_cost_latency_and_tokens(session: Session) -> None:
    experiment = _experiment(session)
    model_a_none = _attempt(experiment, "case", "model_a", "system", "none")
    model_b_none = _attempt(experiment, "case", "model_b", "system", "none")
    model_a_warm = _attempt(experiment, "case", "model_a", "system", "analyst")

    _finish_attempt(
        model_a_none,
        cost_usd=0.20,
        latency_ms=1000,
        input_tokens=100,
        output_tokens=40,
        total_tokens=140,
    )
    _finish_attempt(
        model_b_none,
        cost_usd=0.40,
        latency_ms=2000,
        input_tokens=120,
        output_tokens=60,
        total_tokens=180,
    )
    _finish_attempt(
        model_a_warm,
        cost_usd=0.30,
        latency_ms=1500,
        input_tokens=110,
        output_tokens=55,
        total_tokens=165,
    )
    record_score(
        session,
        run_attempt=model_a_none,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "winner"},
    )
    record_score(
        session,
        run_attempt=model_b_none,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "loser"},
    )
    record_score(
        session,
        run_attempt=model_a_none,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=model_b_none,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": False},
    )
    record_score(
        session,
        run_attempt=model_a_warm,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=model_b_none,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["too generic", "weak risks"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    assert analytics["summary"]["attempt_count"] == 3
    assert analytics["summary"]["winner_count"] == 1
    assert analytics["summary"]["loser_count"] == 1
    assert analytics["summary"]["win_rate"] == 0.5
    assert analytics["summary"]["pass_count"] == 2
    assert analytics["summary"]["fail_count"] == 1
    assert analytics["summary"]["pass_rate"] == pytest.approx(2 / 3)
    assert analytics["summary"]["average_cost_usd"] == pytest.approx(0.3)
    assert analytics["summary"]["average_latency_ms"] == pytest.approx(1500)
    assert analytics["summary"]["token_totals"] == {
        "input_tokens": 330,
        "output_tokens": 155,
        "total_tokens": 485,
    }
    assert analytics["failure_tag_frequency"] == [
        {"tag": "too generic", "count": 1, "rate": pytest.approx(1 / 3)},
        {"tag": "weak risks", "count": 1, "rate": pytest.approx(1 / 3)},
    ]
    assert _row(
        analytics["cost_quality_table"],
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="none",
    )["average_cost_usd"] == 0.2
    assert _row(
        analytics["latency_quality_table"],
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="analyst",
    )["average_latency_ms"] == 1500


def test_ties_and_cannot_judge_do_not_become_wins_or_losses(session: Session) -> None:
    experiment = _experiment(session)
    tied = _attempt(experiment, "case", "model_a", "system", "none")
    unclear = _attempt(experiment, "case", "model_b", "system", "none")
    _finish_attempt(tied)
    _finish_attempt(unclear)
    record_score(
        session,
        run_attempt=tied,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "tie", "winner": "tie"},
    )
    record_score(
        session,
        run_attempt=unclear,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "cannot_judge", "winner": "cannot_judge"},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    assert analytics["summary"]["winner_count"] == 0
    assert analytics["summary"]["loser_count"] == 0
    assert analytics["summary"]["tie_count"] == 1
    assert analytics["summary"]["cannot_judge_count"] == 1
    assert analytics["summary"]["win_rate"] is None


def test_canceled_attempts_are_counted_as_terminal_without_failure(session: Session) -> None:
    experiment = _experiment(session)
    attempt = _attempt(experiment, "case", "model_a", "system", "none")
    attempt.run.status = "canceled"
    attempt.status = "canceled"
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    assert analytics["summary"]["attempt_count"] == 1
    assert analytics["summary"]["failed_attempt_count"] == 0
    assert analytics["summary"]["failure_rate"] == 0


def test_results_pass_rates_use_blind_human_review_scores_only(session: Session) -> None:
    experiment = _experiment(session)
    attempt = _attempt(experiment, "case", "model_a", "system", "none")
    _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=attempt,
        type="pass_fail",
        evaluator_type="code",
        criterion="required_sections",
        value={"passed": False},
    )
    record_score(
        session,
        run_attempt=attempt,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=attempt,
        type="pairwise_preference",
        evaluator_type="code",
        criterion="deterministic_preference",
        value={"outcome": "loser"},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    assert analytics["summary"]["pass_count"] == 1
    assert analytics["summary"]["fail_count"] == 0
    assert analytics["summary"]["pass_rate"] == 1.0
    assert analytics["summary"]["loser_count"] == 0


def test_failure_tag_rate_counts_each_tag_once_per_attempt(session: Session) -> None:
    experiment = _experiment(session)
    attempt = _attempt(experiment, "case", "model_a", "system", "none")
    _finish_attempt(attempt)
    for _ in range(2):
        record_score(
            session,
            run_attempt=attempt,
            type="failure_tags",
            evaluator_type="human",
            criterion="blind_pairwise_failure_tags",
            value={"tags": ["too generic", "too generic"]},
        )
    record_score(
        session,
        run_attempt=attempt,
        type="failure_tags",
        evaluator_type="code",
        criterion="deterministic_failure_tags",
        value={"tags": ["ignored"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    assert analytics["failure_tag_frequency"] == [{"tag": "too generic", "count": 1, "rate": 1.0}]


def test_warmer_lift_uses_baseline_and_reports_missing_no_warmer(session: Session) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    no_baseline = _attempt(experiment, "other", "model_a", "system", "analyst")
    for attempt in (baseline, analyst, no_baseline):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=baseline,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": False},
    )
    record_score(
        session,
        run_attempt=analyst,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=no_baseline,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    lift = _row(
        analytics["warmer_lift"],
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="analyst",
    )
    assert lift["metric"] == "pass_rate"
    assert lift["baseline_rate"] == 0.0
    assert lift["warmer_rate"] == 1.0
    assert lift["lift"] == 1.0
    missing = _row(
        analytics["warmer_lift"],
        case_slug="other",
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="analyst",
    )
    assert missing["baseline_missing"] is True
    assert missing["lift"] is None


def test_context_sensitivity_and_divergence_use_available_spread(session: Session) -> None:
    experiment = _experiment(session)
    weak = _attempt(experiment, "case", "model_a", "system", "none")
    strong = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (weak, strong):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=weak,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": False},
    )
    record_score(
        session,
        run_attempt=strong,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=weak,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["missed transmission mechanism"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    sensitivity = _row(
        analytics["context_sensitivity"],
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
    )
    assert sensitivity["warmer_count"] == 2
    assert sensitivity["best_warmer_slug"] == "analyst"
    assert sensitivity["worst_warmer_slug"] == "none"
    assert sensitivity["score_spread"] == 1.0
    assert sensitivity["label"] == "high"
    divergence = _row(
        analytics["divergence_placeholders"],
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
    )
    assert divergence["label"] == "high"
    assert divergence["signals"] == ["score_spread", "failure_tag_spread"]


def test_divergence_metrics_include_stored_scores_and_failure_mode_spread(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=analyst,
        type="divergence",
        evaluator_type="code",
        criterion="divergence_semantic_overlap",
        value={
            "metric_source": "deterministic_semantic_overlap",
            "comparison_scope": "case_model_system_prompt_warmer",
            "baseline_attempt_id": baseline.attempt_id,
            "comparison_attempt_id": analyst.attempt_id,
            "value": 0.42,
            "label": "medium",
            "warning": "Deterministic lexical/keyphrase overlap is an uncalibrated heuristic.",
        },
    )
    record_score(
        session,
        run_attempt=baseline,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["weak risks"]},
    )
    record_score(
        session,
        run_attempt=analyst,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["overconfident conclusion"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    semantic = _row(
        analytics["divergence_metrics"],
        criterion="divergence_semantic_overlap",
        warmer_slug="analyst",
    )
    assert semantic["metric_source"] == "deterministic_semantic_overlap"
    assert semantic["baseline_attempt_id"] == baseline.attempt_id
    assert semantic["comparison_attempt_id"] == analyst.attempt_id
    assert semantic["value"] == 0.42
    assert semantic["label"] == "medium"
    failure_mode = _row(
        analytics["divergence_metrics"],
        criterion="divergence_failure_mode_spread",
        warmer_slug="analyst",
    )
    assert failure_mode["metric_source"] == "human_failure_tags"
    assert failure_mode["value"] == 1.0
    assert failure_mode["label"] == "high"
    assert failure_mode["details"]["baseline_tags"] == ["weak risks"]
    assert failure_mode["details"]["comparison_tags"] == ["overconfident conclusion"]


def test_failure_mode_spread_requires_tags_on_both_sides(session: Session) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=baseline,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["weak risks"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    failure_mode = _row(
        analytics["divergence_metrics"],
        criterion="divergence_failure_mode_spread",
        warmer_slug="analyst",
    )
    assert failure_mode["value"] is None
    assert failure_mode["label"] == "unavailable"
    assert "must be available on both baseline and comparison" in failure_mode["warning"]


def test_failure_mode_spread_does_not_fall_back_to_unmatched_replicate(
    session: Session,
) -> None:
    experiment = _experiment(session, replicates=2)
    baseline = _attempts(experiment, "case", "model_a", "system", "none")[0]
    analyst = _attempts(experiment, "case", "model_a", "system", "analyst")[1]
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=baseline,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["weak risks"]},
    )
    record_score(
        session,
        run_attempt=analyst,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["overconfident conclusion"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    failure_mode = _row(
        analytics["divergence_metrics"],
        criterion="divergence_failure_mode_spread",
        warmer_slug="analyst",
    )
    assert failure_mode["baseline_attempt_id"] is None
    assert failure_mode["value"] is None
    assert failure_mode["label"] == "unavailable"
    assert "No no-warmer baseline" in failure_mode["warning"]


def test_failure_mode_spread_uses_newest_same_replicate_baseline(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=baseline,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["old baseline"]},
    )
    newer_baseline = record_run_attempt(
        session,
        run=baseline.run,
        attempt_id="newer-baseline",
        replicate_index=baseline.replicate_index,
        replicate_group_id=baseline.replicate_group_id,
        attempt_kind="retry",
        parent_attempt_id=baseline.attempt_id,
        attempt_number=2,
        status="succeeded",
        response_payload={"output_text": "newer baseline"},
    )
    record_score(
        session,
        run_attempt=newer_baseline,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["new baseline"]},
    )
    record_score(
        session,
        run_attempt=analyst,
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["comparison"]},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    failure_mode = _row(
        analytics["divergence_metrics"],
        criterion="divergence_failure_mode_spread",
        warmer_slug="analyst",
    )
    assert failure_mode["baseline_attempt_id"] == newer_baseline.attempt_id
    assert failure_mode["details"]["baseline_tags"] == ["new baseline"]


def test_claim_conclusion_divergence_uses_judge_scores_and_deterministic_fallback(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    fallback_baseline = _attempt(experiment, "case", "model_b", "system", "none")
    fallback_analyst = _attempt(experiment, "case", "model_b", "system", "analyst")
    no_baseline = _attempt(experiment, "other", "model_a", "system", "analyst")
    for attempt in (baseline, analyst, fallback_baseline, fallback_analyst, no_baseline):
        _finish_attempt(attempt)
    baseline.response_payload = {
        "output_text": "Claim: Copper demand stays tight. Conclusion: Buy quality miners."
    }
    analyst.response_payload = {
        "output_text": "Claim: Copper demand is mixed. Conclusion: Keep a smaller long."
    }
    fallback_baseline.response_payload = {
        "output_text": "Claim: Inventories are falling. Conclusion: Maintain a bullish bias."
    }
    fallback_analyst.response_payload = {
        "output_text": "Claim: Inventories are falling but smelter margins are weak. "
        "Conclusion: Maintain a cautious bullish bias."
    }
    record_score(
        session,
        run_attempt=baseline,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "claim_quality", "score": 5},
        confidence=0.8,
    )
    record_score(
        session,
        run_attempt=analyst,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "claim_quality", "score": 3},
        confidence=0.7,
    )
    record_score(
        session,
        run_attempt=baseline,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "conclusion_support", "score": 4},
        confidence=0.8,
    )
    record_score(
        session,
        run_attempt=analyst,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "conclusion_support", "score": 4},
        confidence=0.7,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    claim = _row(
        analytics["divergence_metrics"],
        criterion="divergence_claim",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert claim["metric_source"] == "llm_judge_rubric"
    assert claim["baseline_attempt_id"] == baseline.attempt_id
    assert claim["comparison_attempt_id"] == analyst.attempt_id
    assert claim["value"] == 0.4
    assert claim["label"] == "medium"
    assert claim["details"]["baseline"]["dimension"] == "claim_quality"
    assert claim["details"]["comparison"]["score"] == 3.0
    conclusion = _row(
        analytics["divergence_metrics"],
        criterion="divergence_conclusion",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert conclusion["metric_source"] == "llm_judge_rubric"
    assert conclusion["value"] == 0.0
    assert conclusion["label"] == "low"

    fallback = _row(
        analytics["divergence_metrics"],
        criterion="divergence_claim",
        model_config_slug="model_b",
        warmer_slug="analyst",
    )
    assert fallback["metric_source"] == "deterministic_fallback"
    assert fallback["value"] is not None
    assert "No judge-backed claim evidence" in fallback["warning"]
    missing = _row(
        analytics["divergence_metrics"],
        criterion="divergence_claim",
        case_slug="other",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert missing["metric_source"] == "deterministic_fallback"
    assert missing["label"] == "unavailable"
    assert "No no-warmer baseline" in missing["warning"]


def test_conclusion_divergence_is_unavailable_when_signal_section_is_missing(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    baseline.response_payload = {"output_text": "Claim: Copper demand stays tight."}
    analyst.response_payload = {"output_text": "Claim: Copper demand is mixed."}
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    conclusion = _row(
        analytics["divergence_metrics"],
        criterion="divergence_conclusion",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert conclusion["metric_source"] == "deterministic_fallback"
    assert conclusion["label"] == "unavailable"
    assert "Missing comparable local text" in conclusion["warning"]


def test_claim_divergence_prefers_newest_judge_scores_when_confidence_ties(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    baseline.response_payload = {"output_text": "Claim: Copper demand stays tight."}
    analyst.response_payload = {"output_text": "Claim: Copper demand is mixed."}
    record_score(
        session,
        run_attempt=baseline,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "claim_quality", "score": 5},
        confidence=0.8,
    )
    record_score(
        session,
        run_attempt=analyst,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "claim_quality", "score": 4},
        confidence=0.8,
    )
    newest_baseline = record_score(
        session,
        run_attempt=baseline,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "claim_quality", "score": 2},
        confidence=0.8,
    )
    newest_analyst = record_score(
        session,
        run_attempt=analyst,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "claim_quality", "score": 1},
        confidence=0.8,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    claim = _row(
        analytics["divergence_metrics"],
        criterion="divergence_claim",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert claim["metric_source"] == "llm_judge_rubric"
    assert claim["details"]["baseline"]["score_id"] == newest_baseline.id
    assert claim["details"]["comparison"]["score_id"] == newest_analyst.id
    assert claim["value"] == 0.5


def test_claim_divergence_matches_signal_dimensions_by_token_boundary(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    baseline.response_payload = {"output_text": "Claim: Inventories are falling."}
    analyst.response_payload = {
        "output_text": "Claim: Inventories are falling but treatment charges are weak."
    }
    record_score(
        session,
        run_attempt=baseline,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "disclaimer_quality", "score": 5},
        confidence=0.8,
    )
    record_score(
        session,
        run_attempt=analyst,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={"evaluator_id": "memo_judge", "dimension": "disclaimer_quality", "score": 1},
        confidence=0.8,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    claim = _row(
        analytics["divergence_metrics"],
        criterion="divergence_claim",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert claim["metric_source"] == "deterministic_fallback"
    assert "No judge-backed claim evidence" in claim["warning"]


def test_claim_divergence_falls_back_when_judge_evidence_is_not_compatible(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    baseline.response_payload = {"output_text": "Thesis: Inventories are falling."}
    analyst.response_payload = {
        "output_text": "Thesis: Inventories are falling but treatment charges are weak."
    }
    record_score(
        session,
        run_attempt=baseline,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={
            "evaluator_id": "claim_judge",
            "judge_execution_id": 1,
            "dimension": "claim_quality",
            "score": 5,
        },
        confidence=0.8,
    )
    record_score(
        session,
        run_attempt=analyst,
        type="rubric_score",
        evaluator_type="llm_judge",
        criterion="llm_judge_rubric",
        value={
            "evaluator_id": "different_judge",
            "judge_execution_id": 2,
            "dimension": "claim_quality",
            "score": 1,
        },
        confidence=0.8,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    claim = _row(
        analytics["divergence_metrics"],
        criterion="divergence_claim",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert claim["metric_source"] == "deterministic_fallback"
    assert claim["details"]["baseline_text"] == "Inventories are falling."
    assert claim["details"]["comparison_text"] == (
        "Inventories are falling but treatment charges are weak."
    )


def test_fallback_and_carryover_extract_common_provider_payload_text(
    session: Session,
) -> None:
    experiment = _experiment(session)
    baseline = _attempt(experiment, "case", "model_a", "system", "none")
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (baseline, analyst):
        _finish_attempt(attempt)
    analyst.run.run_snapshot["warmer"]["messages"] = [
        {"role": "user", "content": "Focus on inventory drawdown."}
    ]
    baseline.response_payload = {
        "choices": [{"message": {"content": "Conclusion: Maintain a bullish bias."}}]
    }
    analyst.response_payload = {
        "content": [
            {
                "type": "text",
                "text": "Conclusion: Maintain a bullish bias with inventory drawdown support.",
            }
        ]
    }
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    conclusion = _row(
        analytics["divergence_metrics"],
        criterion="divergence_conclusion",
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert conclusion["metric_source"] == "deterministic_fallback"
    assert conclusion["label"] != "unavailable"
    carryover = _row(
        analytics["carryover_audit"],
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert carryover["status"] == "reused"
    assert carryover["details"]["matched_warmer_terms"] == ["drawdown", "inventory"]


def test_carryover_audit_prefers_highest_confidence_structured_evidence(
    session: Session,
) -> None:
    experiment = _experiment(session)
    analyst = _attempt(experiment, "case", "model_a", "system", "analyst")
    _finish_attempt(_attempt(experiment, "case", "model_a", "system", "none"))
    _finish_attempt(analyst)
    record_score(
        session,
        run_attempt=analyst,
        type="pass_fail",
        evaluator_type="llm_judge",
        criterion="llm_judge_pass_fail",
        value={
            "evaluator_id": "memo_judge",
            "structured_output": {
                "carryover": {
                    "status": "ignored",
                    "explanation": "Lower confidence judge saw no reuse.",
                }
            },
        },
        confidence=0.25,
    )
    record_score(
        session,
        run_attempt=analyst,
        type="pass_fail",
        evaluator_type="llm_judge",
        criterion="llm_judge_pass_fail",
        value={
            "evaluator_id": "memo_judge",
            "structured_output": {
                "carryover": {
                    "status": "reused",
                    "explanation": "Higher confidence judge saw supported reuse.",
                }
            },
        },
        confidence=0.9,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    row = _row(
        analytics["carryover_audit"],
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert row["status"] == "reused"
    assert row["explanation"] == "Higher confidence judge saw supported reuse."


def test_carryover_audit_uses_structured_judge_output_and_local_overlap(
    session: Session,
) -> None:
    experiment = _experiment(session)
    structured = _attempt(experiment, "case", "model_a", "system", "analyst")
    local = _attempt(experiment, "case", "model_b", "system", "analyst")
    for attempt in (
        _attempt(experiment, "case", "model_a", "system", "none"),
        structured,
        _attempt(experiment, "case", "model_b", "system", "none"),
        local,
    ):
        _finish_attempt(attempt)
    for attempt in (structured, local):
        attempt.run.run_snapshot["warmer"]["messages"] = [
            {
                "role": "user",
                "content": "Focus on inventory drawdown and futures curve inversion.",
            }
        ]
    structured.response_payload = {"output_text": "The answer repeats the warmer too narrowly."}
    local.response_payload = {
        "output_text": "Inventory drawdown and futures curve inversion support the setup."
    }
    record_score(
        session,
        run_attempt=structured,
        type="pass_fail",
        evaluator_type="llm_judge",
        criterion="llm_judge_pass_fail",
        value={
            "evaluator_id": "memo_judge",
            "passed": False,
            "structured_output": {
                "carryover": {
                    "status": "overfit",
                    "evidence": "Repeats inventory drawdown without new support.",
                    "explanation": "The answer leans too heavily on the warmer.",
                }
            },
        },
        confidence=0.65,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    structured_row = _row(
        analytics["carryover_audit"],
        model_config_slug="model_a",
        warmer_slug="analyst",
    )
    assert structured_row["status"] == "overfit"
    assert structured_row["source_evidence"] == "structured_judge_output"
    assert structured_row["explanation"] == "The answer leans too heavily on the warmer."
    local_row = _row(
        analytics["carryover_audit"],
        model_config_slug="model_b",
        warmer_slug="analyst",
    )
    assert local_row["status"] == "reused"
    assert local_row["source_evidence"] == "local_warmer_overlap"
    assert local_row["details"]["matched_warmer_terms"] == [
        "curve",
        "drawdown",
        "futures",
        "inventory",
        "inversion",
    ]


def test_divergence_and_carryover_summaries_group_samples_and_source_kinds(
    session: Session,
) -> None:
    experiment = _experiment(session, replicates=2)
    baseline_attempts = _attempts(experiment, "case", "model_a", "system", "none")
    warmer_attempts = _attempts(experiment, "case", "model_a", "system", "analyst")
    for attempt in baseline_attempts + warmer_attempts:
        _finish_attempt(attempt)
    for index, attempt in enumerate(warmer_attempts):
        record_score(
            session,
            run_attempt=attempt,
            type="divergence",
            evaluator_type="code",
            criterion="divergence_semantic_overlap",
            value={
                "metric_source": "deterministic_semantic_overlap",
                "comparison_scope": "case_model_system_prompt_warmer",
                "baseline_attempt_id": baseline_attempts[index].attempt_id,
                "comparison_attempt_id": attempt.attempt_id,
                "value": 0.2 + (index * 0.4),
                "label": "medium" if index == 0 else "high",
                "warning": "Semantic overlap is a deterministic heuristic.",
            },
            confidence=0.35,
        )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    divergence = _row(
        analytics["divergence_summary"],
        criterion="divergence_semantic_overlap",
        metric_source="deterministic_semantic_overlap",
    )
    assert divergence["sample_count"] == 2
    assert divergence["source_kind"] == "deterministic_heuristic"
    assert divergence["warning_label"] == "heuristic"
    assert divergence["label"] == "medium"
    assert divergence["value"] == pytest.approx(0.4)
    assert all(row["sample_count"] == 1 for row in analytics["divergence_metrics"])

    carryover = _row(
        analytics["carryover_summary"],
        warmer_slug="analyst",
        source_evidence="local_warmer_overlap",
        status="ignored",
    )
    assert carryover["sample_count"] == 2
    assert carryover["source_kind"] == "deterministic_heuristic"
    assert carryover["warning_label"] == "heuristic"
    assert all(row["sample_count"] == 1 for row in analytics["carryover_audit"])


def test_context_sensitivity_does_not_compare_mixed_quality_metrics(session: Session) -> None:
    experiment = _experiment(session)
    pass_fail = _attempt(experiment, "case", "model_a", "system", "none")
    pairwise_only = _attempt(experiment, "case", "model_a", "system", "analyst")
    for attempt in (pass_fail, pairwise_only):
        _finish_attempt(attempt)
    record_score(
        session,
        run_attempt=pass_fail,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=pairwise_only,
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "loser"},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)

    sensitivity = _row(
        analytics["context_sensitivity"],
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
    )
    assert sensitivity["scored_warmer_count"] == 2
    assert sensitivity["metric"] is None
    assert sensitivity["best_warmer_slug"] is None
    assert sensitivity["worst_warmer_slug"] is None
    assert sensitivity["score_spread"] is None
    assert sensitivity["label"] == "insufficient_data"
    divergence = _row(
        analytics["divergence_placeholders"],
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
    )
    assert divergence["score_spread"] is None
    assert divergence["signals"] == []
    assert divergence["label"] == "insufficient_data"


def test_nondeterminism_summaries_exclude_retry_attempts_from_reliability_samples(
    session: Session,
) -> None:
    experiment = _experiment(session, replicates=2)
    attempts = _attempts(experiment, "case", "model_a", "system", "none")
    _finish_attempt(attempts[0], cost_usd=0.20, latency_ms=1000, total_tokens=100)
    attempts[0].attempt_kind = "replicate"
    attempts[0].replicate_group_id = "case:model_a:system:none"
    attempts[1].status = "failed"
    attempts[1].attempt_kind = "replicate"
    attempts[1].replicate_group_id = "case:model_a:system:none"
    retry = record_run_attempt(
        session,
        run=attempts[1].run,
        attempt_id="retry-case-model-a",
        replicate_index=1,
        replicate_group_id="case:model_a:system:none",
        attempt_kind="retry",
        parent_attempt_id=attempts[1].attempt_id,
        status="succeeded",
        cost_usd=0.40,
        latency_ms=2500,
        total_tokens=180,
        response_payload={"output_text": "retry answer"},
    )
    session.flush()
    record_score(
        session,
        run_attempt=attempts[0],
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    record_score(
        session,
        run_attempt=attempts[1],
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": False},
    )
    record_score(
        session,
        run_attempt=retry,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    by_model = _row(
        analytics["nondeterminism_by_dimension"]["model_config_slug"],
        model_config_slug="model_a",
    )

    assert by_model["sample_count"] == 2
    assert by_model["retry_attempt_count"] == 1
    assert by_model["failure_rate_interval"]["label"] == "low_sample"
    assert by_model["failure_rate_interval"]["sample_count"] == 2
    assert by_model["pass_rate_interval"]["rate"] == 0.5
    assert by_model["cost_usd_interval"]["mean"] == 0.2
    assert by_model["cost_usd_interval"]["sample_count"] == 1
    assert by_model["cost_usd_interval"]["label"] == "single_sample"


def test_uncertainty_intervals_report_zero_and_one_sample_behavior(session: Session) -> None:
    experiment = _experiment(session)
    attempt = _attempt(experiment, "case", "model_a", "system", "none")
    _finish_attempt(attempt, cost_usd=0.25, latency_ms=1000, total_tokens=50)
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    summary = analytics["summary"]

    assert summary["win_rate_interval"] == {
        "sample_count": 0,
        "rate": None,
        "lower": None,
        "upper": None,
        "label": "no_samples",
    }
    assert summary["failure_rate_interval"]["sample_count"] == 1
    assert summary["failure_rate_interval"]["lower"] == summary["failure_rate_interval"]["upper"] == 0.0
    assert summary["failure_rate_interval"]["label"] == "single_sample"
    assert summary["cost_usd_interval"]["lower"] == summary["cost_usd_interval"]["upper"] == 0.25
    assert summary["cost_usd_interval"]["variance"] == 0.0


def test_cost_quality_frontier_marks_dominated_rows_and_missing_inputs(
    session: Session,
) -> None:
    experiment = _experiment(session)
    model_a_none = _attempt(experiment, "case", "model_a", "system", "none")
    model_b_none = _attempt(experiment, "case", "model_b", "system", "none")
    model_a_warm = _attempt(experiment, "case", "model_a", "system", "analyst")
    model_b_warm = _attempt(experiment, "case", "model_b", "system", "analyst")
    other_model_a = _attempt(experiment, "other", "model_a", "system", "none")
    _finish_attempt(model_a_none, cost_usd=0.20, latency_ms=1000)
    _finish_attempt(model_b_none, cost_usd=0.35, latency_ms=1400)
    _finish_attempt(model_a_warm, cost_usd=None, latency_ms=900)
    _finish_attempt(model_b_warm, cost_usd=0.10, latency_ms=None)
    _finish_attempt(other_model_a, cost_usd=0.05, latency_ms=700)
    for attempt in [model_a_none, model_b_none, model_a_warm, model_b_warm]:
        record_score(
            session,
            run_attempt=attempt,
            type="pass_fail",
            evaluator_type="human",
            criterion="blind_pairwise_pass_fail",
            value={"passed": True},
        )
    record_score(
        session,
        run_attempt=model_a_none,
        type="pass_fail",
        evaluator_type="llm_judge",
        criterion="llm_judge_pass_fail",
        value={"passed": True, "evaluator_id": "judge_1"},
        confidence=0.9,
    )
    record_score(
        session,
        run_attempt=model_a_warm,
        type="divergence",
        evaluator_type="code",
        criterion="divergence_semantic_overlap",
        value={
            "metric_source": "deterministic_semantic_overlap",
            "comparison_scope": "case_model_system_prompt_warmer",
            "baseline_attempt_id": model_a_none.attempt_id,
            "comparison_attempt_id": model_a_warm.attempt_id,
            "value": 0.62,
            "label": "high",
            "warning": "Semantic overlap is a deterministic heuristic.",
        },
        confidence=0.4,
    )
    session.commit()

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    frontier_rows = analytics["cost_quality_frontier"]
    frontier = _row(
        frontier_rows,
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="none",
    )
    dominated = _row(
        frontier_rows,
        case_slug="case",
        model_config_slug="model_b",
        system_prompt_slug="system",
        warmer_slug="none",
    )
    missing_cost = _row(
        frontier_rows,
        case_slug="case",
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="analyst",
    )
    missing_latency = _row(
        frontier_rows,
        case_slug="case",
        model_config_slug="model_b",
        system_prompt_slug="system",
        warmer_slug="analyst",
    )
    missing_quality = _row(
        frontier_rows,
        case_slug="other",
        model_config_slug="model_a",
        system_prompt_slug="system",
        warmer_slug="none",
    )

    assert frontier["is_frontier"] is True
    assert frontier["dominance_status"] == "frontier"
    assert frontier["quality_metric"] == "pass_rate"
    assert frontier["quality_rate"] == 1.0
    assert frontier["quality_interval"]["label"] == "single_sample"
    assert frontier["cost_usd_interval"]["lower"] == 0.2
    assert frontier["latency_ms_interval"]["upper"] == 1000.0
    assert frontier["judge_calibration_overlays"] == [
        {
            "evaluator_id": "judge_1",
            "comparison_count": 1,
            "agreement_rate": 1.0,
            "low_confidence_count": 0,
        }
    ]
    assert dominated["is_frontier"] is False
    assert dominated["dominance_status"] == "dominated"
    assert dominated["dominated_by"] == frontier["frontier_key"]
    assert missing_cost["dominance_status"] == "missing_cost"
    assert missing_cost["warmer_lift"]["lift"] == 0.0
    assert any(
        row["criterion"] == "divergence_semantic_overlap" and row["label"] == "high"
        for row in missing_cost["divergence_summary"]
    )
    assert missing_latency["dominance_status"] == "missing_latency"
    assert missing_quality["dominance_status"] == "missing_quality"


def test_results_analytics_filters_feed_frontier_and_reviewer_scores(
    session: Session,
) -> None:
    experiment = _experiment(session)
    experiment.manifest_snapshot = {
        **dict(experiment.manifest_snapshot or {}),
        "suite": {"id": "suite_a", "split": "holdout"},
    }
    model_a = _attempt(experiment, "case", "model_a", "system", "none")
    model_b = _attempt(experiment, "case", "model_b", "system", "none")
    other = _attempt(experiment, "other", "model_a", "system", "none")
    _finish_attempt(model_a, cost_usd=0.20, latency_ms=1000)
    _finish_attempt(model_b, cost_usd=0.25, latency_ms=900)
    _finish_attempt(other, cost_usd=0.15, latency_ms=800)
    record_score(
        session,
        run_attempt=model_a,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True, "reviewer_id": "alice"},
    )
    record_score(
        session,
        run_attempt=model_a,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": False, "reviewer_id": "bob"},
    )
    record_score(
        session,
        run_attempt=model_b,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": False, "reviewer_id": "alice"},
    )
    record_score(
        session,
        run_attempt=model_a,
        type="pass_fail",
        evaluator_type="llm_judge",
        criterion="llm_judge_pass_fail",
        value={"passed": True, "evaluator_id": "judge_1"},
        confidence=0.9,
    )
    session.commit()

    analytics = aggregate_experiment_results(
        session,
        experiment_id=experiment.id,
        case_slug="case",
        suite_slug="suite_a",
        suite_split="holdout",
        model_config_slug="model_a",
        evaluator_source="human",
        reviewer_id="alice",
    )

    assert analytics["filters"]["case_slug"] == "case"
    assert analytics["filters"]["suite_slug"] == "suite_a"
    assert analytics["filters"]["suite_split"] == "holdout"
    assert analytics["filters"]["model_config_slug"] == "model_a"
    assert analytics["filters"]["evaluator_source"] == "human"
    assert analytics["filters"]["reviewer_id"] == "alice"
    assert analytics["summary"]["attempt_count"] == 1
    assert analytics["summary"]["pass_rate"] == 1.0
    assert analytics["summary"]["fail_count"] == 0
    assert [
        row["model_config_slug"] for row in analytics["cost_quality_frontier"]
    ] == ["model_a"]
    assert analytics["cost_quality_frontier"][0]["judge_calibration_overlays"] == [
        {
            "evaluator_id": "judge_1",
            "comparison_count": 1,
            "agreement_rate": 1.0,
            "low_confidence_count": 0,
        }
    ]

    judge_filtered = aggregate_experiment_results(
        session,
        experiment_id=experiment.id,
        case_slug="case",
        model_config_slug="model_a",
        evaluator_source="llm_judge",
        reviewer_id="alice",
    )

    assert judge_filtered["summary"]["attempt_count"] == 1
    assert judge_filtered["summary"]["pass_rate"] == 1.0

    empty_suite = aggregate_experiment_results(
        session,
        experiment_id=experiment.id,
        suite_slug="suite_b",
    )
    assert empty_suite["summary"]["attempt_count"] == 0
    assert empty_suite["cost_quality_frontier"] == []

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        response = client.get(
            f"/monitor/experiments/{experiment.id}/analytics",
            params={
                "case_slug": "case",
                "suite_slug": "suite_a",
                "suite_split": "holdout",
                "model_config_slug": "model_a",
                "evaluator_source": "human",
                "reviewer_id": "alice",
            },
        )
    finally:
        api_module.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["filters"]["suite_slug"] == "suite_a"
    assert payload["filters"]["reviewer_id"] == "alice"
    assert payload["summary"]["pass_rate"] == 1.0
    assert [row["model_config_slug"] for row in payload["cost_quality_frontier"]] == ["model_a"]


def test_reviewer_assignment_analytics_apply_dimension_filters(session: Session) -> None:
    experiment = _experiment(session)
    for case_slug in ("case", "other"):
        _finish_attempt(_attempt(experiment, case_slug, "model_a", "system", "none"))
        _finish_attempt(_attempt(experiment, case_slug, "model_b", "system", "none"))
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="dimension-review",
        name="Dimension Review",
        random_seed=1,
        reviewer_slugs=["alice"],
    )
    session.commit()

    all_analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    case_analytics = aggregate_experiment_results(
        session,
        experiment_id=experiment.id,
        case_slug="case",
    )
    suite_analytics = aggregate_experiment_results(
        session,
        experiment_id=experiment.id,
        suite_slug="suite_a",
    )
    split_analytics = aggregate_experiment_results(
        session,
        experiment_id=experiment.id,
        suite_split="holdout",
    )

    assert all_analytics["reviewer_coverage"][0]["assigned_count"] == 2
    assert case_analytics["reviewer_coverage"][0]["assigned_count"] == 1
    assert suite_analytics["reviewer_coverage"] == []
    assert split_analytics["reviewer_coverage"] == []


def test_results_analytics_endpoint_returns_experiment_aggregation(session: Session) -> None:
    experiment = _experiment(session)
    attempt = _attempt(experiment, "case", "model_a", "system", "none")
    _finish_attempt(attempt, cost_usd=0.25)
    session.commit()

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        response = client.get(f"/monitor/experiments/{experiment.id}/analytics")
    finally:
        api_module.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["summary"]["attempt_count"] == 1
    assert response.json()["cost_quality_table"][0]["average_cost_usd"] == 0.25


def _experiment(session: Session, *, replicates: int = 1):
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="analytics", name="Analytics")
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": "analytics_exp",
                "name": "Analytics experiment",
                "cases": [
                    {"id": "case", "prompt": "case"},
                    {"id": "other", "prompt": "other"},
                ],
                "models": [
                    {"id": "model_a", "provider": "openai", "model": "a"},
                    {"id": "model_b", "provider": "anthropic", "model": "b"},
                ],
                "system_prompts": [{"id": "system", "prompt": "system"}],
                "warmers": [
                    {"id": "none", "messages": []},
                    {"id": "analyst", "messages": [{"role": "user", "content": "warm"}]},
                ],
                "design": {"replicates": replicates},
                "controls": {"local_only": True},
                "evaluation": {"evaluators": []},
            }
        ),
    )
    experiment.status = "complete"
    session.commit()
    return experiment


def _attempt(experiment, case_slug: str, model_slug: str, system_slug: str, warmer_slug: str):
    for run in experiment.runs:
        if (
            run.case_slug == case_slug
            and run.model_config_slug == model_slug
            and run.system_prompt_slug == system_slug
            and run.warmer_slug == warmer_slug
        ):
            return run.attempts[0]
    raise AssertionError("attempt not found")


def _attempts(experiment, case_slug: str, model_slug: str, system_slug: str, warmer_slug: str):
    for run in experiment.runs:
        if (
            run.case_slug == case_slug
            and run.model_config_slug == model_slug
            and run.system_prompt_slug == system_slug
            and run.warmer_slug == warmer_slug
        ):
            return sorted(run.attempts, key=lambda attempt: attempt.replicate_index)
    raise AssertionError("attempts not found")


def _finish_attempt(
    attempt,
    *,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    attempt.run.status = "complete"
    attempt.status = "succeeded"
    attempt.response_payload = {"output_text": "answer"}
    attempt.cost_usd = cost_usd
    attempt.latency_ms = latency_ms
    attempt.input_tokens = input_tokens
    attempt.output_tokens = output_tokens
    attempt.total_tokens = total_tokens


def _row(rows: list[dict], **expected):
    for row in rows:
        if all(row.get(key) == value for key, value in expected.items()):
            return row
    raise AssertionError(f"row not found: {expected}")
