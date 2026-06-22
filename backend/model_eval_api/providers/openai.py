from __future__ import annotations

from typing import Any

from model_eval_api.providers.base import (
    BaseProviderAdapter,
    artifact_data_url,
    artifact_filename,
    file_artifacts,
    image_artifact_data_url,
    image_artifacts,
    int_value,
    message_content,
    model_config_from_run_snapshot,
    model_input_from_run_snapshot,
    normalized_request_config,
)
from model_eval_api.providers.models import (
    ProviderCapabilities,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)


class OpenAIAdapter(BaseProviderAdapter):
    provider = "openai"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_images=True,
            supports_files=True,
            supports_tools=True,
            supports_json_schema=True,
        )

    def build_request(self, run_snapshot: dict[str, Any]) -> ProviderRequest:
        model_config = model_config_from_run_snapshot(run_snapshot)
        model_input = model_input_from_run_snapshot(run_snapshot)
        raw_params = dict(model_config.get("raw_provider_params") or {})
        normalized = normalized_request_config(model_config)

        input_messages = [
            {"role": message["role"], "content": message_content(message)}
            for message in model_input.get("final_messages", [])
        ]
        artifact_parts = _openai_artifact_parts(model_input)
        if artifact_parts:
            input_messages.append({"role": "user", "content": artifact_parts})
        payload: dict[str, Any] = {"model": model_config["model"], "input": input_messages}
        if normalized["temperature"] is not None:
            payload["temperature"] = normalized["temperature"]
        max_output_tokens = _openai_max_output_tokens(raw_params, normalized["max_output_tokens"])
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens

        reasoning = raw_params.get("reasoning")
        reasoning_level = normalized["reasoning_level"]
        raw_reasoning_level = raw_params.get("reasoning_level")
        if raw_reasoning_level == "none":
            reasoning_level = "none"
        elif raw_reasoning_level:
            reasoning_level = raw_reasoning_level
        if reasoning is not None:
            payload["reasoning"] = reasoning
        elif "reasoning_effort" in raw_params:
            reasoning_effort = raw_params["reasoning_effort"]
            if reasoning_effort == "none":
                pass
            elif reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort}
            elif reasoning_level and reasoning_level != "none":
                payload["reasoning"] = {"effort": reasoning_level}
        elif reasoning_level and reasoning_level != "none":
            payload["reasoning"] = {"effort": reasoning_level}

        payload.update(
            {
                key: value
                for key, value in raw_params.items()
                if key
                not in {
                    "max_tokens",
                    "max_output_tokens",
                    "model",
                    "input",
                    "messages",
                    "reasoning",
                    "reasoning_effort",
                    "reasoning_level",
                }
            }
        )
        return ProviderRequest(
            provider=self.provider,
            model=model_config["model"],
            payload=payload,
            raw_provider_params=raw_params,
            normalized_config=normalized,
        )

    def normalize_response(
        self, request: ProviderRequest, response_payload: dict[str, Any]
    ) -> ProviderResponse:
        usage = _openai_usage(response_payload.get("usage") or {})
        return ProviderResponse(
            provider=self.provider,
            model=request.model,
            response_payload=response_payload,
            provider_response_id=response_payload.get("id"),
            output_text=_openai_output_text(response_payload),
            usage=usage,
            cost_usd=self.estimate_cost(usage, model=request.model),
            provider_metadata={
                key: response_payload[key]
                for key in ("system_fingerprint", "model")
                if key in response_payload
            },
        )


def _openai_usage(payload: dict[str, Any]) -> ProviderUsage:
    input_tokens = int_value(payload, "input_tokens", "prompt_tokens")
    output_tokens = int_value(payload, "output_tokens", "completion_tokens")
    total_tokens = int_value(payload, "total_tokens") or input_tokens + output_tokens
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _openai_max_output_tokens(
    raw_params: dict[str, Any], normalized_max_output_tokens: Any
) -> Any:
    if "max_output_tokens" in raw_params:
        return raw_params["max_output_tokens"]
    if "max_tokens" in raw_params:
        return raw_params["max_tokens"]
    return normalized_max_output_tokens


def _openai_artifact_parts(model_input: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for artifact in image_artifacts(model_input):
        image_url = image_artifact_data_url(artifact)
        if image_url is not None:
            parts.append({"type": "input_image", "image_url": image_url, "detail": "auto"})
    for artifact in file_artifacts(model_input):
        file_data = artifact_data_url(artifact)
        if file_data is not None and file_data.startswith("data:"):
            parts.append(
                {
                    "type": "input_file",
                    "filename": artifact_filename(artifact),
                    "file_data": file_data,
                }
            )
    return parts


def _openai_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    output_items = payload.get("output")
    if not isinstance(output_items, list):
        return ""
    for item in output_items:
        if not isinstance(item, dict):
            continue
        content_items = item.get("content")
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            if (
                isinstance(content, dict)
                and content.get("type") in {"output_text", "text"}
                and isinstance(content.get("text"), str)
            ):
                parts.append(content["text"])
    return "".join(parts)
