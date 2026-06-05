from __future__ import annotations

import itertools
import json
import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from model_eval_api.execution_states import AttemptStatus, ExperimentStatus
from model_eval_api.executor import ExecutionControls, default_provider_adapters
from model_eval_api.persistence.models import Experiment, JudgeExecution, Run, RunAttempt
from model_eval_api.persistence.repositories import record_audit_event, record_score
from model_eval_api.providers import (
    ProviderAdapter,
    ProviderExecutionConfig,
    ProviderRequest,
    ProviderResponse,
)
from model_eval_api.providers.settings import enforce_provider_config
from model_eval_api.response_payloads import attempt_output_text as _attempt_output_text
from model_eval_api.results_analytics import (
    JUDGE_PAIRWISE_CRITERION,
    JUDGE_PASS_FAIL_CRITERION,
    JUDGE_RUBRIC_CRITERION,
)


def run_llm_judge(
    session: Session,
    *,
    experiment_id: int,
    evaluator_id: str,
    dry_run: bool = True,
    local_only: bool = True,
    position_swap: bool = True,
    random_seed: int | None = 1,
    provider_config: ProviderExecutionConfig | None = None,
    adapters: dict[str, ProviderAdapter] | None = None,
) -> dict[str, Any]:
    experiment = _require_experiment(session, experiment_id)
    if experiment.status != ExperimentStatus.COMPLETE.value:
        raise ValueError("Judge execution requires a complete experiment.")
    if _existing_execution(session, experiment_id=experiment.id, evaluator_id=evaluator_id):
        raise ValueError(
            f"Judge execution already exists for evaluator '{evaluator_id}' on experiment {experiment.id}."
        )
    evaluator_snapshot = _judge_evaluator_snapshot(experiment, evaluator_id)
    judge_config = dict(evaluator_snapshot["definition"]["judge_config"])
    model_snapshot, execution_config, controls = _enforce_execution_gates(
        experiment,
        judge_config=judge_config,
        dry_run=dry_run,
        local_only=local_only,
        provider_config=provider_config,
    )
    attempts = _succeeded_attempts(session, experiment)
    comparisons = _pairwise_comparisons(attempts, position_swap=position_swap, random_seed=random_seed)
    request_payload = _judge_request_payload(judge_config, comparisons)
    _enforce_context_budget(judge_config, comparisons, controls.context_budget_tokens)
    execution = JudgeExecution(
        project_id=experiment.project_id,
        experiment_id=experiment.id,
        evaluator_id=evaluator_id,
        judge_config_snapshot=judge_config,
        source_run_attempt_ids=sorted({attempt.id for comparison in comparisons for attempt in comparison.attempts}),
        request_payload=request_payload,
        response_payload={},
        status="running",
        mode="pairwise",
        dry_run=dry_run,
        local_only=execution_config.local_only,
        metadata_json={
            "position_swap": position_swap,
            "random_seed": random_seed,
            "comparison_count": len(comparisons),
        },
    )
    session.add(execution)
    session.flush()

    score_ids: list[int] = []
    response_comparisons: list[dict[str, Any]] = []
    for comparison in comparisons:
        if dry_run:
            decision = _synthetic_decision(comparison, judge_config)
        else:
            decision = _live_decision(
                comparison,
                judge_config=judge_config,
                model_snapshot=model_snapshot,
                provider_config=execution_config,
                adapters=adapters,
            )
        response_comparisons.append(decision)
        score_ids.extend(
            _record_pairwise_scores(
                session,
                execution=execution,
                evaluator_snapshot=evaluator_snapshot,
                comparison=comparison,
                decision=decision,
            )
        )
    execution.status = "succeeded"
    execution.score_ids = score_ids
    execution.response_payload = {
        "dry_run": dry_run,
        "comparisons": response_comparisons,
        "scores_recorded": len(score_ids),
    }
    record_audit_event(
        session,
        experiment=experiment,
        event_kind="llm_judge_execution_completed",
        entity_type="judge_execution",
        entity_id=str(execution.id),
        details={
            "evaluator_id": evaluator_id,
            "dry_run": dry_run,
            "local_only": execution_config.local_only,
            "scores_recorded": len(score_ids),
        },
    )
    return {
        "judge_execution_id": execution.id,
        "experiment_id": experiment.id,
        "evaluator_id": evaluator_id,
        "status": execution.status,
        "comparisons": len(comparisons),
        "scores_recorded": len(score_ids),
        "dry_run": dry_run,
        "local_only": execution_config.local_only,
    }


