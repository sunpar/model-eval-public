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


THINKING_BUDGET_BY_LEVEL = {
    "low": 1024,
    "medium": 4096,
    "high": 8192,
}


class AnthropicAdapter(BaseProviderAdapter):
    provider = "anthropic"

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_images=True,
            supports_files=True,
            supports_tools=True,
            supports_json_schema=False,
        )

    def build_request(self, run_snapshot: dict[str, Any]) -> ProviderRequest:
        model_config = model_config_from_run_snapshot(run_snapshot)
        model_input = model_input_from_run_snapshot(run_snapshot)
        raw_params = dict(model_config.get("raw_provider_params") or {})
        normalized = normalized_request_config(model_config)
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for message in model_input.get("final_messages", []):
            role = message.get("role")
            content = message_content(message)
            if role in {"system", "developer"}:
                system_parts.append(content)
            elif role in {"user", "assistant"}:
                messages.append({"role": role, "content": content})
        artifact_blocks = _anthropic_artifact_blocks(model_input)
        if artifact_blocks:
            messages.append({"role": "user", "content": artifact_blocks})

        payload: dict[str, Any] = {
            "model": model_config["model"],
            "messages": messages,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        max_tokens = _anthropic_max_tokens(raw_params, normalized["max_output_tokens"])
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        thinking = _anthropic_thinking(raw_params, normalized.get("reasoning_level"))
        if thinking is not None:
            payload["thinking"] = thinking

        payload.update(
            {
                key: value
                for key, value in raw_params.items()
                if key
                not in {
                    "max_output_tokens",
                    "max_tokens",
                    "model",
                    "messages",
                    "system",
                    "thinking",
                    "thinking_budget",
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
        usage = _anthropic_usage(response_payload.get("usage") or {})
        return ProviderResponse(
            provider=self.provider,
            model=request.model,
            response_payload=response_payload,
            provider_response_id=response_payload.get("id"),
            output_text=_anthropic_output_text(response_payload),
            usage=usage,
            cost_usd=self.estimate_cost(usage, model=request.model),
            provider_metadata={
                key: response_payload[key]
                for key in ("model", "stop_reason")
                if key in response_payload
            },
        )


def _anthropic_thinking(
    raw_params: dict[str, Any], reasoning_level: str | None
) -> dict[str, Any] | None:
    if "thinking" in raw_params:
        return raw_params["thinking"]
    if "thinking_budget" in raw_params:
        budget = raw_params["thinking_budget"]
        if isinstance(budget, str):
            budget = THINKING_BUDGET_BY_LEVEL.get(budget)
        if type(budget) is int and budget > 0:
            return {"type": "enabled", "budget_tokens": budget}
        return None
    if reasoning_level and reasoning_level != "none":
        budget = THINKING_BUDGET_BY_LEVEL.get(reasoning_level)
        if type(budget) is int:
            return {"type": "enabled", "budget_tokens": budget}
    return None


def _anthropic_max_tokens(raw_params: dict[str, Any], normalized_max_output_tokens: Any) -> Any:
    if "max_tokens" in raw_params:
        return raw_params["max_tokens"]
    if "max_output_tokens" in raw_params:
        return raw_params["max_output_tokens"]
    return normalized_max_output_tokens


def _anthropic_artifact_blocks(model_input: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for artifact in image_artifacts(model_input):
        image_url = image_artifact_data_url(artifact)
        if image_url is None:
            continue
        mime_type = str(artifact.get("mime_type"))
        if image_url.startswith("data:"):
            _, encoded = image_url.split(",", 1)
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": encoded,
                    },
                }
            )
        else:
            blocks.append({"type": "image", "source": {"type": "url", "url": image_url}})
    for artifact in file_artifacts(model_input):
        file_data = artifact_data_url(artifact)
        if file_data is None:
            continue
        if file_data.startswith("data:"):
            _, encoded = file_data.split(",", 1)
            source = {
                "type": "base64",
                "media_type": str(artifact.get("mime_type") or "application/octet-stream"),
                "data": encoded,
            }
        else:
            source = {"type": "url", "url": file_data}
        blocks.append(
            {
                "type": "document",
                "source": source,
                "title": artifact_filename(artifact),
            }
        )
    return blocks


def _anthropic_usage(payload: dict[str, Any]) -> ProviderUsage:
    input_tokens = int_value(payload, "input_tokens")
    output_tokens = int_value(payload, "output_tokens")
    total_tokens = int_value(payload, "total_tokens") or input_tokens + output_tokens
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _anthropic_output_text(payload: dict[str, Any]) -> str:
    parts = [
        item["text"]
        for item in payload.get("content") or []
        if item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    return "".join(parts)
