from __future__ import annotations

from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass, field
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from model_eval_api.persistence.models import (
    Experiment,
    ReviewAssignment,
    ReviewSet,
    Run,
    RunAttempt,
    Score,
)
from model_eval_api.response_payloads import attempt_output_text as _attempt_output_text


TERMINAL_ATTEMPT_STATUSES = {"succeeded", "failed", "canceled"}
HUMAN_PAIRWISE_CRITERION = "blind_pairwise_preference"
HUMAN_PASS_FAIL_CRITERION = "blind_pairwise_pass_fail"
HUMAN_FAILURE_TAGS_CRITERION = "blind_pairwise_failure_tags"
HUMAN_RUBRIC_CRITERION = "blind_pairwise_rubric_notes"
JUDGE_PAIRWISE_CRITERION = "llm_judge_pairwise_preference"
JUDGE_PASS_FAIL_CRITERION = "llm_judge_pass_fail"
JUDGE_RUBRIC_CRITERION = "llm_judge_rubric"
CLAIM_DIVERGENCE_CRITERION = "divergence_claim"
CONCLUSION_DIVERGENCE_CRITERION = "divergence_conclusion"
DIVERGENCE_COMPARISON_SCOPE = "case_model_system_prompt_warmer"
JUDGE_DIVERGENCE_WARNING = (
    "Judge-backed divergence uses existing stored LLM judge scores and should be calibrated "
    "against human labels before being treated as a quality signal."
)
DETERMINISTIC_FALLBACK_WARNING = (
    "No judge-backed {signal} evidence is available; deterministic fallback uses local text "
    "heuristics only."
)
SIGNAL_TERMS = {
    "claim": ("claim", "claims", "thesis", "assertion", "assertions"),
    "conclusion": (
        "conclusion",
        "conclusions",
        "recommendation",
        "recommendations",
        "bottom line",
    ),
}
CARRYOVER_STATUSES = {"reused", "ignored", "overfit", "unknown"}
LOCAL_TEXT_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "focus",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "with",
}


@dataclass
class AttemptMetrics:
    attempt: RunAttempt
    case_slug: str
    model_config_slug: str
    system_prompt_slug: str
    warmer_slug: str
    winner_count: int = 0
    loser_count: int = 0
    tie_count: int = 0
    cannot_judge_count: int = 0
    pass_count: int = 0
    fail_count: int = 0
    failure_tags: set[str] = field(default_factory=set)
    divergence_scores: list[dict[str, Any]] = field(default_factory=list)
    metric_adapter_scores: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pairwise_decided_count(self) -> int:
        return self.winner_count + self.loser_count

    @property
    def pairwise_total_count(self) -> int:
        return self.pairwise_decided_count + self.tie_count + self.cannot_judge_count

    @property
    def pass_fail_count(self) -> int:
        return self.pass_count + self.fail_count

    @property
    def is_reliability_sample(self) -> bool:
        return self.attempt.attempt_kind == "replicate" and self.attempt.parent_attempt_id is None

    @property
    def suite_split(self) -> str:
        manifest = dict(self.attempt.run.experiment.manifest_snapshot or {})
        suite = dict(manifest.get("suite") or {})
        design = dict(self.attempt.run.experiment.design_snapshot or {})
        return str(suite.get("split") or design.get("split") or "all")

    @property
    def suite_slug(self) -> str:
        manifest = dict(self.attempt.run.experiment.manifest_snapshot or {})
        suite = dict(manifest.get("suite") or {})
        return str(suite.get("id") or suite.get("slug") or "all")


@dataclass
class AggregateBucket:
    attempt_count: int = 0
    failed_attempt_count: int = 0
    winner_count: int = 0
    loser_count: int = 0
    tie_count: int = 0
    cannot_judge_count: int = 0
    pass_count: int = 0
    fail_count: int = 0
    cost_total_usd: float = 0.0
    cost_count: int = 0
    latency_total_ms: int = 0
    latency_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_values: list[float] = field(default_factory=list)
    latency_values: list[float] = field(default_factory=list)
    total_token_values: list[float] = field(default_factory=list)
    failure_tags: Counter[str] = field(default_factory=Counter)

    def add(self, metrics: AttemptMetrics) -> None:
        attempt = metrics.attempt
        self.attempt_count += 1
        if attempt.status == "failed":
            self.failed_attempt_count += 1
        self.winner_count += metrics.winner_count
        self.loser_count += metrics.loser_count
        self.tie_count += metrics.tie_count
        self.cannot_judge_count += metrics.cannot_judge_count
        self.pass_count += metrics.pass_count
        self.fail_count += metrics.fail_count
        self.failure_tags.update(metrics.failure_tags)
        if attempt.cost_usd is not None:
            cost = float(attempt.cost_usd)
            self.cost_total_usd += cost
            self.cost_count += 1
            self.cost_values.append(cost)
        if attempt.latency_ms is not None:
            latency = int(attempt.latency_ms)
            self.latency_total_ms += latency
            self.latency_count += 1
            self.latency_values.append(float(latency))
        self.input_tokens += int(attempt.input_tokens or 0)
        self.output_tokens += int(attempt.output_tokens or 0)
        total_tokens = int(attempt.total_tokens or 0)
        self.total_tokens += total_tokens
        if attempt.total_tokens is not None:
            self.total_token_values.append(float(total_tokens))

    @property
    def pairwise_decided_count(self) -> int:
        return self.winner_count + self.loser_count

    @property
    def pass_fail_count(self) -> int:
        return self.pass_count + self.fail_count


@dataclass(frozen=True)
class AnalyticsFilters:
    case_slug: str | None = None
    suite_slug: str | None = None
    suite_split: str | None = None
    model_config_slug: str | None = None
    system_prompt_slug: str | None = None
    warmer_slug: str | None = None
    evaluator_source: str | None = None
    reviewer_id: str | None = None

    def payload(self) -> dict[str, str | None]:
        return {
            "case_slug": self.case_slug,
            "suite_slug": self.suite_slug,
            "suite_split": self.suite_split,
            "model_config_slug": self.model_config_slug,
            "system_prompt_slug": self.system_prompt_slug,
            "warmer_slug": self.warmer_slug,
            "evaluator_source": self.evaluator_source,
            "reviewer_id": self.reviewer_id,
        }