def _require_experiment(session: Session, experiment_id: int) -> Experiment:
    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError(f"Experiment {experiment_id} does not exist.")
    return experiment


def _existing_execution(
    session: Session, *, experiment_id: int, evaluator_id: str
) -> JudgeExecution | None:
    return session.scalar(
        select(JudgeExecution).where(
            JudgeExecution.experiment_id == experiment_id,
            JudgeExecution.evaluator_id == evaluator_id,
        )
    )


def _judge_evaluator_snapshot(experiment: Experiment, evaluator_id: str) -> dict[str, Any]:
    snapshot = (experiment.evaluator_snapshots or {}).get(evaluator_id)
    if not snapshot:
        raise ValueError(f"Evaluator '{evaluator_id}' was not found on experiment {experiment.id}.")
    if snapshot.get("type") != "llm_judge":
        raise ValueError(f"Evaluator '{evaluator_id}' is not an LLM judge evaluator.")
    judge_config = (snapshot.get("definition") or {}).get("judge_config")
    if not isinstance(judge_config, dict):
        raise ValueError(f"Evaluator '{evaluator_id}' does not include a judge config snapshot.")
    return snapshot


def _enforce_execution_gates(
    experiment: Experiment,
    *,
    judge_config: dict[str, Any],
    dry_run: bool,
    local_only: bool,
    provider_config: ProviderExecutionConfig | None,
) -> tuple[dict[str, Any], ProviderExecutionConfig, ExecutionControls]:
    model_ref = judge_config.get("judge_model_config_ref") or {}
    model_snapshot = (experiment.model_config_snapshots or {}).get(model_ref.get("id"))
    if not isinstance(model_snapshot, dict):
        raise ValueError(f"Judge model config '{model_ref.get('id')}' was not found.")
    controls = ExecutionControls.from_snapshot(experiment.controls_snapshot)
    effective_local_only = bool(local_only or controls.local_only)
    request = ProviderRequest(
        provider=str(model_snapshot.get("provider") or ""),
        model=str(model_snapshot.get("model") or ""),
        payload={},
        raw_provider_params=dict(judge_config.get("raw_provider_params") or {}),
        normalized_config={
            "provider": model_snapshot.get("provider"),
            "model": model_snapshot.get("model"),
            "judge": True,
        },
    )
    execution_config = _provider_execution_config(
        experiment,
        local_only=effective_local_only,
        base_config=provider_config,
    )
    try:
        enforce_provider_config(
            request,
            execution_config,
            dry_run=dry_run,
        )
    except Exception as error:
        raise ValueError(str(error)) from error
    if controls.max_total_cost_usd is not None and _current_experiment_cost(experiment) >= controls.max_total_cost_usd:
        raise ValueError("Cost cap exceeded before judge execution.")
    return model_snapshot, execution_config, controls


def _provider_execution_config(
    experiment: Experiment,
    *,
    local_only: bool,
    base_config: ProviderExecutionConfig | None = None,
) -> ProviderExecutionConfig:
    base = base_config or ProviderExecutionConfig.from_env()
    project_allowed = _policy_tuple(experiment.project.provider_allow_list)
    project_denied = _policy_tuple(experiment.project.provider_deny_list)
    allowed = _intersect_allowed(base.allowed_providers, project_allowed)
    denied = tuple(sorted({*_policy_tuple(base.denied_providers), *project_denied}))
    return ProviderExecutionConfig(
        local_only=local_only,
        allowed_providers=allowed,
        denied_providers=denied,
        client=base.client,
    )


def _policy_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values if str(value))


