from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


MetricInputs = dict[str, Any]


@dataclass(frozen=True)
class MetricAdapterScore:
    type: str
    criterion: str
    value: dict[str, Any]
    explanation: str
    confidence: float


@dataclass(frozen=True)
class MetricAdapter:
    kind: str
    version: str
    required_inputs: list[str]
    output_schema: dict[str, Any]
    local_only: bool
    capability_metadata: dict[str, Any]
    evaluate: Callable[[MetricInputs], MetricAdapterScore]


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


def get_metric_adapter(kind: str) -> MetricAdapter:
    normalized = normalize_metric_adapter_kind(kind)
    try:
        return METRIC_ADAPTERS[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported metric adapter kind: {kind}") from error


def normalize_metric_adapter_kind(kind: str) -> str:
    return kind.strip().lower().replace(" ", "_").replace("-", "_")


def run_metric_adapter(kind: str, inputs: MetricInputs) -> MetricAdapterScore:
    adapter = get_metric_adapter(kind)
    validation = validate_metric_adapter_inputs(adapter.required_inputs, inputs)
    if not validation["valid"]:
        return _score(
            criterion=adapter.kind,
            score=None,
            label="missing_inputs",
            details=validation,
            explanation=f"Missing required metric adapter inputs: {', '.join(validation['missing'])}.",
            confidence=0.0,
        )
    return adapter.evaluate(inputs)


def validate_metric_adapter_inputs(
    required_inputs: list[str], inputs: MetricInputs
) -> dict[str, Any]:
    present: list[str] = []
    missing: list[str] = []
    for field in required_inputs:
        normalized = str(field).strip()
        if _input_present(normalized, inputs.get(normalized)):
            present.append(normalized)
        else:
            missing.append(normalized)
    return {"valid": not missing, "present": present, "missing": missing}


def map_deepeval_result_to_score(
    adapter_snapshot: dict[str, Any], result: dict[str, Any]
) -> MetricAdapterScore:
    adapter_kind = normalize_metric_adapter_kind(str(adapter_snapshot.get("adapter_kind") or ""))
    adapter_version = str(adapter_snapshot.get("adapter_version") or "")
    score = _float_or_none(result.get("score"))
    success = result.get("success")
    metric_name = str(result.get("name") or result.get("metric") or adapter_kind)
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    return MetricAdapterScore(
        type="metric_adapter",
        criterion=adapter_kind,
        value={
            "metric_source": "deepeval_style",
            "source_kind": "judge_backed",
            "adapter_kind": adapter_kind,
            "adapter_version": adapter_version,
            "metric_name": metric_name,
            "score": score,
            "success": success,
            "metadata": dict(metadata),
        },
        explanation=str(result.get("reason") or result.get("explanation") or ""),
        confidence=score if score is not None else 0.0,
    )


def _retrieval_precision(inputs: MetricInputs) -> MetricAdapterScore:
    answer_tokens = _text_tokens(str(inputs.get("answer_text") or ""))
    chunks = _list(inputs.get("retrieved_chunks"))
    relevant = 0
    for chunk in chunks:
        chunk_tokens = _text_tokens(_text_from_mapping(chunk))
        if answer_tokens and chunk_tokens and answer_tokens & chunk_tokens:
            relevant += 1
    score = round(relevant / len(chunks), 4) if chunks else 0.0
    return _score(
        criterion="retrieval_precision",
        score=score,
        label=_score_label(score),
        details={
            "relevant_chunk_count": relevant,
            "retrieved_chunk_count": len(chunks),
        },
        explanation="Measured retrieved chunk lexical overlap with the answer text.",
        confidence=0.7,
    )


def _citation_coverage(inputs: MetricInputs) -> MetricAdapterScore:
    answer_text = str(inputs.get("answer_text") or "")
    available_ids = sorted(
        {
            citation_id
            for citation in _list(inputs.get("citations"))
            if isinstance(citation, dict)
            for citation_id in [_citation_id(citation)]
            if citation_id is not None
        }
    )
    referenced_ids = set(re.findall(r"\[([A-Za-z0-9_.:-]+)\]", answer_text))
    cited_ids = sorted(referenced_ids & set(available_ids))
    uncited_ids = sorted(set(available_ids) - set(cited_ids))
    score = round(len(cited_ids) / len(available_ids), 4) if available_ids else 0.0
    return _score(
        criterion="citation_coverage",
        score=score,
        label=_score_label(score),
        details={
            "available_ids": available_ids,
            "cited_ids": cited_ids,
            "uncited_ids": uncited_ids,
        },
        explanation="Measured how many provided citation IDs appear in bracket citations.",
        confidence=0.75,
    )


def _groundedness_checklist(inputs: MetricInputs) -> MetricAdapterScore:
    answer_tokens = _text_tokens(str(inputs.get("answer_text") or ""))
    context_tokens = set()
    for chunk in _list(inputs.get("retrieved_chunks")):
        context_tokens |= _text_tokens(_text_from_mapping(chunk))
    derived_artifacts = _list(inputs.get("derived_artifacts"))
    checklist = {
        "has_answer_text": bool(answer_tokens),
        "has_supporting_context": bool(answer_tokens & context_tokens),
        "has_derived_artifacts": bool(derived_artifacts),
    }
    score = round(sum(checklist.values()) / len(checklist), 4)
    return _score(
        criterion="groundedness_checklist",
        score=score,
        label=_score_label(score),
        details={"checklist": checklist},
        explanation="Checked local answer text against retrieved context and derived artifacts.",
        confidence=0.65,
    )


def _answer_relevance(inputs: MetricInputs) -> MetricAdapterScore:
    answer_tokens = _text_tokens(str(inputs.get("answer_text") or ""))
    reference_tokens = set()
    for answer in _list(inputs.get("reference_answers")):
        reference_tokens |= _text_tokens(_text_from_mapping(answer))
    score = round(_jaccard(answer_tokens, reference_tokens), 4)
    return _score(
        criterion="answer_relevance",
        score=score,
        label=_score_label(score),
        details={
            "answer_terms": sorted(answer_tokens)[:20],
            "reference_terms": sorted(reference_tokens)[:20],
        },
        explanation="Measured local lexical overlap between answer and reference answers.",
        confidence=0.6,
    )


def _score(
    *,
    criterion: str,
    score: float | None,
    label: str,
    details: dict[str, Any],
    explanation: str,
    confidence: float,
) -> MetricAdapterScore:
    return MetricAdapterScore(
        type="metric_adapter",
        criterion=criterion,
        value={
            "metric_source": "local_metric_adapter",
            "source_kind": "deterministic_heuristic",
            "adapter_kind": criterion,
            "score": score,
            "label": label,
            **details,
        },
        explanation=explanation,
        confidence=confidence,
    )


def _input_present(field: str, value: Any) -> bool:
    if field == "answer_text":
        return isinstance(value, str) and bool(value.strip())
    if field in {"retrieved_chunks", "citations", "derived_artifacts"}:
        return isinstance(value, list) and bool(value)
    if field == "reference_answers":
        return (isinstance(value, str) and bool(value.strip())) or (
            isinstance(value, list) and bool(value)
        )
    return value not in (None, "", [], {})


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text_from_mapping(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    for key in ("text", "chunk_text", "content", "answer", "reference_answer"):
        item = value.get(key)
        if isinstance(item, str):
            return item
    return " ".join(str(item) for item in value.values() if isinstance(item, str))


def _citation_id(citation: dict[str, Any]) -> str | None:
    for key in ("id", "citation_id"):
        if key not in citation:
            continue
        value = citation[key]
        if value is None:
            continue
        citation_id = str(value).strip()
        if citation_id:
            return citation_id
    return None


def _text_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())
        if token not in STOP_WORDS and len(token) > 2
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _score_label(score: float | None) -> str:
    if score is None:
        return "unavailable"
    if score >= 0.8:
        return "strong"
    if score >= 0.5:
        return "partial"
    return "weak"


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


METRIC_ADAPTERS: dict[str, MetricAdapter] = {
    "retrieval_precision": MetricAdapter(
        kind="retrieval_precision",
        version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object", "required": ["score", "label"]},
        local_only=True,
        capability_metadata={"input_family": "retrieval"},
        evaluate=_retrieval_precision,
    ),
    "citation_coverage": MetricAdapter(
        kind="citation_coverage",
        version="local-1",
        required_inputs=["answer_text", "citations"],
        output_schema={"type": "object", "required": ["score", "cited_ids"]},
        local_only=True,
        capability_metadata={"input_family": "citations"},
        evaluate=_citation_coverage,
    ),
    "groundedness_checklist": MetricAdapter(
        kind="groundedness_checklist",
        version="local-1",
        required_inputs=["answer_text", "retrieved_chunks", "derived_artifacts"],
        output_schema={"type": "object", "required": ["score", "checklist"]},
        local_only=True,
        capability_metadata={"input_family": "derived_artifacts"},
        evaluate=_groundedness_checklist,
    ),
    "answer_relevance": MetricAdapter(
        kind="answer_relevance",
        version="local-1",
        required_inputs=["answer_text", "reference_answers"],
        output_schema={"type": "object", "required": ["score", "label"]},
        local_only=True,
        capability_metadata={"input_family": "reference_answers"},
        evaluate=_answer_relevance,
    ),
}
