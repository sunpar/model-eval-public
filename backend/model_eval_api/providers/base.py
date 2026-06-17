from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from model_eval_api.providers.errors import ProviderAuthError
from model_eval_api.providers.models import (
    ProviderCapabilities,
    ProviderExecutionConfig,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from model_eval_api.providers.pricing import estimate_cost_usd
from model_eval_api.providers.settings import enforce_provider_config


class BaseProviderAdapter:
    provider: str

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities()

    def execute(
        self,
        request: ProviderRequest,
        *,
        config: ProviderExecutionConfig | None = None,
        dry_run: bool = True,
    ) -> ProviderResponse:
        execution_config = config or ProviderExecutionConfig.from_env()
        enforce_provider_config(request, execution_config, dry_run=dry_run)
        if dry_run:
            return ProviderResponse(
                provider=request.provider,
                model=request.model,
                response_payload={"dry_run": True, "request_payload": request.payload},
                dry_run=True,
            )
        if execution_config.client is None:
            raise ProviderAuthError("Live provider execution requires a configured client.")
        return self.normalize_response(request, execution_config.client(request))

    def extract_tokens(self, response: ProviderResponse) -> ProviderUsage:
        return response.usage

    def estimate_cost(self, usage: ProviderUsage, *, model: str) -> float | None:
        return estimate_cost_usd(provider=self.provider, model=model, usage=usage)


def model_config_from_run_snapshot(run_snapshot: dict[str, Any]) -> dict[str, Any]:
    model_config = run_snapshot.get("model_config") or {}
    if not model_config and "run_snapshot" in run_snapshot:
        model_config = run_snapshot["run_snapshot"].get("model_config") or {}
    return dict(model_config)


def model_input_from_run_snapshot(run_snapshot: dict[str, Any]) -> dict[str, Any]:
    if "model_input_snapshot" in run_snapshot:
        return dict(run_snapshot["model_input_snapshot"] or {})
    nested = run_snapshot.get("run_snapshot") or {}
    return dict(nested.get("model_input_snapshot") or {})


def normalized_request_config(model_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": model_config.get("provider"),
        "model": model_config.get("model"),
        "temperature": model_config.get("temperature"),
        "max_output_tokens": model_config.get("max_output_tokens"),
        "reasoning_level": model_config.get("reasoning_level"),
        "capability_flags": dict(model_config.get("capability_flags") or {}),
    }


def message_content(message: dict[str, Any]) -> str:
    value = message.get("content")
    if value is None:
        value = message.get("content_ref", "")
    return str(value)


def image_artifact_data_url(artifact: dict[str, Any]) -> str | None:
    mime_type = artifact.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
        return None
    return artifact_data_url(artifact)


def artifact_data_url(artifact: dict[str, Any]) -> str | None:
    mime_type = artifact.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type:
        mime_type = "application/octet-stream"
    uri = artifact.get("storage_uri") or artifact.get("uri")
    if not isinstance(uri, str) or not uri:
        return None
    if uri.startswith(("http://", "https://", "data:")):
        return uri
    from model_eval_api.artifacts import local_storage_path

    path = local_storage_path(uri)
    if path is None:
        return None
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{mime_type};base64,{encoded}"


def artifact_filename(artifact: dict[str, Any]) -> str:
    filename = artifact.get("filename") or artifact.get("name")
    if isinstance(filename, str) and filename.strip():
        return Path(filename).name
    uri = artifact.get("storage_uri") or artifact.get("uri")
    if isinstance(uri, str):
        parsed = urlparse(uri)
        if parsed.path:
            name = Path(unquote(parsed.path)).name
            if name:
                return name
    artifact_id = artifact.get("id")
    if isinstance(artifact_id, str) and artifact_id.strip():
        return artifact_id
    return "artifact"


def file_artifacts(model_input: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(artifact)
        for artifact in model_input.get("artifact_inputs") or []
        if isinstance(artifact, dict) and artifact.get("input_mode") == "direct_file"
    ]


def image_artifacts(model_input: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(artifact)
        for artifact in model_input.get("artifact_inputs") or []
        if isinstance(artifact, dict)
        and artifact.get("input_mode") == "image_direct"
        and isinstance(artifact.get("mime_type"), str)
        and str(artifact.get("mime_type")).startswith("image/")
    ]


def int_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if type(value) is int:
            return value
    return 0