def _intersect_allowed(
    base_allowed: Any,
    project_allowed: tuple[str, ...],
) -> tuple[str, ...] | None:
    base_values = _policy_tuple(base_allowed)
    if base_allowed is not None and project_allowed:
        return tuple(sorted({item for item in base_values if item in set(project_allowed)}))
    if base_allowed is not None:
        return base_values
    if project_allowed:
        return project_allowed
    return None


def _current_experiment_cost(experiment: Experiment) -> float:
    return sum(
        float(attempt.cost_usd or 0.0)
        for run in experiment.runs
        for attempt in run.attempts
        if not attempt.cache_hit
    )


def _enforce_context_budget(
    judge_config: dict[str, Any],
    comparisons: list[JudgeComparison],
    budget_tokens: int | None,
) -> None:
    if budget_tokens is None:
        return
    for comparison in comparisons:
        if _judge_request_token_estimate(judge_config, comparison) > budget_tokens:
            raise ValueError("Context budget exceeded before judge execution.")


def _judge_request_token_estimate(
    judge_config: dict[str, Any],
    comparison: JudgeComparison,
) -> int:
    text = "\n\n".join(
        [
            str(judge_config.get("judge_prompt") or ""),
            json.dumps(judge_config.get("rubric_dimensions") or [], sort_keys=True),
            json.dumps(judge_config.get("output_schema") or {}, sort_keys=True),
            _comparison_prompt(comparison),
        ]
    )
    return _estimate_tokens(text)


def _succeeded_attempts(session: Session, experiment: Experiment) -> list[RunAttempt]:
    attempts = session.scalars(
        select(RunAttempt)
        .join(Run)
        .where(
            Run.experiment_id == experiment.id,
            RunAttempt.status == AttemptStatus.SUCCEEDED.value,
        )
        .order_by(
            Run.case_slug,
            Run.system_prompt_slug,
            Run.warmer_slug,
            RunAttempt.replicate_index,
            Run.model_config_slug,
            RunAttempt.id,
        )
    ).all()
    latest_by_run: dict[int, RunAttempt] = {}
    for attempt in attempts:
        if dict(attempt.response_payload or {}).get("dry_run") is True:
            continue
        existing = latest_by_run.get(attempt.run_id)
        if existing is None or _attempt_sort_key(attempt) > _attempt_sort_key(existing):
            latest_by_run[attempt.run_id] = attempt
    return sorted(
        latest_by_run.values(),
        key=lambda attempt: (
            attempt.run.case_slug,
            attempt.run.system_prompt_slug,
            attempt.run.warmer_slug,
            attempt.replicate_index,
            attempt.run.model_config_slug,
            attempt.id or 0,
        ),
    )


def _attempt_sort_key(attempt: RunAttempt) -> tuple[int, int]:
    return (int(attempt.attempt_number or 1), int(attempt.id or 0))


class JudgeComparison:
    def __init__(
        self,
        *,
        comparison_id: str,
        attempts: list[RunAttempt],
        swap_index: int,
    ) -> None:
        self.comparison_id = comparison_id
        self.attempts = attempts
        self.swap_index = swap_index


def _pairwise_comparisons(
    attempts: list[RunAttempt], *, position_swap: bool, random_seed: int | None
) -> list[JudgeComparison]:
    grouped: dict[tuple[str, str, str, int], list[RunAttempt]] = {}
    for attempt in attempts:
        key = (
            attempt.run.case_slug,
            attempt.run.system_prompt_slug,
            attempt.run.warmer_slug,
            int(attempt.replicate_index),
        )
        grouped.setdefault(key, []).append(attempt)
    rng = random.Random(random_seed)
    comparisons: list[JudgeComparison] = []
    for key, group in sorted(grouped.items()):
        for pair_index, pair in enumerate(itertools.combinations(sorted(group, key=lambda item: item.id), 2), start=1):
            ordered = list(pair)
            rng.shuffle(ordered)
            comparison_base = f"{key[0]}:{key[1]}:{key[2]}:{key[3]}:{pair_index}"
            comparisons.append(
                JudgeComparison(
                    comparison_id=f"{comparison_base}:0",
                    attempts=ordered,
                    swap_index=0,
                )
            )
            if position_swap:
                comparisons.append(
                    JudgeComparison(
                        comparison_id=f"{comparison_base}:1",
                        attempts=list(reversed(ordered)),
                        swap_index=1,
                    )
                )
    return comparisons


