from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from model_eval_api.execution_states import AttemptStatus
from model_eval_api.persistence.models import Experiment, Run, RunAttempt, Score
from model_eval_api.persistence.repositories import record_score
from model_eval_api.response_payloads import attempt_output_text as _attempt_output_text


@dataclass(frozen=True)
class DeterministicScore:
    type: str
    criterion: str
    value: dict[str, Any]
    explanation: str
    confidence: float


class DeterministicEvaluator(Protocol):
    kind: str

    def evaluate(
        self, attempt: RunAttempt, snapshot: dict[str, Any], definition: dict[str, Any]
    ) -> DeterministicScore:
        ...


DEFAULT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "investment_memo_required_sections_v1": {
        "kind": "required_sections",
        "criterion": "investment_memo_required_sections",
        "required_sections": ["thesis", "variant view", "risks", "watch items"],
    },
    "investment_memo_token_budget_v1": {
        "kind": "token_budget",
        "criterion": "investment_memo_token_budget",
        "max_output_tokens": 1200,
    },
    "json_schema_v1": {
        "kind": "json_schema",
        "criterion": "json_schema",
        "schema": {"type": "object"},
    },
    "citation_required_v1": {
        "kind": "citation_required",
        "criterion": "citation_required",
    },
    "hallucinated_numbers_check_v1": {
        "kind": "citation_required",
        "criterion": "citation_required",
    },
    "no_empty_output_v1": {
        "kind": "no_empty_output",
        "criterion": "no_empty_output",
    },
}

DIVERGENCE_METRIC_SOURCES = {
    "divergence_semantic_overlap": "deterministic_semantic_overlap",
    "divergence_section_structure": "deterministic_section_structure",
    "divergence_token_length": "deterministic_token_length",
    "divergence_confidence_language": "deterministic_confidence_language",
}
DIVERGENCE_ATTEMPT_STATUSES = {
    AttemptStatus.SUCCEEDED.value,
    AttemptStatus.FAILED.value,
    AttemptStatus.CANCELED.value,
}
DIVERGENCE_COMPARISON_SCOPE = "case_model_system_prompt_warmer"
SEMANTIC_HEURISTIC_WARNING = (
    "Deterministic lexical/keyphrase overlap is an uncalibrated heuristic, not semantic judging."
)
SECTION_HEURISTIC_WARNING = (
    "Deterministic section-structure divergence uses markdown headings and required-order "
    "heuristics only."
)
TOKEN_LENGTH_WARNING = "Deterministic token-length divergence uses local output text only."
CONFIDENCE_LANGUAGE_WARNING = (
    "Deterministic confidence-language divergence uses local marker heuristics only."
)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}
HEDGING_MARKERS = (
    "appears",
    "could",
    "likely",
    "may",
    "might",
    "possible",
    "roughly",
    "suggests",
    "uncertain",
)
CERTAINTY_MARKERS = (
    "definitely",
    "certainly",
    "guaranteed",
    "must",
    "will",
    "always",
    "never",
    "no doubt",
)
CONFIDENCE_MARKERS = (
    "confidence",
    "conviction",
    "probability",
    "risk",
    "low confidence",
    "medium confidence",
    "high confidence",
)


class RequiredSectionEvaluator:
    kind = "required_sections"

    def evaluate(
        self, attempt: RunAttempt, snapshot: dict[str, Any], definition: dict[str, Any]
    ) -> DeterministicScore:
        text = _attempt_output_text(attempt)
        sections = _string_list(definition.get("sections") or definition.get("required_sections"))
        present = [section for section in sections if _contains_section(text, section)]
        missing = [section for section in sections if section not in present]
        passed = not missing
        criterion = _criterion(snapshot, definition, "required_sections")
        explanation = (
            "Found all required sections."
            if passed
            else f"Missing required sections: {', '.join(missing)}."
        )
        return DeterministicScore(
            type="pass_fail",
            criterion=criterion,
            value={
                "passed": passed,
                "matched_sections": present,
                "missing_sections": missing,
                "required_sections": sections,
            },
            explanation=explanation,
            confidence=1.0,
        )