def aggregate_experiment_results(
    session: Session,
    *,
    experiment_id: int,
    case_slug: str | None = None,
    suite_slug: str | None = None,
    suite_split: str | None = None,
    model_config_slug: str | None = None,
    system_prompt_slug: str | None = None,
    warmer_slug: str | None = None,
    evaluator_source: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    filters = AnalyticsFilters(
        case_slug=case_slug,
        suite_slug=suite_slug,
        suite_split=suite_split,
        model_config_slug=model_config_slug,
        system_prompt_slug=system_prompt_slug,
        warmer_slug=warmer_slug,
        evaluator_source=evaluator_source,
        reviewer_id=reviewer_id,
    )
    attempts = _filter_attempt_metrics(
        _experiment_attempt_metrics(session, experiment_id=experiment_id, filters=filters),
        filters=filters,
    )
    assignments = _filter_review_assignments(
        _experiment_review_assignments(session, experiment_id=experiment_id, filters=filters),
        filters=filters,
    )
    summary_bucket = _bucket(attempts)
    quality_by_warmer = _quality_by_case_model_prompt_warmer(attempts)
    divergence_metrics = _divergence_metric_rows(attempts, filters=filters)
    carryover_audit = _carryover_audit_rows(attempts, filters=filters)
    warmer_lift = _warmer_lift_rows(quality_by_warmer)
    divergence_summary = _divergence_summary_rows(divergence_metrics)
    carryover_summary = _carryover_summary_rows(carryover_audit)
    judge_calibration = _judge_calibration_rows(attempts, filters=filters)
    return {
        "experiment_id": experiment_id,
        "filters": filters.payload(),
        "summary": _bucket_payload(summary_bucket),
        "failure_tag_frequency": _failure_tag_frequency(summary_bucket),
        "warmer_lift": warmer_lift,
        "context_sensitivity": _context_sensitivity_rows(quality_by_warmer),
        "divergence_placeholders": _divergence_rows(quality_by_warmer),
        "divergence_metrics": divergence_metrics,
        "divergence_summary": divergence_summary,
        "carryover_audit": carryover_audit,
        "carryover_summary": carryover_summary,
        "cost_quality_frontier": _cost_quality_frontier_rows(
            attempts,
            warmer_lift=warmer_lift,
            divergence_summary=divergence_summary,
            carryover_summary=carryover_summary,
            judge_calibration=judge_calibration,
        ),
        "cost_quality_table": _cost_quality_rows(attempts),
        "latency_quality_table": _latency_quality_rows(attempts),
        "failure_rate_table": _failure_rate_rows(attempts),
        "failure_rate_by_dimension": _failure_rate_by_dimension(attempts),
        "nondeterminism_by_dimension": _nondeterminism_by_dimension(attempts),
        "judge_calibration": judge_calibration,
        "judge_verbosity_bias": _judge_verbosity_bias_rows(attempts, filters=filters),
        "reviewer_coverage": _reviewer_coverage_rows(assignments),
        "reviewer_disagreement": _reviewer_disagreement_rows(assignments),
        "failure_taxonomy_rollup": _failure_taxonomy_rollup(attempts, filters=filters),
        "metric_adapter_scores": _metric_adapter_score_rows(attempts),
    }


def _experiment_attempt_metrics(
    session: Session, *, experiment_id: int, filters: AnalyticsFilters
) -> list[AttemptMetrics]:
    attempts = session.scalars(
        select(RunAttempt)
        .join(Run)
        .where(
            Run.experiment_id == experiment_id,
            RunAttempt.status.in_(TERMINAL_ATTEMPT_STATUSES),
        )
        .options(
            selectinload(RunAttempt.run).selectinload(Run.experiment),
            selectinload(RunAttempt.scores),
        )
        .order_by(RunAttempt.id)
    ).all()
    return [_attempt_metrics(attempt, filters=filters) for attempt in attempts]


def _experiment_review_assignments(
    session: Session, *, experiment_id: int, filters: AnalyticsFilters
) -> list[ReviewAssignment]:
    options = [
        selectinload(ReviewAssignment.review_item),
        selectinload(ReviewAssignment.review_set),
    ]
    if filters.reviewer_id is not None:
        options.append(selectinload(ReviewAssignment.reviewer))
    return session.scalars(
        select(ReviewAssignment)
        .join(ReviewSet)
        .where(ReviewSet.experiment_id == experiment_id)
        .options(*options)
        .order_by(ReviewAssignment.id)
    ).all()


def _filter_attempt_metrics(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[AttemptMetrics]:
    return [
        metrics
        for metrics in attempts
        if (filters.case_slug is None or metrics.case_slug == filters.case_slug)
        and (filters.suite_slug is None or metrics.suite_slug == filters.suite_slug)
        and (filters.suite_split is None or metrics.suite_split == filters.suite_split)
        and (
            filters.model_config_slug is None
            or metrics.model_config_slug == filters.model_config_slug
        )
        and (
            filters.system_prompt_slug is None
            or metrics.system_prompt_slug == filters.system_prompt_slug
        )
        and (filters.warmer_slug is None or metrics.warmer_slug == filters.warmer_slug)
    ]


def _filter_review_assignments(
    assignments: list[ReviewAssignment], *, filters: AnalyticsFilters
) -> list[ReviewAssignment]:
    return [
        assignment
        for assignment in assignments
        if _assignment_matches_reviewer(assignment, filters)
        and _assignment_matches_dimensions(assignment, filters)
    ]


def _score_matches_filters(score: Score, filters: AnalyticsFilters) -> bool:
    if filters.evaluator_source is not None and score.evaluator_type != filters.evaluator_source:
        return False
    if filters.reviewer_id is not None and score.evaluator_type == "human":
        value = dict(score.value or {})
        return value.get("reviewer_id") == filters.reviewer_id
    return True


def _assignment_matches_reviewer(
    assignment: ReviewAssignment, filters: AnalyticsFilters
) -> bool:
    return (
        filters.reviewer_id is None
        or assignment.reviewer is not None
        and assignment.reviewer.slug == filters.reviewer_id
    )


def _assignment_matches_dimensions(
    assignment: ReviewAssignment, filters: AnalyticsFilters
) -> bool:
    item = assignment.review_item
    if item is None:
        return True
    metadata = dict(item.metadata_json or {})
    group = dict(metadata.get("group") or {})
    answers = [
        dict(answer)
        for answer in dict(metadata.get("reveal_metadata") or {}).get("answers") or []
        if isinstance(answer, dict)
    ]
    if filters.case_slug is not None and not _review_item_value_matches(
        filters.case_slug, group.get("case_slug"), answers, "case_slug"
    ):
        return False
    if filters.model_config_slug is not None and not _review_item_value_matches(
        filters.model_config_slug, group.get("model_config_slug"), answers, "model_config_slug"
    ):
        return False
    if filters.system_prompt_slug is not None and not _review_item_value_matches(
        filters.system_prompt_slug, group.get("system_prompt_slug"), answers, "system_prompt_slug"
    ):
        return False
    if filters.warmer_slug is not None and not _review_item_value_matches(
        filters.warmer_slug, group.get("warmer_slug"), answers, "warmer_slug"
    ):
        return False
    review_set_metadata = dict((assignment.review_set.metadata_json or {}) if assignment.review_set else {})
    if filters.suite_slug is not None:
        suite = dict(review_set_metadata.get("suite") or {})
        if suite.get("id") != filters.suite_slug:
            return False
    if filters.suite_split is not None:
        suite = dict(review_set_metadata.get("suite") or {})
        if suite.get("split") != filters.suite_split:
            return False
    return True


def _review_item_value_matches(
    expected: str, group_value: Any, answers: list[dict[str, Any]], answer_key: str
) -> bool:
    return group_value == expected or any(answer.get(answer_key) == expected for answer in answers)


def _score_matches_calibration_filters(score: Score, filters: AnalyticsFilters) -> bool:
    if filters.evaluator_source not in (None, "human", "llm_judge"):
        return False
    if score.evaluator_type == "human" and filters.reviewer_id is not None:
        value = dict(score.value or {})
        return value.get("reviewer_id") == filters.reviewer_id
    return score.evaluator_type in {"human", "llm_judge"}


def _attempt_metrics(attempt: RunAttempt, *, filters: AnalyticsFilters) -> AttemptMetrics:
    run = attempt.run
    metrics = AttemptMetrics(
        attempt=attempt,
        case_slug=run.case_slug,
        model_config_slug=run.model_config_slug,
        system_prompt_slug=run.system_prompt_slug,
        warmer_slug=run.warmer_slug,
    )
    for score in attempt.scores:
        if not _score_matches_filters(score, filters):
            continue
        value = dict(score.value or {})
        if (
            score.type == "pairwise_preference"
            and score.evaluator_type == "human"
            and score.criterion == HUMAN_PAIRWISE_CRITERION
        ):
            outcome = value.get("outcome")
            if outcome == "winner":
                metrics.winner_count += 1
            elif outcome == "loser":
                metrics.loser_count += 1
            elif outcome == "tie":
                metrics.tie_count += 1
            elif outcome == "cannot_judge":
                metrics.cannot_judge_count += 1
        elif (
            score.type == "pass_fail"
            and (
                (
                    score.evaluator_type == "human"
                    and score.criterion == HUMAN_PASS_FAIL_CRITERION
                )
                or (
                    filters.evaluator_source == "llm_judge"
                    and score.evaluator_type == "llm_judge"
                )
            )
        ):
            passed = value.get("passed")
            if passed is True:
                metrics.pass_count += 1
            elif passed is False:
                metrics.fail_count += 1
        elif (
            score.type == "failure_tags"
            and score.evaluator_type == "human"
            and score.criterion == HUMAN_FAILURE_TAGS_CRITERION
        ):
            tags = value.get("tags")
            if isinstance(tags, list):
                metrics.failure_tags.update(str(tag) for tag in tags)
        elif score.type == "divergence" and score.evaluator_type == "code":
            metrics.divergence_scores.append(
                {
                    "criterion": score.criterion,
                    "metric_source": value.get("metric_source"),
                    "comparison_scope": value.get("comparison_scope"),
                    "baseline_attempt_id": value.get("baseline_attempt_id"),
                    "comparison_attempt_id": value.get("comparison_attempt_id"),
                    "value": value.get("value"),
                    "label": value.get("label"),
                    "warning": value.get("warning"),
                    "details": dict(value.get("details") or {}),
                    "confidence": score.confidence,
                    "explanation": score.explanation,
                }
            )
        elif score.evaluator_type == "metric_adapter":
            metrics.metric_adapter_scores.append(
                {
                    "criterion": score.criterion,
                    "metric_source": value.get("metric_source"),
                    "source_kind": value.get("source_kind"),
                    "score": value.get("score"),
                    "label": value.get("label"),
                    "adapter_config": dict(value.get("adapter_config") or {}),
                    "confidence": score.confidence,
                    "explanation": score.explanation,
                }
            )
    return metrics


def _bucket(metrics: list[AttemptMetrics]) -> AggregateBucket:
    bucket = AggregateBucket()
    for item in metrics:
        bucket.add(item)
    return bucket


def _bucket_payload(bucket: AggregateBucket) -> dict[str, Any]:
    return {
        "attempt_count": bucket.attempt_count,
        "failed_attempt_count": bucket.failed_attempt_count,
        "failure_rate": _ratio(bucket.failed_attempt_count, bucket.attempt_count),
        "failure_rate_interval": _rate_interval(bucket.failed_attempt_count, bucket.attempt_count),
        "winner_count": bucket.winner_count,
        "loser_count": bucket.loser_count,
        "tie_count": bucket.tie_count,
        "cannot_judge_count": bucket.cannot_judge_count,
        "win_rate": _ratio(bucket.winner_count, bucket.pairwise_decided_count),
        "win_rate_interval": _rate_interval(bucket.winner_count, bucket.pairwise_decided_count),
        "pass_count": bucket.pass_count,
        "fail_count": bucket.fail_count,
        "pass_rate": _ratio(bucket.pass_count, bucket.pass_fail_count),
        "pass_rate_interval": _rate_interval(bucket.pass_count, bucket.pass_fail_count),
        "average_cost_usd": _average(bucket.cost_total_usd, bucket.cost_count),
        "cost_usd_interval": _numeric_interval(bucket.cost_values),
        "average_latency_ms": _average(bucket.latency_total_ms, bucket.latency_count),
        "latency_ms_interval": _numeric_interval(bucket.latency_values),
        "token_totals": {
            "input_tokens": bucket.input_tokens,
            "output_tokens": bucket.output_tokens,
            "total_tokens": bucket.total_tokens,
        },
        "total_tokens_interval": _numeric_interval(bucket.total_token_values),
    }


def _failure_tag_frequency(bucket: AggregateBucket) -> list[dict[str, Any]]:
    return [
        {
            "tag": tag,
            "count": count,
            "rate": _ratio(count, bucket.attempt_count),
        }
        for tag, count in sorted(bucket.failure_tags.items(), key=lambda item: (-item[1], item[0]))
    ]


def _quality_by_case_model_prompt_warmer(
    attempts: list[AttemptMetrics],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[AttemptMetrics]] = {}
    for metrics in attempts:
        key = (
            metrics.case_slug,
            metrics.model_config_slug,
            metrics.system_prompt_slug,
            metrics.warmer_slug,
        )
        groups.setdefault(key, []).append(metrics)
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for key, group in groups.items():
        bucket = _bucket(group)
        metric, score = _quality_metric(bucket)
        result[key] = {
            "bucket": bucket,
            "metric": metric,
            "score": score,
            "failure_tags": set(bucket.failure_tags),
        }
    return result


def _quality_metric(bucket: AggregateBucket) -> tuple[str | None, float | None]:
    pass_rate = _ratio(bucket.pass_count, bucket.pass_fail_count)
    if pass_rate is not None:
        return "pass_rate", pass_rate
    win_rate = _ratio(bucket.winner_count, bucket.pairwise_decided_count)
    if win_rate is not None:
        return "win_rate", win_rate
    return None, None


def _warmer_lift_rows(
    quality_groups: dict[tuple[str, str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baselines = {
        (case_slug, model_slug, system_slug): value
        for (case_slug, model_slug, system_slug, warmer_slug), value in quality_groups.items()
        if warmer_slug == "none"
    }
    for (case_slug, model_slug, system_slug, warmer_slug), value in sorted(quality_groups.items()):
        if warmer_slug == "none":
            continue
        baseline = baselines.get((case_slug, model_slug, system_slug))
        metric = value["metric"]
        baseline_missing = baseline is None
        comparable = (
            baseline is not None
            and baseline["metric"] is not None
            and metric is not None
            and baseline["metric"] == metric
        )
        baseline_rate = baseline["score"] if baseline is not None else None
        warmer_rate = value["score"]
        rows.append(
            {
                "case_slug": case_slug,
                "model_config_slug": model_slug,
                "system_prompt_slug": system_slug,
                "warmer_slug": warmer_slug,
                "metric": metric or (baseline["metric"] if baseline is not None else None),
                "baseline_warmer_slug": "none",
                "baseline_missing": baseline_missing,
                "baseline_rate": baseline_rate,
                "warmer_rate": warmer_rate,
                "lift": warmer_rate - baseline_rate if comparable else None,
            }
        )
    return rows


def _context_sensitivity_rows(
    quality_groups: dict[tuple[str, str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[tuple[str, dict[str, Any]]]] = {}
    for (case_slug, model_slug, system_slug, warmer_slug), value in quality_groups.items():
        grouped.setdefault((case_slug, model_slug, system_slug), []).append((warmer_slug, value))
    rows: list[dict[str, Any]] = []
    for (case_slug, model_slug, system_slug), warmers in sorted(grouped.items()):
        scored = _comparable_scored_warmers(warmers)
        best = max(scored, key=lambda item: (item[1]["score"], item[0])) if scored else None
        worst = min(scored, key=lambda item: (item[1]["score"], item[0])) if scored else None
        spread = (
            best[1]["score"] - worst[1]["score"]
            if best is not None and worst is not None and len(scored) >= 2
            else None
        )
        rows.append(
            {
                "case_slug": case_slug,
                "model_config_slug": model_slug,
                "system_prompt_slug": system_slug,
                "warmer_count": len(warmers),
                "scored_warmer_count": sum(1 for _, value in warmers if value["score"] is not None),
                "metric": scored[0][1]["metric"] if scored else None,
                "best_warmer_slug": best[0] if best else None,
                "worst_warmer_slug": worst[0] if worst else None,
                "score_spread": spread,
                "label": _spread_label(spread),
            }
        )
    return rows


def _divergence_rows(
    quality_groups: dict[tuple[str, str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[tuple[str, dict[str, Any]]]] = {}
    for (case_slug, model_slug, system_slug, warmer_slug), value in quality_groups.items():
        grouped.setdefault((case_slug, model_slug, system_slug), []).append((warmer_slug, value))
    rows: list[dict[str, Any]] = []
    for (case_slug, model_slug, system_slug), warmers in sorted(grouped.items()):
        comparable = _comparable_scored_warmers(warmers)
        scores = [value["score"] for _, value in comparable]
        score_spread = max(scores) - min(scores) if len(scores) >= 2 else None
        tag_sets = [frozenset(value["failure_tags"]) for _, value in warmers]
        has_tag_spread = len(set(tag_sets)) > 1
        signals = []
        if score_spread is not None and score_spread > 0:
            signals.append("score_spread")
        if has_tag_spread:
            signals.append("failure_tag_spread")
        rows.append(
            {
                "case_slug": case_slug,
                "model_config_slug": model_slug,
                "system_prompt_slug": system_slug,
                "score_spread": score_spread,
                "failure_tag_spread": has_tag_spread,
                "signals": signals,
                "label": _divergence_label(score_spread, has_tag_spread),
                "semantic_diff_available": False,
            }
        )
    return rows


def _divergence_metric_rows(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metrics in attempts:
        for score in metrics.divergence_scores:
            rows.append(
                {
                    "case_slug": metrics.case_slug,
                    "model_config_slug": metrics.model_config_slug,
                    "system_prompt_slug": metrics.system_prompt_slug,
                    "warmer_slug": metrics.warmer_slug,
                    **score,
                }
            )
    rows.extend(_failure_mode_spread_rows(attempts))
    rows.extend(_claim_conclusion_divergence_rows(attempts, filters=filters))
    annotated = [_annotated_divergence_row(row, sample_count=1) for row in rows]
    return sorted(
        annotated,
        key=lambda row: (
            row["case_slug"],
            row["model_config_slug"],
            row["system_prompt_slug"],
            row["warmer_slug"],
            row["criterion"],
            str(row.get("comparison_attempt_id") or ""),
        ),
    )


def _metric_adapter_score_rows(attempts: list[AttemptMetrics]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metrics in attempts:
        for score in metrics.metric_adapter_scores:
            adapter_config = dict(score.get("adapter_config") or {})
            metric_source = str(score.get("metric_source") or "unknown")
            rows.append(
                {
                    "attempt_id": metrics.attempt.attempt_id,
                    "case_slug": metrics.case_slug,
                    "model_config_slug": metrics.model_config_slug,
                    "system_prompt_slug": metrics.system_prompt_slug,
                    "warmer_slug": metrics.warmer_slug,
                    "adapter_config_slug": str(adapter_config.get("id") or ""),
                    "adapter_config_version": adapter_config.get("version"),
                    "criterion": score["criterion"],
                    "metric_source": metric_source,
                    "source_kind": score.get("source_kind") or _analytics_source_kind(metric_source),
                    "score": _float_or_none(score.get("score")),
                    "label": score.get("label"),
                    "explanation": score.get("explanation"),
                    "confidence": score.get("confidence"),
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            str(row["adapter_config_slug"]),
            str(row["criterion"]),
            str(row["case_slug"]),
            str(row["model_config_slug"]),
            str(row["system_prompt_slug"]),
            str(row["warmer_slug"]),
            str(row["attempt_id"]),
        ),
    )


def _annotated_divergence_row(
    row: dict[str, Any], *, sample_count: int
) -> dict[str, Any]:
    metric_source = str(row.get("metric_source") or "unknown")
    source_kind = _analytics_source_kind(metric_source)
    warning = _analytics_warning(source_kind, row.get("warning"), row_type="divergence")
    return {
        **row,
        "metric_source": metric_source,
        "source_kind": source_kind,
        "warning": warning,
        "warning_label": _analytics_warning_label(source_kind, warning),
        "sample_count": sample_count,
    }


def _divergence_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["case_slug"]),
            str(row["model_config_slug"]),
            str(row["system_prompt_slug"]),
            str(row["warmer_slug"]),
            str(row["criterion"]),
            str(row.get("metric_source") or "unknown"),
            str(row.get("source_kind") or _analytics_source_kind(row.get("metric_source"))),
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for (
        case_slug,
        model_slug,
        system_slug,
        warmer_slug,
        criterion,
        metric_source,
        source_kind,
    ), group in sorted(grouped.items()):
        value = _mean_value(row.get("value") for row in group)
        warning = _first_text(row.get("warning") for row in group)
        summary_rows.append(
            {
                "case_slug": case_slug,
                "model_config_slug": model_slug,
                "system_prompt_slug": system_slug,
                "warmer_slug": warmer_slug,
                "criterion": criterion,
                "metric_source": metric_source,
                "source_kind": source_kind,
                "value": value,
                "label": _summary_divergence_label(value, group),
                "warning": warning,
                "warning_label": _analytics_warning_label(source_kind, warning),
                "sample_count": len(group),
                "confidence": _mean_value(row.get("confidence") for row in group),
            }
        )
    return summary_rows


def _failure_mode_spread_rows(attempts: list[AttemptMetrics]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[AttemptMetrics]] = {}
    for metrics in attempts:
        key = (metrics.case_slug, metrics.model_config_slug, metrics.system_prompt_slug)
        grouped.setdefault(key, []).append(metrics)

    rows: list[dict[str, Any]] = []
    for (case_slug, model_slug, system_slug), group in sorted(grouped.items()):
        baselines = [metrics for metrics in group if metrics.warmer_slug == "none"]
        comparisons = [metrics for metrics in group if metrics.warmer_slug != "none"]
        for comparison in sorted(comparisons, key=lambda item: (item.warmer_slug, item.attempt.id)):
            baseline = _matching_baseline_metrics(baselines, comparison)
            baseline_tags = set(baseline.failure_tags) if baseline is not None else set()
            comparison_tags = set(comparison.failure_tags)
            if baseline is None:
                value = None
                label = "unavailable"
                warning = "No no-warmer baseline is available for failure-mode spread."
            elif not baseline_tags or not comparison_tags:
                value = None
                label = "unavailable"
                warning = (
                    "Human failure tags must be available on both baseline and comparison "
                    "attempts for failure-mode spread."
                )
            else:
                value = round(1 - _jaccard(baseline_tags, comparison_tags), 4)
                label = _metric_divergence_label(value)
                warning = (
                    "Failure-mode spread is based on available human failure tags, not "
                    "semantic judging."
                )
            rows.append(
                {
                    "case_slug": case_slug,
                    "model_config_slug": model_slug,
                    "system_prompt_slug": system_slug,
                    "warmer_slug": comparison.warmer_slug,
                    "criterion": "divergence_failure_mode_spread",
                    "metric_source": "human_failure_tags",
                    "comparison_scope": "case_model_system_prompt_warmer",
                    "baseline_attempt_id": baseline.attempt.attempt_id if baseline else None,
                    "comparison_attempt_id": comparison.attempt.attempt_id,
                    "value": value,
                    "label": label,
                    "warning": warning,
                    "details": {
                        "baseline_tags": sorted(baseline_tags),
                        "comparison_tags": sorted(comparison_tags),
                        "shared_tags": sorted(baseline_tags & comparison_tags),
                        "added_tags": sorted(comparison_tags - baseline_tags),
                        "removed_tags": sorted(baseline_tags - comparison_tags),
                    },
                    "confidence": 1.0 if value is not None else 0.0,
                    "explanation": warning,
                }
            )
    return rows


def _claim_conclusion_divergence_rows(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[AttemptMetrics]] = {}
    for metrics in attempts:
        key = (metrics.case_slug, metrics.model_config_slug, metrics.system_prompt_slug)
        grouped.setdefault(key, []).append(metrics)

    rows: list[dict[str, Any]] = []
    for group in grouped.values():
        baselines = [metrics for metrics in group if metrics.warmer_slug == "none"]
        comparisons = [metrics for metrics in group if metrics.warmer_slug != "none"]
        for comparison in sorted(comparisons, key=lambda item: (item.warmer_slug, item.attempt.id)):
            baseline = _matching_baseline_metrics(baselines, comparison)
            for signal, criterion in (
                ("claim", CLAIM_DIVERGENCE_CRITERION),
                ("conclusion", CONCLUSION_DIVERGENCE_CRITERION),
            ):
                rows.append(
                    _claim_conclusion_divergence_row(
                        baseline,
                        comparison,
                        filters=filters,
                        signal=signal,
                        criterion=criterion,
                    )
                )
    return rows


def _claim_conclusion_divergence_row(
    baseline: AttemptMetrics | None,
    comparison: AttemptMetrics,
    *,
    filters: AnalyticsFilters,
    signal: str,
    criterion: str,
) -> dict[str, Any]:
    baseline_evidence, comparison_evidence = _matched_judge_signal_evidence(
        baseline,
        comparison,
        filters,
        signal,
    )
    if baseline_evidence is not None and comparison_evidence is not None:
        max_score = max(abs(baseline_evidence["score"]), abs(comparison_evidence["score"]), 1.0)
        value = round(abs(comparison_evidence["score"] - baseline_evidence["score"]) / max_score, 4)
        warning = JUDGE_DIVERGENCE_WARNING
        return _divergence_analytics_row(
            baseline,
            comparison,
            criterion=criterion,
            metric_source=str(comparison_evidence["metric_source"]),
            value=value,
            label=_metric_divergence_label(value),
            warning=warning,
            details={
                "baseline": baseline_evidence,
                "comparison": comparison_evidence,
            },
            confidence=_average_confidence(
                baseline_evidence.get("confidence"), comparison_evidence.get("confidence")
            ),
            explanation=(
                f"Compared stored judge-backed {signal} evidence against the no-warmer baseline."
            ),
        )
    return _deterministic_signal_fallback_row(
        baseline,
        comparison,
        signal=signal,
        criterion=criterion,
    )


def _matched_judge_signal_evidence(
    baseline: AttemptMetrics | None,
    comparison: AttemptMetrics,
    filters: AnalyticsFilters,
    signal: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    baseline_candidates = _judge_signal_evidence_candidates(baseline, signal, filters=filters)
    comparison_candidates = _judge_signal_evidence_candidates(comparison, signal, filters=filters)
    matches = [
        (baseline_candidate, comparison_candidate)
        for baseline_candidate in baseline_candidates
        for comparison_candidate in comparison_candidates
        if _compatible_judge_evidence(baseline_candidate, comparison_candidate)
    ]
    if not matches:
        return None, None
    return max(
        matches,
        key=lambda pair: (
            _confidence_sort_value(pair[0]),
            _confidence_sort_value(pair[1]),
            int(pair[0]["score_id"] or 0),
            int(pair[1]["score_id"] or 0),
        ),
    )


def _judge_signal_evidence_candidates(
    metrics: AttemptMetrics | None, signal: str, *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    if metrics is None:
        return []
    candidates: list[dict[str, Any]] = []
    for score in metrics.attempt.scores:
        if not _score_matches_filters(score, filters):
            continue
        if score.evaluator_type != "llm_judge":
            continue
        value = dict(score.value or {})
        if (
            score.type == "rubric_score"
            and score.criterion == JUDGE_RUBRIC_CRITERION
            and _dimension_matches_signal(value.get("dimension"), signal)
            and isinstance(value.get("score"), int | float)
        ):
            candidates.append(
                {
                    "metric_source": "llm_judge_rubric",
                    "score": float(value["score"]),
                    "dimension": str(value.get("dimension")),
                    "evaluator_id": value.get("evaluator_id"),
                    "judge_execution_id": value.get("judge_execution_id"),
                    "comparison_id": value.get("comparison_id"),
                    "score_id": score.id,
                    "confidence": score.confidence,
                }
            )
        structured_score = _structured_signal_score(value.get("structured_output"), signal)
        if structured_score is not None:
            candidates.append(
                {
                    "metric_source": "llm_judge_structured_output",
                    "score": structured_score,
                    "dimension": f"{signal}_score",
                    "evaluator_id": value.get("evaluator_id"),
                    "judge_execution_id": value.get("judge_execution_id"),
                    "comparison_id": value.get("comparison_id"),
                    "score_id": score.id,
                    "confidence": score.confidence,
                }
            )
    return candidates


def _compatible_judge_evidence(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("metric_source") != right.get("metric_source"):
        return False
    if left.get("dimension") != right.get("dimension"):
        return False
    for key in ("evaluator_id", "judge_execution_id", "comparison_id"):
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value is not None and right_value is not None and left_value != right_value:
            return False
    return True


def _confidence_sort_value(evidence: dict[str, Any]) -> tuple[bool, float]:
    confidence = evidence.get("confidence")
    return (isinstance(confidence, int | float), float(confidence or 0))


def _dimension_matches_signal(dimension: Any, signal: str) -> bool:
    if not isinstance(dimension, str):
        return False
    tokens = _dimension_tokens(dimension)
    return any(_term_tokens_match(tokens, term) for term in SIGNAL_TERMS[signal])


def _dimension_tokens(dimension: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9]+", "_", dimension.lower()).strip("_")
    return [token for token in normalized.split("_") if token]


def _term_tokens_match(tokens: list[str], term: str) -> bool:
    term_tokens = _dimension_tokens(term)
    if not term_tokens:
        return False
    if len(term_tokens) == 1:
        return term_tokens[0] in tokens
    width = len(term_tokens)
    return any(
        tokens[index : index + width] == term_tokens
        for index in range(len(tokens) - width + 1)
    )


def _structured_signal_score(value: Any, signal: str) -> float | None:
    if not isinstance(value, dict):
        return None
    candidate_keys = (
        f"{signal}_score",
        f"{signal}_quality",
        f"{signal}_support",
        signal,
    )
    for key in candidate_keys:
        candidate = value.get(key)
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int | float):
            return float(candidate)
        if isinstance(candidate, dict):
            score = candidate.get("score")
            if isinstance(score, bool):
                continue
            if isinstance(score, int | float):
                return float(score)
    return None


def _deterministic_signal_fallback_row(
    baseline: AttemptMetrics | None,
    comparison: AttemptMetrics,
    *,
    signal: str,
    criterion: str,
) -> dict[str, Any]:
    if baseline is None:
        warning = f"No no-warmer baseline is available for {signal} divergence."
        return _divergence_analytics_row(
            baseline,
            comparison,
            criterion=criterion,
            metric_source="deterministic_fallback",
            value=None,
            label="unavailable",
            warning=warning,
            details={"reason": warning},
            confidence=0.0,
            explanation=warning,
        )
    baseline_text = _signal_text(_attempt_output_text(baseline.attempt), signal)
    comparison_text = _signal_text(_attempt_output_text(comparison.attempt), signal)
    if not baseline_text.strip() or not comparison_text.strip():
        warning = f"Missing comparable local text for {signal} divergence."
        return _divergence_analytics_row(
            baseline,
            comparison,
            criterion=criterion,
            metric_source="deterministic_fallback",
            value=None,
            label="unavailable",
            warning=warning,
            details={"reason": warning},
            confidence=0.0,
            explanation=warning,
        )
    baseline_tokens = _local_text_tokens(baseline_text)
    comparison_tokens = _local_text_tokens(comparison_text)
    if not baseline_tokens or not comparison_tokens:
        warning = f"Local text did not contain enough comparable {signal} terms."
        return _divergence_analytics_row(
            baseline,
            comparison,
            criterion=criterion,
            metric_source="deterministic_fallback",
            value=None,
            label="unavailable",
            warning=warning,
            details={"reason": warning},
            confidence=0.0,
            explanation=warning,
        )
    value = round(1 - _jaccard(baseline_tokens, comparison_tokens), 4)
    warning = DETERMINISTIC_FALLBACK_WARNING.format(signal=signal)
    return _divergence_analytics_row(
        baseline,
        comparison,
        criterion=criterion,
        metric_source="deterministic_fallback",
        value=value,
        label=_metric_divergence_label(value),
        warning=warning,
        details={
            "baseline_terms": sorted(baseline_tokens),
            "comparison_terms": sorted(comparison_tokens),
            "shared_terms": sorted(baseline_tokens & comparison_tokens),
            "baseline_text": baseline_text,
            "comparison_text": comparison_text,
        },
        confidence=0.35,
        explanation=f"Compared local {signal} text with deterministic fallback heuristics.",
    )


def _divergence_analytics_row(
    baseline: AttemptMetrics | None,
    comparison: AttemptMetrics,
    *,
    criterion: str,
    metric_source: str,
    value: float | None,
    label: str,
    warning: str,
    details: dict[str, Any],
    confidence: float | None,
    explanation: str,
) -> dict[str, Any]:
    return {
        "case_slug": comparison.case_slug,
        "model_config_slug": comparison.model_config_slug,
        "system_prompt_slug": comparison.system_prompt_slug,
        "warmer_slug": comparison.warmer_slug,
        "criterion": criterion,
        "metric_source": metric_source,
        "comparison_scope": DIVERGENCE_COMPARISON_SCOPE,
        "baseline_attempt_id": baseline.attempt.attempt_id if baseline is not None else None,
        "comparison_attempt_id": comparison.attempt.attempt_id,
        "value": value,
        "label": label,
        "warning": warning,
        "details": details,
        "confidence": confidence,
        "explanation": explanation,
    }


def _carryover_audit_rows(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metrics in attempts:
        if metrics.warmer_slug == "none":
            continue
        structured = _structured_carryover_evidence(metrics, filters=filters)
        if structured is not None:
            rows.append(_carryover_audit_row(metrics, **structured))
        else:
            rows.append(_local_carryover_audit_row(metrics))
    return sorted(
        rows,
        key=lambda row: (
            row["case_slug"],
            row["model_config_slug"],
            row["system_prompt_slug"],
            row["warmer_slug"],
            str(row["comparison_attempt_id"]),
        ),
    )


def _carryover_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["case_slug"]),
            str(row["model_config_slug"]),
            str(row["system_prompt_slug"]),
            str(row["warmer_slug"]),
            str(row["source_evidence"]),
            str(row.get("source_kind") or _analytics_source_kind(row.get("source_evidence"))),
            str(row["status"]),
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for (
        case_slug,
        model_slug,
        system_slug,
        warmer_slug,
        source_evidence,
        source_kind,
        status,
    ), group in sorted(grouped.items()):
        warning = _first_text(row.get("warning") for row in group)
        summary_rows.append(
            {
                "case_slug": case_slug,
                "model_config_slug": model_slug,
                "system_prompt_slug": system_slug,
                "warmer_slug": warmer_slug,
                "source_evidence": source_evidence,
                "source_kind": source_kind,
                "status": status,
                "warning": warning,
                "warning_label": _analytics_warning_label(source_kind, warning),
                "sample_count": len(group),
                "confidence": _mean_value(row.get("confidence") for row in group),
            }
        )
    return summary_rows


def _structured_carryover_evidence(
    metrics: AttemptMetrics, *, filters: AnalyticsFilters
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for score in metrics.attempt.scores:
        if not _score_matches_filters(score, filters):
            continue
        if score.evaluator_type != "llm_judge":
            continue
        value = dict(score.value or {})
        structured = value.get("structured_output")
        if not isinstance(structured, dict):
            continue
        carryover = structured.get("carryover")
        if isinstance(carryover, dict):
            status = str(carryover.get("status") or "unknown").strip().lower()
            if status not in CARRYOVER_STATUSES:
                status = "unknown"
            explanation = str(carryover.get("explanation") or carryover.get("evidence") or "")
            candidates.append(
                {
                    "status": status,
                    "source_evidence": "structured_judge_output",
                    "explanation": explanation,
                    "details": {
                        "evidence": carryover.get("evidence"),
                        "evaluator_id": value.get("evaluator_id"),
                        "score_id": score.id,
                    },
                    "confidence": score.confidence,
                }
            )
        status_value = structured.get("carryover_status")
        if isinstance(status_value, str):
            status = status_value.strip().lower()
            if status not in CARRYOVER_STATUSES:
                status = "unknown"
            candidates.append(
                {
                    "status": status,
                    "source_evidence": "structured_judge_output",
                    "explanation": str(structured.get("carryover_explanation") or ""),
                    "details": {
                        "evidence": structured.get("carryover_evidence"),
                        "evaluator_id": value.get("evaluator_id"),
                        "score_id": score.id,
                    },
                    "confidence": score.confidence,
                }
            )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            _confidence_sort_value(item),
            int(item["details"].get("score_id") or 0),
        ),
    )


def _local_carryover_audit_row(metrics: AttemptMetrics) -> dict[str, Any]:
    warmer_text = _warmer_text(metrics.attempt)
    output_text = _attempt_output_text(metrics.attempt)
    warmer_terms = _local_text_tokens(warmer_text)
    output_terms = _local_text_tokens(output_text)
    matched = warmer_terms & output_terms
    if not warmer_terms or not output_terms:
        status = "unknown"
        explanation = "No comparable warmer or output text is available for carryover audit."
    elif len(matched) == 0:
        status = "ignored"
        explanation = "No local warmer terms were found in the output."
    elif len(matched) >= max(1, len(warmer_terms) - 1) and len(output_terms) <= len(matched) + 1:
        status = "overfit"
        explanation = "Output is dominated by local warmer terms."
    else:
        status = "reused"
        explanation = "Output reuses locally matched warmer terms."
    overlap_rate = _ratio(len(matched), len(warmer_terms))
    return _carryover_audit_row(
        metrics,
        status=status,
        source_evidence="local_warmer_overlap",
        explanation=explanation,
        details={
            "matched_warmer_terms": sorted(matched),
            "warmer_terms": sorted(warmer_terms),
            "output_terms": sorted(output_terms),
            "overlap_rate": overlap_rate,
        },
        confidence=0.4 if status != "unknown" else 0.0,
    )


def _carryover_audit_row(
    metrics: AttemptMetrics,
    *,
    status: str,
    source_evidence: str,
    explanation: str,
    details: dict[str, Any],
    confidence: float | None,
) -> dict[str, Any]:
    source_kind = _analytics_source_kind(source_evidence)
    warning = _analytics_warning(source_kind, None, row_type="carryover")
    return {
        "case_slug": metrics.case_slug,
        "model_config_slug": metrics.model_config_slug,
        "system_prompt_slug": metrics.system_prompt_slug,
        "warmer_slug": metrics.warmer_slug,
        "comparison_attempt_id": metrics.attempt.attempt_id,
        "source_evidence": source_evidence,
        "source_kind": source_kind,
        "status": status,
        "explanation": explanation,
        "warning": warning,
        "warning_label": _analytics_warning_label(source_kind, warning),
        "sample_count": 1,
        "details": details,
        "confidence": confidence,
    }


def _signal_text(text: str, signal: str) -> str:
    if not text.strip():
        return ""
    labels = SIGNAL_TERMS[signal]
    label_pattern = "|".join(re.escape(label) for label in labels)
    all_labels = "|".join(re.escape(label) for terms in SIGNAL_TERMS.values() for label in terms)
    pattern = re.compile(
        rf"(?is)\b(?:{label_pattern})\s*:\s*(.*?)(?=\b(?:{all_labels})\s*:|$)"
    )
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _warmer_text(attempt: RunAttempt) -> str:
    warmer = dict((attempt.run.run_snapshot or {}).get("warmer") or {})
    parts = [
        str(message.get("content"))
        for message in warmer.get("messages") or []
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    ]
    if isinstance(warmer.get("intent"), str):
        parts.append(str(warmer["intent"]))
    return "\n".join(parts)


def _local_text_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())
        if token not in LOCAL_TEXT_STOP_WORDS and len(token) > 2
    }


def _average_confidence(left: Any, right: Any) -> float | None:
    values = [value for value in (left, right) if isinstance(value, int | float)]
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)


def _mean_value(values: Any) -> float | None:
    numeric = [float(value) for value in values if isinstance(value, int | float)]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 4)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _first_text(values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _summary_divergence_label(value: float | None, rows: list[dict[str, Any]]) -> str:
    if value is not None:
        return _metric_divergence_label(value)
    labels = {str(row.get("label")) for row in rows if row.get("label")}
    if len(labels) == 1:
        return labels.pop()
    if labels:
        return "mixed"
    return "unavailable"


def _analytics_source_kind(source: Any) -> str:
    source_text = str(source or "").lower()
    if source_text.startswith("llm_judge") or source_text == "structured_judge_output":
        return "judge_backed"
    if source_text.startswith("human"):
        return "human_backed"
    if source_text.startswith("deterministic") or source_text.startswith("local"):
        return "deterministic_heuristic"
    return "unknown"


def _analytics_warning(
    source_kind: str,
    warning: Any,
    *,
    row_type: str,
) -> str | None:
    if isinstance(warning, str) and warning.strip():
        return warning.strip()
    if source_kind == "deterministic_heuristic":
        if row_type == "carryover":
            return "Carryover audit uses local warmer/output token overlap only."
        return "This divergence row is based on deterministic text heuristics, not calibrated judge or human labels."
    if source_kind == "judge_backed":
        if row_type == "carryover":
            return (
                "Judge-backed carryover audit uses existing stored LLM judge output and should "
                "be calibrated against human labels before being treated as a quality signal."
            )
        return JUDGE_DIVERGENCE_WARNING
    return None


def _analytics_warning_label(source_kind: str, warning: str | None) -> str:
    if source_kind == "deterministic_heuristic":
        return "heuristic"
    if source_kind == "judge_backed":
        return "judge_needs_calibration"
    if source_kind == "human_backed":
        return "human_labeled"
    return "none" if warning is None else "review"


def _matching_baseline_metrics(
    baselines: list[AttemptMetrics], comparison: AttemptMetrics
) -> AttemptMetrics | None:
    same_replicate = [
        baseline
        for baseline in baselines
        if baseline.attempt.replicate_index == comparison.attempt.replicate_index
    ]
    if not same_replicate:
        return None
    return max(same_replicate, key=lambda baseline: baseline.attempt.id or 0)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _comparable_scored_warmers(
    warmers: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    scored = [(warmer_slug, value) for warmer_slug, value in warmers if value["score"] is not None]
    metrics = {value["metric"] for _, value in scored}
    if len(metrics) != 1:
        return []
    return scored


def _cost_quality_rows(attempts: list[AttemptMetrics]) -> list[dict[str, Any]]:
    return _quality_table_rows(attempts, value_name="average_cost_usd")


def _latency_quality_rows(attempts: list[AttemptMetrics]) -> list[dict[str, Any]]:
    return _quality_table_rows(attempts, value_name="average_latency_ms")


def _quality_table_rows(attempts: list[AttemptMetrics], *, value_name: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[AttemptMetrics]] = {}
    for metrics in attempts:
        key = (metrics.model_config_slug, metrics.system_prompt_slug, metrics.warmer_slug)
        groups.setdefault(key, []).append(metrics)
    rows = []
    for (model_slug, system_slug, warmer_slug), group in sorted(groups.items()):
        bucket = _bucket(group)
        payload = _bucket_payload(bucket)
        quality_metric, quality_rate = _quality_metric(bucket)
        row = {
            "model_config_slug": model_slug,
            "system_prompt_slug": system_slug,
            "warmer_slug": warmer_slug,
            "attempt_count": bucket.attempt_count,
            "win_rate": payload["win_rate"],
            "pass_rate": payload["pass_rate"],
            "failure_rate": payload["failure_rate"],
            "average_cost_usd": payload["average_cost_usd"],
            "average_latency_ms": payload["average_latency_ms"],
            "token_totals": payload["token_totals"],
        }
        row["quality_metric"] = quality_metric
        row["quality_rate"] = quality_rate
        if value_name == "average_cost_usd":
            row["cost_usd_per_quality_point"] = _cost_per_quality_point(
                row["average_cost_usd"], row["quality_rate"]
            )
        rows.append(row)
    return rows


def _cost_quality_frontier_rows(
    attempts: list[AttemptMetrics],
    *,
    warmer_lift: list[dict[str, Any]],
    divergence_summary: list[dict[str, Any]],
    carryover_summary: list[dict[str, Any]],
    judge_calibration: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str, str], list[AttemptMetrics]] = {}
    for metrics in attempts:
        key = (
            metrics.case_slug,
            metrics.suite_slug,
            metrics.suite_split,
            metrics.model_config_slug,
            metrics.system_prompt_slug,
            metrics.warmer_slug,
        )
        groups.setdefault(key, []).append(metrics)

    experiment = attempts[0].attempt.run.experiment if attempts else None
    warmer_lift_by_key = {_case_model_system_warmer_key(row): row for row in warmer_lift}
    divergence_by_key = _rows_by_case_model_system_warmer(divergence_summary)
    carryover_by_key = _rows_by_case_model_system_warmer(carryover_summary)
    overlays = _judge_calibration_overlays(judge_calibration)
    rows: list[dict[str, Any]] = []
    for (case_slug, suite_slug, suite_split, model_slug, system_slug, warmer_slug), group in sorted(
        groups.items()
    ):
        bucket = _bucket(group)
        payload = _bucket_payload(bucket)
        quality_metric, quality_rate = _quality_metric(bucket)
        quality_interval = _quality_interval(payload, quality_metric)
        key = (case_slug, model_slug, system_slug, warmer_slug)
        row = {
            "frontier_key": _frontier_key(
                case_slug,
                suite_slug,
                suite_split,
                model_slug,
                system_slug,
                warmer_slug,
            ),
            "case_slug": case_slug,
            "suite_slug": suite_slug,
            "suite_split": suite_split,
            "model_config_slug": model_slug,
            "system_prompt_slug": system_slug,
            "warmer_slug": warmer_slug,
            "attempt_count": bucket.attempt_count,
            "failed_attempt_count": bucket.failed_attempt_count,
            "quality_metric": quality_metric,
            "quality_rate": quality_rate,
            "quality_interval": quality_interval,
            "quality_uncertainty_label": quality_interval.get("label"),
            "average_cost_usd": payload["average_cost_usd"],
            "cost_usd_interval": payload["cost_usd_interval"],
            "cost_uncertainty_label": payload["cost_usd_interval"].get("label"),
            "average_latency_ms": payload["average_latency_ms"],
            "latency_ms_interval": payload["latency_ms_interval"],
            "latency_uncertainty_label": payload["latency_ms_interval"].get("label"),
            "token_totals": payload["token_totals"],
            "total_tokens_interval": payload["total_tokens_interval"],
            "warmer_lift": warmer_lift_by_key.get(key),
            "divergence_summary": divergence_by_key.get(key, []),
            "carryover_summary": carryover_by_key.get(key, []),
            "judge_calibration_overlays": overlays,
            "dominated_by": None,
            "is_frontier": False,
        }
        row.update(
            _promptfoo_frontier_fields(
                experiment,
                case_slug=case_slug,
                model_slug=model_slug,
                system_slug=system_slug,
            )
        )
        row["dominance_status"] = _frontier_missing_status(row) or "candidate"
        rows.append(row)
    _mark_frontier_rows(rows)
    return rows


def _quality_interval(payload: dict[str, Any], quality_metric: str | None) -> dict[str, Any]:
    if quality_metric == "pass_rate":
        return dict(payload["pass_rate_interval"])
    if quality_metric == "win_rate":
        return dict(payload["win_rate_interval"])
    return _rate_interval(0, 0)


def _frontier_missing_status(row: dict[str, Any]) -> str | None:
    if row["quality_rate"] is None:
        return "missing_quality"
    if row["average_cost_usd"] is None:
        return "missing_cost"
    if row["average_latency_ms"] is None:
        return "missing_latency"
    return None


def _mark_frontier_rows(rows: list[dict[str, Any]]) -> None:
    candidates = [row for row in rows if row["dominance_status"] == "candidate"]
    for row in candidates:
        dominator = next(
            (
                candidate
                for candidate in candidates
                if candidate is not row and _frontier_dominates(candidate, row)
            ),
            None,
        )
        if dominator is None:
            row["dominance_status"] = "frontier"
            row["is_frontier"] = True
        else:
            row["dominance_status"] = "dominated"
            row["dominated_by"] = dominator["frontier_key"]


def _frontier_dominates(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    if (
        candidate["case_slug"] != row["case_slug"]
        or candidate["suite_slug"] != row["suite_slug"]
        or candidate["suite_split"] != row["suite_split"]
    ):
        return False
    if candidate["quality_metric"] != row["quality_metric"]:
        return False
    candidate_quality = candidate["quality_rate"]
    row_quality = row["quality_rate"]
    candidate_cost = candidate["average_cost_usd"]
    row_cost = row["average_cost_usd"]
    candidate_latency = candidate["average_latency_ms"]
    row_latency = row["average_latency_ms"]
    if None in (
        candidate_quality,
        row_quality,
        candidate_cost,
        row_cost,
        candidate_latency,
        row_latency,
    ):
        return False
    return (
        candidate_quality >= row_quality
        and candidate_cost <= row_cost
        and candidate_latency <= row_latency
        and (
            candidate_quality > row_quality
            or candidate_cost < row_cost
            or candidate_latency < row_latency
        )
    )


def _frontier_key(
    case_slug: str,
    suite_slug: str,
    suite_split: str,
    model_slug: str,
    system_slug: str,
    warmer_slug: str,
) -> str:
    return "|".join([case_slug, suite_slug, suite_split, model_slug, system_slug, warmer_slug])


def _case_model_system_warmer_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["case_slug"]),
        str(row["model_config_slug"]),
        str(row["system_prompt_slug"]),
        str(row["warmer_slug"]),
    )


def _rows_by_case_model_system_warmer(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_case_model_system_warmer_key(row), []).append(row)
    return grouped


def _judge_calibration_overlays(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evaluator_id": row["evaluator_id"],
            "comparison_count": row["comparison_count"],
            "agreement_rate": row["agreement_rate"],
            "low_confidence_count": row["low_confidence_count"],
        }
        for row in rows
    ]


def _promptfoo_frontier_fields(
    experiment: Experiment | None,
    *,
    case_slug: str,
    model_slug: str,
    system_slug: str,
) -> dict[str, Any]:
    if experiment is None:
        return {
            "promptfoo_provider_id": None,
            "promptfoo_prompt_id": system_slug,
            "promptfoo_test_description": _title_from_slug(case_slug),
            "promptfoo_assertion_types": [],
        }
    model_snapshot = dict((experiment.model_config_snapshots or {}).get(model_slug) or {})
    raw_params = dict(model_snapshot.get("raw_provider_params") or {})
    provider_id = raw_params.get("promptfoo_provider_id")
    provider = model_snapshot.get("provider")
    model = model_snapshot.get("model")
    if provider_id is None and provider and model:
        provider_id = f"{provider}:{model}"
    case_snapshot = dict((experiment.case_snapshots or {}).get(case_slug) or {})
    case_name = case_snapshot.get("name")
    return {
        "promptfoo_provider_id": str(provider_id) if provider_id is not None else model_slug,
        "promptfoo_prompt_id": system_slug,
        "promptfoo_test_description": (
            str(case_name)
            if isinstance(case_name, str) and case_name and case_name != case_slug
            else _title_from_slug(case_slug)
        ),
        "promptfoo_assertion_types": _promptfoo_assertion_types(
            experiment.evaluator_snapshots or {}
        ),
    }


def _promptfoo_assertion_types(snapshots: dict[str, dict[str, Any]]) -> list[str]:
    assertion_types: list[str] = []
    for _slug, snapshot in sorted(snapshots.items(), key=_promptfoo_assertion_sort_key):
        definition = dict(snapshot.get("definition") or {})
        assertion_type = definition.get("promptfoo_assertion_type")
        if isinstance(assertion_type, str) and assertion_type:
            assertion_types.append(assertion_type)
    return assertion_types


def _promptfoo_assertion_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
    slug, snapshot = item
    definition = dict(snapshot.get("definition") or {})
    priority = {"no_empty_output": 0, "json_schema": 1}.get(str(definition.get("kind") or ""), 9)
    return priority, slug


def _title_from_slug(value: str) -> str:
    return re.sub(r"[_-]+", " ", value).strip().title() or value


def _failure_rate_rows(attempts: list[AttemptMetrics]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[AttemptMetrics]] = {}
    for metrics in attempts:
        key = (
            metrics.case_slug,
            metrics.model_config_slug,
            metrics.system_prompt_slug,
            metrics.warmer_slug,
        )
        groups.setdefault(key, []).append(metrics)
    return [
        {
            "case_slug": case_slug,
            "model_config_slug": model_slug,
            "system_prompt_slug": system_slug,
            "warmer_slug": warmer_slug,
            "attempt_count": bucket.attempt_count,
            "failed_attempt_count": bucket.failed_attempt_count,
            "failure_rate": _ratio(bucket.failed_attempt_count, bucket.attempt_count),
        }
        for (case_slug, model_slug, system_slug, warmer_slug), bucket in (
            (key, _bucket(group)) for key, group in sorted(groups.items())
        )
    ]


def _failure_rate_by_dimension(attempts: list[AttemptMetrics]) -> dict[str, list[dict[str, Any]]]:
    return {
        "model_config_slug": _dimension_failure_rows(
            attempts, key_name="model_config_slug", key_fn=lambda item: item.model_config_slug
        ),
        "system_prompt_slug": _dimension_failure_rows(
            attempts, key_name="system_prompt_slug", key_fn=lambda item: item.system_prompt_slug
        ),
        "warmer_slug": _dimension_failure_rows(
            attempts, key_name="warmer_slug", key_fn=lambda item: item.warmer_slug
        ),
        "case_slug": _dimension_failure_rows(
            attempts, key_name="case_slug", key_fn=lambda item: item.case_slug
        ),
    }


def _nondeterminism_by_dimension(attempts: list[AttemptMetrics]) -> dict[str, list[dict[str, Any]]]:
    return {
        "case_slug": _dimension_uncertainty_rows(
            attempts, key_name="case_slug", key_fn=lambda item: item.case_slug
        ),
        "model_config_slug": _dimension_uncertainty_rows(
            attempts, key_name="model_config_slug", key_fn=lambda item: item.model_config_slug
        ),
        "system_prompt_slug": _dimension_uncertainty_rows(
            attempts, key_name="system_prompt_slug", key_fn=lambda item: item.system_prompt_slug
        ),
        "warmer_slug": _dimension_uncertainty_rows(
            attempts, key_name="warmer_slug", key_fn=lambda item: item.warmer_slug
        ),
        "suite_split": _dimension_uncertainty_rows(
            attempts, key_name="suite_split", key_fn=lambda item: item.suite_split
        ),
    }


def _dimension_failure_rows(
    attempts: list[AttemptMetrics],
    *,
    key_name: str,
    key_fn: Callable[[AttemptMetrics], str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[AttemptMetrics]] = {}
    for metrics in attempts:
        groups.setdefault(key_fn(metrics), []).append(metrics)
    return [
        {
            key_name: slug,
            "attempt_count": bucket.attempt_count,
            "failed_attempt_count": bucket.failed_attempt_count,
            "failure_rate": _ratio(bucket.failed_attempt_count, bucket.attempt_count),
        }
        for slug, bucket in ((key, _bucket(group)) for key, group in sorted(groups.items()))
    ]


def _dimension_uncertainty_rows(
    attempts: list[AttemptMetrics],
    *,
    key_name: str,
    key_fn: Callable[[AttemptMetrics], str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[AttemptMetrics]] = {}
    retry_counts: Counter[str] = Counter()
    for metrics in attempts:
        key = key_fn(metrics)
        if metrics.is_reliability_sample:
            grouped.setdefault(key, []).append(metrics)
        elif metrics.attempt.attempt_kind == "retry" or metrics.attempt.parent_attempt_id is not None:
            retry_counts[key] += 1
    keys = sorted(set(grouped) | set(retry_counts))
    rows = []
    for key in keys:
        bucket = _bucket(grouped.get(key, []))
        rows.append(
            {
                key_name: key,
                "sample_count": bucket.attempt_count,
                "retry_attempt_count": retry_counts[key],
                "failure_rate_interval": _rate_interval(
                    bucket.failed_attempt_count, bucket.attempt_count
                ),
                "pass_rate_interval": _rate_interval(bucket.pass_count, bucket.pass_fail_count),
                "win_rate_interval": _rate_interval(
                    bucket.winner_count, bucket.pairwise_decided_count
                ),
                "cost_usd_interval": _numeric_interval(bucket.cost_values),
                "latency_ms_interval": _numeric_interval(bucket.latency_values),
                "total_tokens_interval": _numeric_interval(bucket.total_token_values),
            }
        )
    return rows


def _reviewer_coverage_rows(assignments: list[ReviewAssignment]) -> list[dict[str, Any]]:
    grouped: dict[int, list[ReviewAssignment]] = {}
    for assignment in assignments:
        grouped.setdefault(assignment.review_set_id, []).append(assignment)
    rows: list[dict[str, Any]] = []
    for review_set_id, values in sorted(grouped.items()):
        submitted = [assignment for assignment in values if assignment.status == "submitted"]
        rows.append(
            {
                "review_set_id": review_set_id,
                "assigned_count": len(values),
                "submitted_count": len(submitted),
                "pending_count": len(values) - len(submitted),
                "reviewer_count": len({assignment.reviewer_id for assignment in values}),
                "coverage_rate": _ratio(len(submitted), len(values)),
            }
        )
    return rows


def _reviewer_disagreement_rows(assignments: list[ReviewAssignment]) -> list[dict[str, Any]]:
    grouped: dict[int, list[ReviewAssignment]] = {}
    for assignment in assignments:
        if assignment.status != "submitted":
            continue
        grouped.setdefault(assignment.review_item_id, []).append(assignment)
    rows: list[dict[str, Any]] = []
    for review_item_id, values in sorted(grouped.items()):
        if len(values) < 2:
            continue
        decisions = [dict(assignment.decision_snapshot or {}) for assignment in values]
        pass_fail_disagreement_count = _label_disagreement_count(
            [dict(decision.get("pass_fail") or {}) for decision in decisions]
        )
        failure_tag_disagreement_count = _label_disagreement_count(
            [
                {
                    label: tuple(sorted(tags))
                    for label, tags in dict(decision.get("failure_tags") or {}).items()
                }
                for decision in decisions
            ]
        )
        rows.append(
            {
                "review_item_id": review_item_id,
                "review_set_id": values[0].review_set_id,
                "reviewer_count": len({assignment.reviewer_id for assignment in values}),
                "pairwise_disagreement": len({decision.get("winner") for decision in decisions}) > 1,
                "pass_fail_disagreement_count": pass_fail_disagreement_count,
                "failure_tag_disagreement_count": failure_tag_disagreement_count,
            }
        )
    return rows


def _label_disagreement_count(values: list[dict[str, Any]]) -> int:
    labels = sorted({label for value in values for label in value})
    count = 0
    for label in labels:
        observed = {jsonable for jsonable in (value.get(label) for value in values)}
        if len(observed) > 1:
            count += 1
    return count


def _failure_taxonomy_rollup(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, int | None]] = Counter()
    for metrics in attempts:
        for score in metrics.attempt.scores:
            if not _score_matches_filters(score, filters):
                continue
            if (
                score.type != "failure_tags"
                or score.evaluator_type != "human"
                or score.criterion != HUMAN_FAILURE_TAGS_CRITERION
            ):
                continue
            value = dict(score.value or {})
            taxonomy_version = value.get("taxonomy_version")
            if not isinstance(taxonomy_version, int):
                taxonomy_version = None
            tags = value.get("tags")
            if isinstance(tags, list):
                counts.update((str(tag), taxonomy_version) for tag in tags)
    return [
        {
            "tag": tag,
            "taxonomy_version": taxonomy_version,
            "count": count,
        }
        for (tag, taxonomy_version), count in sorted(
            counts.items(), key=lambda item: (item[0][0], item[0][1] or 0)
        )
    ]


def _judge_calibration_rows(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    human_pairwise: dict[int, list[str]] = {}
    human_pass_fail: dict[int, list[bool]] = {}
    human_rubric_attempts: set[int] = set()
    judge_pairwise: dict[str, list[tuple[int, str, float | None]]] = {}
    judge_pass_fail: dict[str, list[tuple[int, bool, float | None]]] = {}
    judge_rubric: dict[str, dict[int, list[float | None]]] = {}
    for metrics in attempts:
        for score in metrics.attempt.scores:
            if not _score_matches_calibration_filters(score, filters):
                continue
            value = dict(score.value or {})
            if (
                score.type == "pairwise_preference"
                and score.evaluator_type == "human"
                and score.criterion == HUMAN_PAIRWISE_CRITERION
            ):
                outcome = value.get("outcome")
                if outcome in {"winner", "loser"}:
                    human_pairwise.setdefault(metrics.attempt.id, []).append(str(outcome))
            elif (
                score.type == "pass_fail"
                and score.evaluator_type == "human"
                and score.criterion == HUMAN_PASS_FAIL_CRITERION
            ):
                passed = value.get("passed")
                if isinstance(passed, bool):
                    human_pass_fail.setdefault(metrics.attempt.id, []).append(passed)
            elif (
                score.type == "rubric_notes"
                and score.evaluator_type == "human"
                and score.criterion == HUMAN_RUBRIC_CRITERION
            ):
                human_rubric_attempts.add(metrics.attempt.id)
            elif (
                score.type == "pairwise_preference"
                and score.evaluator_type == "llm_judge"
                and score.criterion == JUDGE_PAIRWISE_CRITERION
            ):
                outcome = value.get("outcome")
                evaluator_id = value.get("evaluator_id")
                if outcome in {"winner", "loser"} and isinstance(evaluator_id, str):
                    judge_pairwise.setdefault(evaluator_id, []).append(
                        (metrics.attempt.id, str(outcome), score.confidence)
                    )
            elif (
                score.type == "pass_fail"
                and score.evaluator_type == "llm_judge"
                and score.criterion == JUDGE_PASS_FAIL_CRITERION
            ):
                passed = value.get("passed")
                evaluator_id = value.get("evaluator_id")
                if isinstance(passed, bool) and isinstance(evaluator_id, str):
                    judge_pass_fail.setdefault(evaluator_id, []).append(
                        (metrics.attempt.id, passed, score.confidence)
                    )
            elif (
                score.type == "rubric_score"
                and score.evaluator_type == "llm_judge"
                and score.criterion == JUDGE_RUBRIC_CRITERION
            ):
                evaluator_id = value.get("evaluator_id")
                if isinstance(evaluator_id, str):
                    judge_rubric.setdefault(evaluator_id, {}).setdefault(
                        metrics.attempt.id, []
                    ).append(score.confidence)
    rows: list[dict[str, Any]] = []
    evaluator_ids = sorted(
        set(judge_pairwise) | set(judge_pass_fail) | set(judge_rubric)
    )
    for evaluator_id in evaluator_ids:
        pairwise_comparable = [
            (attempt_id, outcome, confidence, human_outcome)
            for attempt_id, outcome, confidence in judge_pairwise.get(evaluator_id, [])
            if attempt_id in human_pairwise
            for human_outcome in human_pairwise[attempt_id]
        ]
        pass_fail_comparable = [
            (attempt_id, passed, confidence, human_passed)
            for attempt_id, passed, confidence in judge_pass_fail.get(evaluator_id, [])
            if attempt_id in human_pass_fail
            for human_passed in human_pass_fail[attempt_id]
        ]
        rubric_comparable = {
            attempt_id: confidences
            for attempt_id, confidences in judge_rubric.get(evaluator_id, {}).items()
            if attempt_id in human_rubric_attempts
        }
        pairwise_agreement_count = sum(
            1
            for _, outcome, _, human_outcome in pairwise_comparable
            if human_outcome == outcome
        )
        pass_fail_agreement_count = sum(
            1
            for _, passed, _, human_passed in pass_fail_comparable
            if human_passed == passed
        )
        rubric_agreement_count = 0
        agreement_comparison_count = len(pairwise_comparable) + len(pass_fail_comparable)
        comparison_count = agreement_comparison_count
        agreement_count = pairwise_agreement_count + pass_fail_agreement_count
        rows.append(
            {
                "evaluator_id": evaluator_id,
                "comparison_count": comparison_count,
                "pairwise_comparison_count": len(pairwise_comparable),
                "pairwise_agreement_count": pairwise_agreement_count,
                "pass_fail_comparison_count": len(pass_fail_comparable),
                "pass_fail_agreement_count": pass_fail_agreement_count,
                "rubric_comparison_count": len(rubric_comparable),
                "rubric_agreement_count": rubric_agreement_count,
                "agreement_count": agreement_count,
                "disagreement_count": agreement_comparison_count - agreement_count,
                "agreement_rate": _ratio(agreement_count, agreement_comparison_count),
                "low_confidence_count": sum(
                    1
                    for _, _, confidence, _ in [*pairwise_comparable, *pass_fail_comparable]
                    if confidence is not None and confidence < 0.5
                )
                + sum(
                    1
                    for confidences in rubric_comparable.values()
                    if any(confidence is not None and confidence < 0.5 for confidence in confidences)
                ),
            }
        )
    return rows


def _judge_verbosity_bias_rows(
    attempts: list[AttemptMetrics], *, filters: AnalyticsFilters
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for metrics in attempts:
        for score in metrics.attempt.scores:
            if not _score_matches_filters(score, filters):
                continue
            value = dict(score.value or {})
            if (
                score.type != "pairwise_preference"
                or score.evaluator_type != "llm_judge"
                or score.criterion != JUDGE_PAIRWISE_CRITERION
            ):
                continue
            evaluator_id = value.get("evaluator_id")
            comparison_id = value.get("comparison_id")
            if not isinstance(evaluator_id, str) or not isinstance(comparison_id, str):
                continue
            grouped.setdefault(evaluator_id, {}).setdefault(comparison_id, []).append(value)
    rows: list[dict[str, Any]] = []
    for evaluator_id, comparisons in sorted(grouped.items()):
        winner_tokens: list[int] = []
        loser_tokens: list[int] = []
        longer_wins = 0
        comparable_count = 0
        for values in comparisons.values():
            winner = next((value for value in values if value.get("outcome") == "winner"), None)
            losers = [value for value in values if value.get("outcome") == "loser"]
            if winner is None or not losers:
                continue
            winner_token_count = int(winner.get("answer_token_count") or 0)
            loser_token_count = max(int(value.get("answer_token_count") or 0) for value in losers)
            winner_tokens.append(winner_token_count)
            loser_tokens.append(loser_token_count)
            if winner_token_count != loser_token_count:
                comparable_count += 1
                if winner_token_count > loser_token_count:
                    longer_wins += 1
        rows.append(
            {
                "evaluator_id": evaluator_id,
                "comparison_count": len(comparisons),
                "length_comparable_count": comparable_count,
                "longer_answer_win_count": longer_wins,
                "longer_answer_win_rate": _ratio(longer_wins, comparable_count),
                "winner_average_tokens": _average(sum(winner_tokens), len(winner_tokens)),
                "loser_average_tokens": _average(sum(loser_tokens), len(loser_tokens)),
            }
        )
    return rows


def _spread_label(spread: float | None) -> str:
    if spread is None:
        return "insufficient_data"
    if spread >= 0.5:
        return "high"
    if spread >= 0.2:
        return "medium"
    return "low"


def _divergence_label(score_spread: float | None, has_tag_spread: bool) -> str:
    if score_spread is None and not has_tag_spread:
        return "insufficient_data"
    if (score_spread is not None and score_spread >= 0.5) or has_tag_spread:
        return "high"
    if score_spread is not None and score_spread >= 0.2:
        return "medium"
    return "low"


def _metric_divergence_label(value: float | None) -> str:
    if value is None:
        return "unavailable"
    if value >= 0.5:
        return "high"
    if value >= 0.2:
        return "medium"
    return "low"


def _cost_per_quality_point(
    average_cost_usd: float | None, quality_rate: float | None
) -> float | None:
    if average_cost_usd is None or quality_rate in (None, 0):
        return None
    return average_cost_usd / quality_rate


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _average(total: float | int, count: int) -> float | None:
    if count == 0:
        return None
    return total / count


def _rate_interval(success_count: int, sample_count: int) -> dict[str, Any]:
    rate = _ratio(success_count, sample_count)
    if sample_count == 0:
        return {
            "sample_count": 0,
            "rate": None,
            "lower": None,
            "upper": None,
            "label": "no_samples",
        }
    if sample_count == 1:
        return {
            "sample_count": 1,
            "rate": rate,
            "lower": rate,
            "upper": rate,
            "label": "single_sample",
        }
    z = 1.96
    denominator = 1 + (z * z / sample_count)
    center = (float(rate or 0) + (z * z / (2 * sample_count))) / denominator
    margin = (
        z
        * math.sqrt(
            ((float(rate or 0) * (1 - float(rate or 0))) / sample_count)
            + ((z * z) / (4 * sample_count * sample_count))
        )
        / denominator
    )
    return {
        "sample_count": sample_count,
        "rate": rate,
        "lower": max(0.0, center - margin),
        "upper": min(1.0, center + margin),
        "label": _uncertainty_label(sample_count),
    }


def _numeric_interval(values: list[float]) -> dict[str, Any]:
    sample_count = len(values)
    if sample_count == 0:
        return {
            "sample_count": 0,
            "mean": None,
            "variance": None,
            "lower": None,
            "upper": None,
            "label": "no_samples",
        }
    mean = sum(values) / sample_count
    if sample_count == 1:
        return {
            "sample_count": 1,
            "mean": mean,
            "variance": 0.0,
            "lower": mean,
            "upper": mean,
            "label": "single_sample",
        }
    variance = sum((value - mean) ** 2 for value in values) / (sample_count - 1)
    margin = 1.96 * math.sqrt(variance / sample_count)
    return {
        "sample_count": sample_count,
        "mean": mean,
        "variance": variance,
        "lower": mean - margin,
        "upper": mean + margin,
        "label": _uncertainty_label(sample_count),
    }


def _uncertainty_label(sample_count: int) -> str:
    if sample_count == 0:
        return "no_samples"
    if sample_count == 1:
        return "single_sample"
    if sample_count < 30:
        return "low_sample"
    return "stable_sample"