def _judge_request_payload(
    judge_config: dict[str, Any], comparisons: list[JudgeComparison]
) -> dict[str, Any]:
    return {
        "judge_prompt": judge_config.get("judge_prompt"),
        "rubric_dimensions": list(judge_config.get("rubric_dimensions") or []),
        "output_schema": dict(judge_config.get("output_schema") or {}),
        "comparisons": [
            {
                "comparison_id": comparison.comparison_id,
                "swap_index": comparison.swap_index,
                "prompt": _comparison_prompt(comparison),
                "answers": [
                    {
                        "label": _label(index),
                        "text": _attempt_output_text(attempt),
                    }
                    for index, attempt in enumerate(comparison.attempts)
                ],
            }
            for comparison in comparisons
        ],
    }


def _comparison_prompt(comparison: JudgeComparison) -> str:
    sections = []
    for index, attempt in enumerate(comparison.attempts):
        sections.append(f"Answer {_label(index)}:\n{_attempt_output_text(attempt)}")
    return "\n\n".join(sections)


def _synthetic_decision(comparison: JudgeComparison, judge_config: dict[str, Any]) -> dict[str, Any]:
    answer_rows = [
        {
            "label": _label(index),
            "run_attempt_id": attempt.id,
            "answer_token_count": _answer_token_count(attempt),
        }
        for index, attempt in enumerate(comparison.attempts)
    ]
    winner = max(answer_rows, key=lambda item: (item["answer_token_count"], item["label"]))
    pass_fail = {row["label"]: row["label"] == winner["label"] for row in answer_rows}
    return {
        "comparison_id": comparison.comparison_id,
        "swap_index": comparison.swap_index,
        "answer_order": [attempt.id for attempt in comparison.attempts],
        "winner": winner["label"],
        "confidence": 0.75,
        "dry_run": True,
        "answers": answer_rows,
        "pass_fail": pass_fail,
        "rubric_scores": _synthetic_rubric_scores(judge_config, pass_fail),
    }


def _synthetic_rubric_scores(
    judge_config: dict[str, Any], pass_fail: dict[str, bool]
) -> dict[str, dict[str, float]]:
    dimensions = _rubric_dimension_names(judge_config)
    return {
        label: {dimension: 5.0 if passed else 2.0 for dimension in dimensions}
        for label, passed in pass_fail.items()
    }


def _live_decision(
    comparison: JudgeComparison,
    *,
    judge_config: dict[str, Any],
    model_snapshot: dict[str, Any],
    provider_config: ProviderExecutionConfig | None,
    adapters: dict[str, ProviderAdapter] | None,
) -> dict[str, Any]:
    provider = str(model_snapshot.get("provider") or "")
    adapter = (adapters or default_provider_adapters()).get(provider)
    if adapter is None:
        raise ValueError(f"Provider '{provider}' is not configured.")
    try:
        request = adapter.build_request(
            _judge_run_snapshot(
                judge_config=judge_config,
                model_snapshot=model_snapshot,
                comparison=comparison,
            )
        )
        response = adapter.execute(
            request,
            config=provider_config,
            dry_run=False,
        )
    except Exception as error:
        raise ValueError(str(error)) from error
    return _provider_decision(comparison, response)


def _judge_run_snapshot(
    *,
    judge_config: dict[str, Any],
    model_snapshot: dict[str, Any],
    comparison: JudgeComparison,
) -> dict[str, Any]:
    output_schema = dict(judge_config.get("output_schema") or {})
    messages = [
        {
            "role": "system",
            "content": str(judge_config.get("judge_prompt") or ""),
        },
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    "Return JSON with a winner label and confidence.",
                    f"Rubric dimensions: {json.dumps(judge_config.get('rubric_dimensions') or [], sort_keys=True)}",
                    f"Output schema: {json.dumps(output_schema, sort_keys=True)}",
                    _comparison_prompt(comparison),
                ]
            ),
        },
    ]
    raw_params = dict(judge_config.get("raw_provider_params") or {})
    return {
        "model_config": {
            "provider": model_snapshot.get("provider"),
            "model": model_snapshot.get("model"),
            "temperature": model_snapshot.get("temperature"),
            "max_output_tokens": model_snapshot.get("max_output_tokens"),
            "reasoning_level": model_snapshot.get("reasoning_level"),
            "capability_flags": dict(model_snapshot.get("capability_flags") or {}),
            "raw_provider_params": raw_params,
        },
        "model_input_snapshot": {
            "final_messages": messages,
        },
    }


