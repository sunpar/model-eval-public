from __future__ import annotations

import hashlib
import json
from typing import Any

from model_eval_api.artifact_types import (
    ARTIFACT_INPUT_MODE_VALUES,
    DERIVED_ARTIFACT_INPUT_MODE_VALUES,
    ArtifactInputMode,
    MIXED_DERIVED_BUNDLE_INPUT_MODE,
)

SENSITIVE_PROVIDER_PARAM_PARTS = (
    "authorization",
    "header",
    "key",
    "password",
    "secret",
    "token",
)

SENSITIVE_LOCATION_METADATA_KEYS = {
    "file_path",
    "local_path",
    "local_storage_uri",
    "path",
    "source_path",
    "source_uri",
    "source_url",
    "storage_uri",
    "uri",
    "url",
}

DERIVED_ARTIFACT_METADATA_KEYS = {
    "source_artifact_id",
    "source_checksum_sha256",
    "parser_name",
    "parser_version",
    "derived_artifact_id",
}


def sanitize_provider_params(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if any(part in key.lower() for part in SENSITIVE_PROVIDER_PARAM_PARTS):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_provider_params(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_provider_params(item) for item in value]
    return value


def sanitize_preprocessing_error_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = key.lower()
            if _is_sensitive_location_metadata_key(normalized_key):
                continue
            if any(part in normalized_key for part in SENSITIVE_PROVIDER_PARAM_PARTS):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_preprocessing_error_metadata(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_preprocessing_error_metadata(item) for item in value]
    return value


def _is_sensitive_location_metadata_key(normalized_key: str) -> bool:
    return normalized_key in SENSITIVE_LOCATION_METADATA_KEYS or normalized_key.endswith(
        ("_path", "_uri", "_url")
    )


def build_case_snapshot(case: Any) -> dict[str, Any]:
    return {
        "id": case.slug,
        "name": case.name,
        "prompt": case.prompt,
        "prompt_ref": case.prompt_ref,
        "dataset_split": getattr(case, "dataset_split", "dev"),
        "version": case.version,
        "archived": case.archived,
    }


def build_artifact_snapshot(artifact: Any) -> dict[str, Any]:
    return {
        "id": artifact.slug,
        "name": artifact.name,
        "artifact_type": artifact.artifact_type,
        "uri": artifact.uri,
        "input_mode": _artifact_input_mode(getattr(artifact, "input_mode", None)),
        "filename": getattr(artifact, "filename", None),
        "checksum_sha256": getattr(artifact, "checksum_sha256", None),
        "size_bytes": getattr(artifact, "size_bytes", None),
        "mime_type": getattr(artifact, "mime_type", None),
        "storage_uri": getattr(artifact, "storage_uri", None),
        "image_width": getattr(artifact, "image_width", None),
        "image_height": getattr(artifact, "image_height", None),
        "created_at": _isoformat(getattr(artifact, "created_at", None)),
        "metadata": dict(artifact.metadata_json or {}),
        "version": artifact.version,
        "archived": artifact.archived,
    }


def build_artifact_preprocessing_run_snapshot(record: Any) -> dict[str, Any]:
    error: dict[str, Any] | None = None
    if getattr(record, "error_kind", None) or getattr(record, "error_message", None):
        error = {
            "kind": getattr(record, "error_kind", None),
            "message": getattr(record, "error_message", None),
            "metadata": dict(getattr(record, "error_metadata", None) or {}),
        }
    return {
        "id": getattr(record, "id", None),
        "source_artifact_id": getattr(record, "source_artifact_id", None),
        "parser": {
            "name": record.parser_name,
            "version": record.parser_version,
        },
        "status": record.status,
        "source_checksum_sha256": getattr(record, "source_checksum_sha256", None),
        "checksums": dict(getattr(record, "checksums", None) or {}),
        "local_storage_uri": getattr(record, "local_storage_uri", None),
        "source_artifact": dict(getattr(record, "source_artifact_snapshot", None) or {}),
        "derived_artifact_ids": list(getattr(record, "derived_artifact_ids", None) or []),
        "derived_artifacts": [
            dict(item) for item in (getattr(record, "derived_artifact_snapshots", None) or [])
        ],
        "extracted_at": _isoformat(getattr(record, "extracted_at", None)),
        "created_at": _isoformat(getattr(record, "created_at", None)),
        "completed_at": _isoformat(getattr(record, "completed_at", None)),
        "error": error,
    }


def build_system_prompt_snapshot(system_prompt: Any) -> dict[str, Any]:
    return {
        "id": system_prompt.slug,
        "name": system_prompt.name,
        "prompt": system_prompt.prompt,
        "prompt_ref": system_prompt.prompt_ref,
        "messages": list(system_prompt.messages or []),
        "version": system_prompt.version,
        "archived": system_prompt.archived,
    }


def build_conversation_warmer_snapshot(warmer: Any) -> dict[str, Any]:
    return {
        "id": warmer.slug,
        "name": warmer.name,
        "domain": warmer.domain,
        "user_level": warmer.user_level,
        "intent": warmer.intent,
        "messages": list(warmer.messages or []),
        "tags": list(warmer.tags or []),
        "version_note": getattr(warmer, "version_note", None),
        "version": warmer.version,
        "archived": warmer.archived,
    }


def build_model_config_snapshot(model_config: Any) -> dict[str, Any]:
    return {
        "id": model_config.slug,
        "name": model_config.name,
        "provider": model_config.provider,
        "model": model_config.model,
        "temperature": model_config.temperature,
        "max_output_tokens": model_config.max_output_tokens,
        "reasoning_level": model_config.reasoning_level,
        "capability_flags": dict(model_config.capability_flags or {}),
        "raw_provider_params": sanitize_provider_params(model_config.raw_provider_params or {}),
        "version": model_config.version,
        "archived": model_config.archived,
    }


def build_llm_judge_config_snapshot(judge_config: Any) -> dict[str, Any]:
    return {
        "id": judge_config.slug,
        "name": judge_config.name,
        "judge_prompt": judge_config.judge_prompt,
        "rubric_dimensions": list(judge_config.rubric_dimensions or []),
        "output_schema": dict(judge_config.output_schema or {}),
        "judge_model_config_ref": {
            "id": judge_config.judge_model_config_slug,
            "version": judge_config.judge_model_config_version,
        },
        "raw_provider_params": sanitize_provider_params(judge_config.raw_provider_params or {}),
        "calibration_status": judge_config.calibration_status,
        "version": judge_config.version,
        "archived": judge_config.archived,
    }


def build_metric_adapter_config_snapshot(config: Any) -> dict[str, Any]:
    return {
        "id": config.slug,
        "name": config.name,
        "adapter_kind": config.adapter_kind,
        "adapter_version": config.adapter_version,
        "required_inputs": list(config.required_inputs or []),
        "output_schema": dict(config.output_schema or {}),
        "capability_metadata": sanitize_provider_params(config.capability_metadata or {}),
        "local_only": config.local_only,
        "version": config.version,
        "archived": config.archived,
    }


def build_failure_taxonomy_snapshot(taxonomy: Any) -> dict[str, Any]:
    return {
        "slug": taxonomy.slug,
        "name": taxonomy.name,
        "version": taxonomy.version,
        "tags": list(taxonomy.tags or []),
    }


def build_benchmark_suite_snapshot(suite: Any) -> dict[str, Any]:
    items = sorted(
        list(getattr(suite, "items", []) or []),
        key=lambda item: (item.item_type, item.item_slug, item.item_version or 0, item.id or 0),
    )
    cases = [_suite_item_snapshot(item) for item in items if item.item_type == "case"]
    return {
        "id": suite.slug,
        "name": suite.name,
        "description": getattr(suite, "description", None),
        "version": suite.version,
        "archived": suite.archived,
        "controls": sanitize_provider_params(dict(getattr(suite, "controls_json", None) or {})),
        "case_count": len(cases),
        "cases": cases,
        "models": [_suite_item_snapshot(item) for item in items if item.item_type == "model"],
        "system_prompts": [
            _suite_item_snapshot(item) for item in items if item.item_type == "system_prompt"
        ],
        "warmers": [_suite_item_snapshot(item) for item in items if item.item_type == "warmer"],
        "evaluators": [_suite_item_snapshot(item) for item in items if item.item_type == "evaluator"],
    }


def build_evaluator_snapshot(evaluator: Any) -> dict[str, Any]:
    return {
        "id": evaluator.slug,
        "name": evaluator.name,
        "type": evaluator.evaluator_type,
        "definition": dict(evaluator.definition or {}),
        "version": evaluator.version,
        "archived": evaluator.archived,
    }


def build_model_input_snapshot(
    *,
    case_snapshot: dict[str, Any],
    system_prompt_snapshot: dict[str, Any],
    warmer_snapshot: dict[str, Any],
    artifact_snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    artifact_inputs = [_artifact_input_snapshot(artifact) for artifact in artifact_snapshots.values()]
    artifact_input_mode = _run_artifact_input_mode(artifact_inputs)
    derived_bundle = _derived_bundle_snapshot(artifact_inputs, artifact_input_mode)
    return {
        "final_messages": _final_messages(system_prompt_snapshot, warmer_snapshot, case_snapshot),
        "artifact_input_mode": artifact_input_mode,
        "artifact_inputs": artifact_inputs,
        "derived_bundle": derived_bundle,
        "derived_bundle_checksum_sha256": (
            derived_bundle["checksum_sha256"] if derived_bundle else None
        ),
    }


def _final_messages(
    system_prompt_snapshot: dict[str, Any],
    warmer_snapshot: dict[str, Any],
    case_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_messages = system_prompt_snapshot.get("messages") or []
    if system_messages:
        messages.extend(dict(message) for message in system_messages)
    elif system_prompt_snapshot.get("prompt"):
        messages.append({"role": "system", "content": system_prompt_snapshot["prompt"]})
    elif system_prompt_snapshot.get("prompt_ref"):
        messages.append({"role": "system", "content_ref": system_prompt_snapshot["prompt_ref"]})
    warmer_messages = warmer_snapshot.get("messages") or []
    if warmer_messages:
        messages.extend(dict(message) for message in warmer_messages)
    elif warmer_snapshot.get("intent"):
        messages.append({"role": "user", "content": warmer_snapshot["intent"]})
    if case_snapshot.get("prompt"):
        messages.append({"role": "user", "content": case_snapshot["prompt"]})
    elif case_snapshot.get("prompt_ref"):
        messages.append({"role": "user", "content_ref": case_snapshot["prompt_ref"]})
    return messages


def _run_artifact_input_mode(artifact_inputs: list[dict[str, Any]]) -> str:
    modes = {artifact["input_mode"] for artifact in artifact_inputs}
    if not modes:
        return ArtifactInputMode.NONE.value
    if len(modes) == 1:
        return modes.pop()
    if modes <= DERIVED_ARTIFACT_INPUT_MODE_VALUES:
        return MIXED_DERIVED_BUNDLE_INPUT_MODE
    raise ValueError("A run cannot mix artifact input modes across direct and derived artifacts.")


def _artifact_input_snapshot(artifact: dict[str, Any]) -> dict[str, Any]:
    input_mode = _artifact_input_mode(artifact.get("input_mode"))
    payload = {
        "id": artifact["id"],
        "input_mode": input_mode,
        "storage_uri": artifact.get("storage_uri") or artifact.get("uri"),
        "mime_type": artifact.get("mime_type"),
        "checksum_sha256": artifact.get("checksum_sha256"),
    }
    metadata = dict(artifact.get("metadata") or {})
    if input_mode in DERIVED_ARTIFACT_INPUT_MODE_VALUES and any(
        key in metadata for key in DERIVED_ARTIFACT_METADATA_KEYS
    ):
        payload.update(
            {
                "source_artifact_id": metadata.get("source_artifact_id"),
                "source_checksum_sha256": metadata.get("source_checksum_sha256"),
                "parser_name": metadata.get("parser_name"),
                "parser_version": metadata.get("parser_version"),
                "derived_artifact_id": metadata.get("derived_artifact_id"),
            }
        )
    return payload


def _derived_bundle_snapshot(
    artifact_inputs: list[dict[str, Any]], artifact_input_mode: str
) -> dict[str, Any] | None:
    derived_inputs = [
        artifact
        for artifact in artifact_inputs
        if artifact["input_mode"] in DERIVED_ARTIFACT_INPUT_MODE_VALUES
        and any(key in artifact for key in DERIVED_ARTIFACT_METADATA_KEYS)
    ]
    if not derived_inputs:
        return None
    items = sorted(
        [
            {
                "id": artifact["id"],
                "input_mode": artifact["input_mode"],
                "source_checksum_sha256": artifact.get("source_checksum_sha256"),
                "parser_name": artifact.get("parser_name"),
                "parser_version": artifact.get("parser_version"),
                "derived_artifact_id": artifact.get("derived_artifact_id"),
            }
            for artifact in derived_inputs
        ],
        key=lambda item: (str(item["id"]), str(item.get("derived_artifact_id"))),
    )
    checksum_payload = {"artifact_input_mode": artifact_input_mode, "items": items}
    checksum = hashlib.sha256(
        json.dumps(
            checksum_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "artifact_input_mode": artifact_input_mode,
        "checksum_sha256": checksum,
        "derived_artifact_ids": [
            item["derived_artifact_id"]
            for item in items
            if item.get("derived_artifact_id") is not None
        ],
        "items": items,
    }


def _artifact_input_mode(value: Any) -> str:
    if isinstance(value, ArtifactInputMode):
        return value.value
    if value == MIXED_DERIVED_BUNDLE_INPUT_MODE:
        raise ValueError("mixed_derived_bundle is a run-level input mode, not an artifact mode.")
    if isinstance(value, str) and value in ARTIFACT_INPUT_MODE_VALUES:
        return value
    return ArtifactInputMode.DIRECT_FILE.value


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _suite_item_snapshot(item: Any) -> dict[str, Any]:
    payload = {
        "id": item.item_slug,
        "version": item.item_version,
        "split": item.item_split,
    }
    payload.update(dict(item.snapshot_json or {}))
    return payload