class TokenBudgetEvaluator:
    kind = "token_budget"

    def evaluate(
        self, attempt: RunAttempt, snapshot: dict[str, Any], definition: dict[str, Any]
    ) -> DeterministicScore:
        max_tokens = _int_or_none(
            definition.get("max_output_tokens")
            or definition.get("max_tokens")
            or definition.get("output_token_budget")
        )
        output_tokens = attempt.output_tokens
        if output_tokens is None:
            output_tokens = _estimated_output_tokens(_attempt_output_text(attempt))
        if max_tokens is None:
            return DeterministicScore(
                type="configuration_error",
                criterion=_criterion(snapshot, definition, "token_budget"),
                value={
                    "passed": None,
                    "output_tokens": output_tokens,
                    "max_output_tokens": None,
                    "error": "missing_max_output_tokens",
                },
                explanation="Token budget evaluator has no max output token budget configured.",
                confidence=0.0,
            )
        passed = output_tokens <= max_tokens
        criterion = _criterion(snapshot, definition, "token_budget")
        explanation = (
            f"Output used {output_tokens} output tokens within the {max_tokens} token budget."
            if passed
            else f"Output used {output_tokens} output tokens over the {max_tokens} token budget."
        )
        return DeterministicScore(
            type="pass_fail",
            criterion=criterion,
            value={
                "passed": passed,
                "output_tokens": output_tokens,
                "max_output_tokens": max_tokens,
            },
            explanation=explanation,
            confidence=1.0 if attempt.output_tokens is not None else 0.6,
        )