def _provider_decision(
    comparison: JudgeComparison, response: ProviderResponse
) -> dict[str, Any]:
    parsed = _parse_output_json(response.output_text)
    winner = parsed.get("winner") or parsed.get("preferred_answer") or parsed.get("choice")
    labels = {_label(index) for index, _ in enumerate(comparison.attempts)}
    if not isinstance(winner, str) or winner.strip().upper() not in labels:
        raise ValueError("Live judge response did not include a valid winner label.")
    confidence = parsed.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        confidence = None
    pass_fail = _parsed_pass_fail(parsed, labels, winner.strip().upper())
    return {
        "comparison_id": comparison.comparison_id,
        "swap_index": comparison.swap_index,
        "answer_order": [attempt.id for attempt in comparison.attempts],
        "winner": winner.strip().upper(),
        "confidence": confidence,
        "dry_run": False,
        "provider_response_id": response.provider_response_id,
        "response_payload": response.response_payload,
        "pass_fail": pass_fail,
        "rubric_scores": _parsed_rubric_scores(parsed, labels),
        "structured_outputs": _parsed_answer_assessments(parsed, labels),
        "answers": [
            {
                "label": _label(index),
                "run_attempt_id": attempt.id,
                "answer_token_count": _answer_token_count(attempt),
            }
            for index, attempt in enumerate(comparison.attempts)
        ],
    }


def _parse_output_json(output_text: str) -> dict[str, Any]:
    parsed: Any | None = None
    for candidate in [output_text, *_json_candidate_strings(output_text)]:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        raise ValueError("Live judge response was not valid JSON.")
    if not isinstance(parsed, dict):
        raise ValueError("Live judge response must be a JSON object.")
    return parsed


def _json_candidate_strings(output_text: str) -> list[str]:
    candidates: list[str] = []
    stripped = output_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidates.append("\n".join(lines[1:-1]).strip())
    start = output_text.find("{")
    end = output_text.rfind("}")
    if start != -1 and end > start:
        candidates.append(output_text[start : end + 1])
    return candidates


def _parsed_pass_fail(
    parsed: dict[str, Any],
    labels: set[str],
    winner: str,
) -> dict[str, bool]:
    raw = parsed.get("pass_fail")
    if isinstance(raw, dict):
        return {
            label: bool(raw[label])
            for label in sorted(labels)
            if label in raw and isinstance(raw[label], bool)
        }
    return {label: label == winner for label in sorted(labels)}


def _parsed_rubric_scores(
    parsed: dict[str, Any],
    labels: set[str],
) -> dict[str, dict[str, float]]:
    raw = parsed.get("rubric_scores") or parsed.get("rubric")
    if not isinstance(raw, dict):
        return {}
    scores: dict[str, dict[str, float]] = {}
    for label in sorted(labels):
        value = raw.get(label)
        if not isinstance(value, dict):
            continue
        dimension_scores = {
            str(dimension): float(score)
            for dimension, score in value.items()
            if isinstance(score, int | float)
        }
        if dimension_scores:
            scores[label] = dimension_scores
    return scores


def _parsed_answer_assessments(
    parsed: dict[str, Any],
    labels: set[str],
) -> dict[str, dict[str, Any]]:
    raw = (
        parsed.get("answer_assessments")
        or parsed.get("answer_assessment")
        or parsed.get("assessments")
        or parsed.get("structured_outputs")
    )
    if not isinstance(raw, dict):
        return {}
    normalized_raw = {
        str(key).strip().upper(): value for key, value in raw.items() if str(key).strip()
    }
    assessments: dict[str, dict[str, Any]] = {}
    for label in sorted(labels):
        value = normalized_raw.get(label)
        if isinstance(value, dict):
            assessments[label] = dict(value)
    return assessments


