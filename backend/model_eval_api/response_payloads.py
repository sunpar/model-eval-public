from __future__ import annotations

from typing import Any

from model_eval_api.persistence.models import RunAttempt


def attempt_output_text(attempt: RunAttempt) -> str:
    payload = attempt.response_payload or {}
    for key in ("output_text", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value and not value.isspace():
            return value
    output_text = _openai_output_text(payload)
    if output_text:
        return output_text
    content_text = _content_text(payload.get("content"))
    if content_text:
        return content_text
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                message_text = _content_text(content)
                if message_text:
                    return message_text
    return ""


def _openai_output_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if isinstance(item, dict):
            parts.extend(_content_parts(item.get("content")))
    return "".join(parts)


def _content_text(value: Any) -> str:
    return "".join(_content_parts(value))


def _content_parts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    parts: list[str] = []
    for item in value:
        if (
            isinstance(item, dict)
            and item.get("type") in {"output_text", "text"}
            and isinstance(item.get("text"), str)
        ):
            parts.append(item["text"])
    return parts