class JsonSchemaEvaluator:
    kind = "json_schema"

    def evaluate(
        self, attempt: RunAttempt, snapshot: dict[str, Any], definition: dict[str, Any]
    ) -> DeterministicScore:
        criterion = _criterion(snapshot, definition, "json_schema")
        text = _attempt_output_text(attempt).strip()
        schema = definition.get("schema") if isinstance(definition.get("schema"), dict) else {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            return DeterministicScore(
                type="pass_fail",
                criterion=criterion,
                value={"passed": False, "errors": [f"Invalid JSON: {error.msg}."]},
                explanation=f"Output is not valid JSON: {error.msg}.",
                confidence=1.0,
            )
        errors = _schema_errors(parsed, schema, path="$")
        return DeterministicScore(
            type="pass_fail",
            criterion=criterion,
            value={"passed": not errors, "errors": errors},
            explanation="Output matches the configured JSON schema."
            if not errors
            else f"Output failed JSON schema validation: {'; '.join(errors)}.",
            confidence=1.0,
        )


class CitationRequiredEvaluator:
    kind = "citation_required"

    def evaluate(
        self, attempt: RunAttempt, snapshot: dict[str, Any], definition: dict[str, Any]
    ) -> DeterministicScore:
        criterion = _criterion(snapshot, definition, "citation_required")
        return DeterministicScore(
            type="placeholder",
            criterion=criterion,
            value={"status": "not_implemented", "passed": None},
            explanation="Citation-required deterministic checks are defined as a placeholder.",
            confidence=0.0,
        )


class NoEmptyOutputEvaluator:
    kind = "no_empty_output"

    def evaluate(
        self, attempt: RunAttempt, snapshot: dict[str, Any], definition: dict[str, Any]
    ) -> DeterministicScore:
        criterion = _criterion(snapshot, definition, "no_empty_output")
        output_length = len(_attempt_output_text(attempt).strip())
        passed = output_length > 0
        return DeterministicScore(
            type="pass_fail",
            criterion=criterion,
            value={"passed": passed, "output_characters": output_length},
            explanation="Output is non-empty." if passed else "Output is empty.",
            confidence=1.0,
        )


EVALUATORS: dict[str, DeterministicEvaluator] = {
    evaluator.kind: evaluator
    for evaluator in (
        RequiredSectionEvaluator(),
        TokenBudgetEvaluator(),
        JsonSchemaEvaluator(),
        CitationRequiredEvaluator(),
        NoEmptyOutputEvaluator(),
    )
}


def evaluate_attempt(
    attempt: RunAttempt, evaluator_snapshot: dict[str, Any]
) -> list[DeterministicScore]:
    snapshot_type = str(evaluator_snapshot.get("type") or "").lower()
    definition = _definition_for(evaluator_snapshot)
    if snapshot_type and snapshot_type not in {"deterministic", "code", *EVALUATORS.keys()}:
        return []
    kind = _normalized_kind(definition, snapshot_type=snapshot_type)
    evaluator = EVALUATORS.get(kind)
    if evaluator is None:
        return []
    return [evaluator.evaluate(attempt, evaluator_snapshot, definition)]


def run_deterministic_evaluators(session: Session, experiment_id: int) -> dict[str, int]:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment {experiment_id} was not found.")
    attempts = session.scalars(
        select(RunAttempt)
        .join(Run)
        .where(Run.experiment_id == experiment.id, RunAttempt.status == AttemptStatus.SUCCEEDED.value)
        .order_by(RunAttempt.id)
    ).all()
    scores_recorded = 0
    for attempt in attempts:
        scores_recorded += record_deterministic_scores_for_attempt(session, attempt)
    scores_recorded += record_deterministic_divergence_scores(session, experiment_id=experiment.id)
    return {
        "experiment_id": experiment.id,
        "attempts_evaluated": len(attempts),
        "scores_recorded": scores_recorded,
    }


def record_deterministic_scores_for_attempt(session: Session, attempt: RunAttempt) -> int:
    if attempt.status != AttemptStatus.SUCCEEDED.value:
        return 0
    if (attempt.response_payload or {}).get("dry_run") is True:
        return 0
    snapshots = attempt.run.experiment.evaluator_snapshots or {}
    recorded = 0
    for snapshot in snapshots.values():
        for result in evaluate_attempt(attempt, snapshot):
            version = _version(snapshot)
            result = _with_evaluator_id(result, snapshot)
            if _score_exists(session, attempt, result, version):
                continue
            record_score(
                session,
                run_attempt=attempt,
                type=result.type,
                evaluator_type="code",
                criterion=result.criterion,
                value=result.value,
                explanation=result.explanation,
                confidence=result.confidence,
                evaluator_version=version,
            )
            recorded += 1
    return recorded


def record_deterministic_divergence_scores(session: Session, *, experiment_id: int) -> int:
    attempts = session.scalars(
        select(RunAttempt)
        .join(Run)
        .where(
            Run.experiment_id == experiment_id,
            RunAttempt.status.in_(DIVERGENCE_ATTEMPT_STATUSES),
        )
        .options(selectinload(RunAttempt.run))
        .order_by(RunAttempt.id)
    ).all()
    grouped: dict[tuple[str, str, str], list[RunAttempt]] = {}
    for attempt in attempts:
        if _is_provider_dry_run(attempt):
            continue
        key = (
            attempt.run.case_slug,
            attempt.run.model_config_slug,
            attempt.run.system_prompt_slug,
        )
        grouped.setdefault(key, []).append(attempt)

    recorded = 0
    for group in grouped.values():
        baselines = [attempt for attempt in group if attempt.run.warmer_slug == "none"]
        comparisons = [attempt for attempt in group if attempt.run.warmer_slug != "none"]
        for comparison in comparisons:
            baseline = _matching_baseline(baselines, comparison)
            for result in _divergence_scores_for_comparison(baseline, comparison):
                if _divergence_score_exists(session, comparison, result):
                    continue
                record_score(
                    session,
                    run_attempt=comparison,
                    type=result.type,
                    evaluator_type="code",
                    criterion=result.criterion,
                    value=result.value,
                    explanation=result.explanation,
                    confidence=result.confidence,
                )
                recorded += 1
    return recorded


def _matching_baseline(baselines: list[RunAttempt], comparison: RunAttempt) -> RunAttempt | None:
    same_replicate = [
        baseline
        for baseline in baselines
        if baseline.replicate_index == comparison.replicate_index
    ]
    if not same_replicate:
        return None
    valid_baselines = [
        baseline for baseline in same_replicate if _attempt_output_text(baseline).strip()
    ]
    return max(valid_baselines or same_replicate, key=lambda baseline: baseline.id or 0)


def _divergence_scores_for_comparison(
    baseline: RunAttempt | None, comparison: RunAttempt
) -> list[DeterministicScore]:
    if baseline is None:
        return [
            _unavailable_divergence_score(
                criterion=criterion,
                metric_source=metric_source,
                baseline=baseline,
                comparison=comparison,
                warning="no no-warmer baseline is available for this case/model/system prompt.",
            )
            for criterion, metric_source in DIVERGENCE_METRIC_SOURCES.items()
        ]
    return [
        _semantic_overlap_divergence(baseline, comparison),
        _section_structure_divergence(baseline, comparison),
        _token_length_divergence(baseline, comparison),
        _confidence_language_divergence(baseline, comparison),
    ]


def _semantic_overlap_divergence(
    baseline: RunAttempt, comparison: RunAttempt
) -> DeterministicScore:
    baseline_text = _attempt_output_text(baseline)
    comparison_text = _attempt_output_text(comparison)
    unavailable = _missing_text_warning(baseline_text, comparison_text)
    if unavailable:
        return _unavailable_divergence_score(
            criterion="divergence_semantic_overlap",
            metric_source="deterministic_semantic_overlap",
            baseline=baseline,
            comparison=comparison,
            warning=unavailable,
        )
    baseline_tokens = _semantic_tokens(baseline_text)
    comparison_tokens = _semantic_tokens(comparison_text)
    if not baseline_tokens or not comparison_tokens:
        return _unavailable_divergence_score(
            criterion="divergence_semantic_overlap",
            metric_source="deterministic_semantic_overlap",
            baseline=baseline,
            comparison=comparison,
            warning="Output text did not contain enough comparable lexical tokens.",
        )
    token_overlap = _jaccard(baseline_tokens, comparison_tokens)
    baseline_phrases = _keyphrases(baseline_text)
    comparison_phrases = _keyphrases(comparison_text)
    phrase_overlap = (
        _jaccard(baseline_phrases, comparison_phrases)
        if baseline_phrases and comparison_phrases
        else None
    )
    overlap = (
        (token_overlap + phrase_overlap) / 2
        if phrase_overlap is not None
        else token_overlap
    )
    value = round(1 - overlap, 4)
    payload = _divergence_payload(
        metric_source="deterministic_semantic_overlap",
        baseline=baseline,
        comparison=comparison,
        value=value,
        label=_divergence_value_label(value),
        warning=SEMANTIC_HEURISTIC_WARNING,
        details={
            "token_overlap": round(token_overlap, 4),
            "keyphrase_overlap": round(phrase_overlap, 4) if phrase_overlap is not None else None,
            "shared_terms": sorted(baseline_tokens & comparison_tokens)[:20],
            "baseline_only_terms": sorted(baseline_tokens - comparison_tokens)[:20],
            "comparison_only_terms": sorted(comparison_tokens - baseline_tokens)[:20],
        },
    )
    return DeterministicScore(
        type="divergence",
        criterion="divergence_semantic_overlap",
        value=payload,
        explanation="Compared local lexical and keyphrase overlap against the no-warmer baseline.",
        confidence=0.45,
    )


def _section_structure_divergence(
    baseline: RunAttempt, comparison: RunAttempt
) -> DeterministicScore:
    baseline_text = _attempt_output_text(baseline)
    comparison_text = _attempt_output_text(comparison)
    unavailable = _missing_text_warning(baseline_text, comparison_text)
    if unavailable:
        return _unavailable_divergence_score(
            criterion="divergence_section_structure",
            metric_source="deterministic_section_structure",
            baseline=baseline,
            comparison=comparison,
            warning=unavailable,
        )
    baseline_headings = _markdown_headings(baseline_text)
    comparison_headings = _markdown_headings(comparison_text)
    if not baseline_headings and not comparison_headings:
        return _unavailable_divergence_score(
            criterion="divergence_section_structure",
            metric_source="deterministic_section_structure",
            baseline=baseline,
            comparison=comparison,
            warning="Neither output contained markdown headings for section-structure analysis.",
        )
    baseline_set = set(baseline_headings)
    comparison_set = set(comparison_headings)
    missing = sorted(baseline_set - comparison_set)
    added = sorted(comparison_set - baseline_set)
    order_changed = _section_order_changed(baseline_headings, comparison_headings)
    denominator = max(1, len(baseline_set | comparison_set) + 1)
    value = round((len(missing) + len(added) + int(order_changed)) / denominator, 4)
    payload = _divergence_payload(
        metric_source="deterministic_section_structure",
        baseline=baseline,
        comparison=comparison,
        value=value,
        label=_divergence_value_label(value),
        warning=SECTION_HEURISTIC_WARNING,
        details={
            "baseline_headings": baseline_headings,
            "comparison_headings": comparison_headings,
            "missing_from_comparison": missing,
            "added_in_comparison": added,
            "shared_heading_order_changed": order_changed,
        },
    )
    return DeterministicScore(
        type="divergence",
        criterion="divergence_section_structure",
        value=payload,
        explanation="Compared markdown heading presence and order against the no-warmer baseline.",
        confidence=0.75,
    )


def _token_length_divergence(baseline: RunAttempt, comparison: RunAttempt) -> DeterministicScore:
    baseline_text = _attempt_output_text(baseline)
    comparison_text = _attempt_output_text(comparison)
    unavailable = _missing_text_warning(baseline_text, comparison_text)
    if unavailable:
        return _unavailable_divergence_score(
            criterion="divergence_token_length",
            metric_source="deterministic_token_length",
            baseline=baseline,
            comparison=comparison,
            warning=unavailable,
        )
    baseline_tokens = _estimated_output_tokens(baseline_text)
    comparison_tokens = _estimated_output_tokens(comparison_text)
    if baseline_tokens <= 0:
        return _unavailable_divergence_score(
            criterion="divergence_token_length",
            metric_source="deterministic_token_length",
            baseline=baseline,
            comparison=comparison,
            warning="Baseline output has no measurable token length.",
        )
    delta_tokens = comparison_tokens - baseline_tokens
    value = round(abs(delta_tokens) / baseline_tokens, 4)
    payload = _divergence_payload(
        metric_source="deterministic_token_length",
        baseline=baseline,
        comparison=comparison,
        value=value,
        label=_divergence_value_label(value),
        warning=TOKEN_LENGTH_WARNING,
        details={
            "baseline_tokens": baseline_tokens,
            "comparison_tokens": comparison_tokens,
            "delta_tokens": delta_tokens,
        },
    )
    return DeterministicScore(
        type="divergence",
        criterion="divergence_token_length",
        value=payload,
        explanation="Compared local output token length against the no-warmer baseline.",
        confidence=0.65,
    )


def _confidence_language_divergence(
    baseline: RunAttempt, comparison: RunAttempt
) -> DeterministicScore:
    baseline_text = _attempt_output_text(baseline)
    comparison_text = _attempt_output_text(comparison)
    unavailable = _missing_text_warning(baseline_text, comparison_text)
    if unavailable:
        return _unavailable_divergence_score(
            criterion="divergence_confidence_language",
            metric_source="deterministic_confidence_language",
            baseline=baseline,
            comparison=comparison,
            warning=unavailable,
        )
    baseline_profile = _confidence_profile(baseline_text)
    comparison_profile = _confidence_profile(comparison_text)
    marker_delta = abs(
        comparison_profile["confidence_marker_count"] - baseline_profile["confidence_marker_count"]
    )
    hedging_delta = abs(comparison_profile["hedging_count"] - baseline_profile["hedging_count"])
    certainty_delta = abs(
        comparison_profile["unsupported_certainty_count"]
        - baseline_profile["unsupported_certainty_count"]
    )
    missing_delta = int(
        comparison_profile["missing_confidence_language"]
        != baseline_profile["missing_confidence_language"]
    )
    value = round(min(1.0, (marker_delta + hedging_delta + certainty_delta + missing_delta) / 4), 4)
    payload = _divergence_payload(
        metric_source="deterministic_confidence_language",
        baseline=baseline,
        comparison=comparison,
        value=value,
        label=_divergence_value_label(value),
        warning=CONFIDENCE_LANGUAGE_WARNING,
        details={
            "baseline": baseline_profile,
            "comparison": comparison_profile,
            "hedging_delta": comparison_profile["hedging_count"] - baseline_profile["hedging_count"],
            "unsupported_certainty_delta": comparison_profile["unsupported_certainty_count"]
            - baseline_profile["unsupported_certainty_count"],
        },
    )
    return DeterministicScore(
        type="divergence",
        criterion="divergence_confidence_language",
        value=payload,
        explanation="Compared local confidence, hedging, and certainty markers against the baseline.",
        confidence=0.6,
    )


def _unavailable_divergence_score(
    *,
    criterion: str,
    metric_source: str,
    baseline: RunAttempt | None,
    comparison: RunAttempt,
    warning: str,
) -> DeterministicScore:
    payload = _divergence_payload(
        metric_source=metric_source,
        baseline=baseline,
        comparison=comparison,
        value=None,
        label="unavailable",
        warning=warning,
        details={"reason": warning},
    )
    return DeterministicScore(
        type="divergence",
        criterion=criterion,
        value=payload,
        explanation=warning,
        confidence=0.0,
    )


def _divergence_payload(
    *,
    metric_source: str,
    baseline: RunAttempt | None,
    comparison: RunAttempt,
    value: float | None,
    label: str,
    warning: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "metric_source": metric_source,
        "comparison_scope": DIVERGENCE_COMPARISON_SCOPE,
        "baseline_attempt_id": baseline.attempt_id if baseline is not None else None,
        "comparison_attempt_id": comparison.attempt_id,
        "value": value,
        "label": label,
        "warning": warning,
        "details": details,
    }


def _divergence_score_exists(
    session: Session, comparison: RunAttempt, result: DeterministicScore
) -> bool:
    return (
        session.scalar(
            select(Score.id).where(
                Score.run_attempt_id == comparison.id,
                Score.evaluator_type == "code",
                Score.type == "divergence",
                Score.criterion == result.criterion,
            )
        )
        is not None
    )


def _definition_for(snapshot: dict[str, Any]) -> dict[str, Any]:
    evaluator_id = str(snapshot.get("id") or "")
    default = DEFAULT_DEFINITIONS.get(evaluator_id, {})
    definition = snapshot.get("definition") if isinstance(snapshot.get("definition"), dict) else {}
    return {**default, **definition}


def _normalized_kind(definition: dict[str, Any], *, snapshot_type: str = "") -> str:
    kind = str(
        definition.get("kind")
        or definition.get("check")
        or (snapshot_type if snapshot_type in EVALUATORS else "")
    ).strip().lower()
    if kind in {"investment_memo_required_sections", "memo_required_sections"}:
        return "required_sections"
    if kind in {"output_token_budget", "token_limit"}:
        return "token_budget"
    if kind in {"non_empty_output", "not_empty"}:
        return "no_empty_output"
    if kind in {"citations_required", "citation_required_placeholder"}:
        return "citation_required"
    return kind


def _score_exists(
    session: Session, attempt: RunAttempt, result: DeterministicScore, version: int | None
) -> bool:
    existing = session.scalars(
        select(Score).where(
            Score.run_attempt_id == attempt.id,
            Score.evaluator_type == "code",
            Score.criterion == result.criterion,
            Score.evaluator_version == version,
        )
    ).all()
    evaluator_id = result.value.get("evaluator_id")
    return any((score.value or {}).get("evaluator_id") == evaluator_id for score in existing)


def _with_evaluator_id(
    result: DeterministicScore, snapshot: dict[str, Any]
) -> DeterministicScore:
    evaluator_id = str(snapshot.get("id") or result.criterion)
    return DeterministicScore(
        type=result.type,
        criterion=result.criterion,
        value={**result.value, "evaluator_id": evaluator_id},
        explanation=result.explanation,
        confidence=result.confidence,
    )


def _is_provider_dry_run(attempt: RunAttempt) -> bool:
    return (attempt.response_payload or {}).get("dry_run") is True


def _missing_text_warning(baseline_text: str, comparison_text: str) -> str | None:
    if not baseline_text.strip() and not comparison_text.strip():
        return "Baseline and comparison outputs are missing text."
    if not baseline_text.strip():
        return "Baseline output is missing text."
    if not comparison_text.strip():
        return "Comparison output is missing text."
    return None


def _semantic_tokens(text: str) -> set[str]:
    return {
        token
        for token in _word_tokens(text)
        if token not in STOP_WORDS and len(token) > 2
    }


def _keyphrases(text: str) -> set[str]:
    tokens = [token for token in _word_tokens(text) if token not in STOP_WORDS and len(token) > 2]
    phrases: set[str] = set()
    for size in (2, 3):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrases.add(" ".join(tokens[index : index + size]))
    return phrases


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _markdown_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(_normalize_heading(match.group(1)))
    return headings


def _normalize_heading(value: str) -> str:
    value = re.sub(r"[*_`]+", "", value).strip().lower()
    return re.sub(r"\s+", " ", value)


def _section_order_changed(baseline_headings: list[str], comparison_headings: list[str]) -> bool:
    shared = [heading for heading in baseline_headings if heading in set(comparison_headings)]
    comparison_positions = {heading: index for index, heading in enumerate(comparison_headings)}
    comparison_order = [comparison_positions[heading] for heading in shared]
    return comparison_order != sorted(comparison_order)


def _confidence_profile(text: str) -> dict[str, Any]:
    hedging_markers = _markers_in_text(text, HEDGING_MARKERS)
    certainty_markers = _markers_in_text(text, CERTAINTY_MARKERS)
    confidence_markers = _markers_in_text(text, CONFIDENCE_MARKERS)
    missing_confidence_language = not hedging_markers and not certainty_markers and not confidence_markers
    return {
        "hedging_markers": hedging_markers,
        "hedging_count": len(hedging_markers),
        "unsupported_certainty_markers": certainty_markers,
        "unsupported_certainty_count": len(certainty_markers),
        "confidence_markers": confidence_markers,
        "confidence_marker_count": len(confidence_markers),
        "missing_confidence_language": missing_confidence_language,
    }


def _markers_in_text(text: str, markers: tuple[str, ...]) -> list[str]:
    normalized = text.lower()
    found: list[str] = []
    for marker in markers:
        pattern = rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])"
        if re.search(pattern, normalized):
            found.append(marker)
    return found