def _record_pairwise_scores(
    session: Session,
    *,
    execution: JudgeExecution,
    evaluator_snapshot: dict[str, Any],
    comparison: JudgeComparison,
    decision: dict[str, Any],
) -> list[int]:
    score_ids: list[int] = []
    for index, attempt in enumerate(comparison.attempts):
        label = _label(index)
        structured_outputs = decision.get("structured_outputs") or {}
        structured_output = (
            structured_outputs.get(label) if isinstance(structured_outputs, dict) else None
        )
        base_value = {
            "label": label,
            "evaluator_id": execution.evaluator_id,
            "judge_execution_id": execution.id,
            "comparison_id": comparison.comparison_id,
            "swap_index": comparison.swap_index,
            "position_swapped": comparison.swap_index == 1,
            "answer_token_count": _answer_token_count(attempt),
            "judge_config_version": execution.judge_config_snapshot.get("version"),
            "judge_config_id": execution.judge_config_snapshot.get("id"),
        }
        if isinstance(structured_output, dict):
            base_value["structured_output"] = structured_output
        score = record_score(
            session,
            run_attempt=attempt,
            type="pairwise_preference",
            evaluator_type="llm_judge",
            criterion=JUDGE_PAIRWISE_CRITERION,
            value={
                **base_value,
                "outcome": "winner" if label == decision["winner"] else "loser",
                "winner": decision["winner"],
            },
            explanation=_judge_score_explanation(decision),
            confidence=decision["confidence"],
            evaluator_version=int(evaluator_snapshot.get("version") or 1),
        )
        session.flush()
        score_ids.append(score.id)
        pass_fail = decision.get("pass_fail") or {}
        if isinstance(pass_fail, dict) and isinstance(pass_fail.get(label), bool):
            pass_score = record_score(
                session,
                run_attempt=attempt,
                type="pass_fail",
                evaluator_type="llm_judge",
                criterion=JUDGE_PASS_FAIL_CRITERION,
                value={**base_value, "passed": pass_fail[label]},
                explanation=_judge_score_explanation(decision),
                confidence=decision["confidence"],
                evaluator_version=int(evaluator_snapshot.get("version") or 1),
            )
            session.flush()
            score_ids.append(pass_score.id)
        rubric_scores = decision.get("rubric_scores") or {}
        label_scores = rubric_scores.get(label) if isinstance(rubric_scores, dict) else None
        if isinstance(label_scores, dict):
            for dimension, value in sorted(label_scores.items()):
                rubric_score = record_score(
                    session,
                    run_attempt=attempt,
                    type="rubric_score",
                    evaluator_type="llm_judge",
                    criterion=JUDGE_RUBRIC_CRITERION,
                    value={**base_value, "dimension": str(dimension), "score": value},
                    explanation=_judge_score_explanation(decision),
                    confidence=decision["confidence"],
                    evaluator_version=int(evaluator_snapshot.get("version") or 1),
                )
                session.flush()
                score_ids.append(rubric_score.id)
    return score_ids


def _judge_score_explanation(decision: dict[str, Any]) -> str:
    return (
        "Synthetic dry-run LLM judge decision."
        if decision.get("dry_run")
        else "Live LLM judge decision."
    )


def _rubric_dimension_names(judge_config: dict[str, Any]) -> list[str]:
    dimensions: list[str] = []
    for dimension in judge_config.get("rubric_dimensions") or []:
        if isinstance(dimension, dict) and dimension.get("name"):
            dimensions.append(str(dimension["name"]))
    return dimensions


def _label(index: int) -> str:
    return chr(ord("A") + index)


def _answer_token_count(attempt: RunAttempt) -> int:
    if attempt.output_tokens is not None:
        return int(attempt.output_tokens)
    return _estimate_tokens(_attempt_output_text(attempt))


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))