def _divergence_value_label(value: float | None) -> str:
    if value is None:
        return "unavailable"
    if value >= 0.5:
        return "high"
    if value >= 0.2:
        return "medium"
    return "low"


def _contains_section(text: str, section: str) -> bool:
    normalized = _normalize_text(text)
    target = re.escape(_normalize_text(section))
    return re.search(rf"(^|\n|\b){target}(\b|:|\n)", normalized) is not None


def _normalize_text(value: str) -> str:
    collapsed = re.sub(r"[\t\r ]+", " ", value.strip().lower())
    return re.sub(r"\n{2,}", "\n", collapsed)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _criterion(snapshot: dict[str, Any], definition: dict[str, Any], fallback: str) -> str:
    value = definition.get("criterion") or fallback
    return str(value)


def _version(snapshot: dict[str, Any]) -> int | None:
    return _int_or_none(snapshot.get("version"))


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _estimated_output_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text.split()) * 1.3))


def _schema_errors(value: Any, schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _matches_json_type(value, expected_type):
        return [f"{path} expected {expected_type}."]
    if expected_type == "object" or isinstance(value, dict):
        if not isinstance(value, dict):
            return errors
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = _string_list(schema.get("required"))
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key} is required.")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                errors.extend(_schema_errors(value[key], child_schema, path=f"{path}.{key}"))
    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else None
        if item_schema:
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, path=f"{path}[{index}]"))
    return errors


def _matches_json_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "integer":
        return (
            (isinstance(value, int) or (isinstance(value, float) and value.is_integer()))
            and not isinstance(value, bool)
        )
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True
