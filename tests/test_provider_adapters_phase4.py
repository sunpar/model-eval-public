from __future__ import annotations

import base64

import pytest
from sqlalchemy.orm import sessionmaker

from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.database import create_database_engine
from model_eval_api.persistence.models import Base
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_project,
    create_workspace,
)
from model_eval_api.providers import (
    AnthropicAdapter,
    ErrorKind,
    OpenAIAdapter,
    ProviderBlockedError,
    ProviderExecutionConfig,
    build_pricing_snapshot,
    classify_provider_error,
)
from model_eval_api.providers.settings import provider_config_from_env


def _run_snapshot(provider: str, raw_provider_params: dict) -> dict:
    return {
        "model_config": {
            "id": f"{provider}_model",
            "provider": provider,
            "model": "gpt-5.5" if provider == "openai" else "claude-opus-4",
            "temperature": 0.2,
            "max_output_tokens": 512,
            "reasoning_level": "high",
            "capability_flags": {"supports_json_schema": True},
            "raw_provider_params": raw_provider_params,
        },
        "model_input_snapshot": {
            "final_messages": [
                {"role": "system", "content": "Follow the house style."},
                {"role": "developer", "content": "Use compact bullets."},
                {"role": "user", "content": "Warm-up context."},
                {"role": "assistant", "content": "Acknowledged."},
                {"role": "user", "content": "Write the memo."},
            ],
            "artifact_input_mode": "none",
            "artifact_inputs": [],
        },
    }


def test_openai_adapter_builds_payload_from_snapshot_and_preserves_raw_params() -> None:
    adapter = OpenAIAdapter()

    request = adapter.build_request(
        _run_snapshot(
            "openai",
            {
                "temperature": 0.9,
                "response_format": {"type": "json_schema", "json_schema": {"name": "memo"}},
                "metadata": {"experiment": "phase4"},
            },
        )
    )

    assert request.provider == "openai"
    assert request.model == "gpt-5.5"
    assert request.raw_provider_params["response_format"]["type"] == "json_schema"
    assert request.payload["input"] == [
        {"role": "system", "content": "Follow the house style."},
        {"role": "developer", "content": "Use compact bullets."},
        {"role": "user", "content": "Warm-up context."},
        {"role": "assistant", "content": "Acknowledged."},
        {"role": "user", "content": "Write the memo."},
    ]
    assert request.payload["temperature"] == 0.9
    assert request.payload["max_output_tokens"] == 512
    assert request.payload["reasoning"] == {"effort": "high"}
    assert request.payload["response_format"]["json_schema"]["name"] == "memo"
    assert adapter.capabilities("gpt-5.5").supports_json_schema is True


def test_openai_raw_reasoning_effort_overrides_normalized_reasoning_level() -> None:
    adapter = OpenAIAdapter()
    snapshot = _run_snapshot("openai", {"reasoning_effort": "low"})
    snapshot["model_config"]["reasoning_level"] = "high"

    request = adapter.build_request(snapshot)

    assert request.payload["reasoning"] == {"effort": "low"}
    assert request.raw_provider_params["reasoning_effort"] == "low"


def test_openai_raw_reasoning_object_overrides_reasoning_effort_none() -> None:
    adapter = OpenAIAdapter()
    snapshot = _run_snapshot(
        "openai",
        {"reasoning": {"effort": "medium"}, "reasoning_effort": "none"},
    )
    snapshot["model_config"]["reasoning_level"] = "high"

    request = adapter.build_request(snapshot)

    assert request.payload["reasoning"] == {"effort": "medium"}


@pytest.mark.parametrize("raw_key", ["reasoning_effort", "reasoning_level"])
def test_openai_raw_reasoning_none_suppresses_normalized_reasoning_level(raw_key: str) -> None:
    adapter = OpenAIAdapter()
    snapshot = _run_snapshot("openai", {raw_key: "none"})
    snapshot["model_config"]["reasoning_level"] = "high"

    request = adapter.build_request(snapshot)

    assert "reasoning" not in request.payload


def test_openai_maps_raw_max_tokens_to_max_output_tokens() -> None:
    adapter = OpenAIAdapter()
    snapshot = _run_snapshot("openai", {"max_tokens": 256})
    snapshot["model_config"]["max_output_tokens"] = 512

    request = adapter.build_request(snapshot)

    assert request.payload["max_output_tokens"] == 256
    assert "max_tokens" not in request.payload


def test_openai_raw_params_cannot_override_request_identity() -> None:
    adapter = OpenAIAdapter()

    request = adapter.build_request(
        _run_snapshot(
            "openai",
            {
                "model": "malicious-model",
                "input": [{"role": "user", "content": "replaced"}],
                "temperature": 0.4,
            },
        )
    )

    assert request.model == "gpt-5.5"
    assert request.payload["model"] == "gpt-5.5"
    assert request.payload["input"][0]["content"] == "Follow the house style."
    assert request.payload["temperature"] == 0.4


def test_openai_maps_image_artifact_inputs_to_content_parts(tmp_path) -> None:
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"png bytes")
    snapshot = _run_snapshot("openai", {})
    snapshot["model_input_snapshot"]["artifact_inputs"] = [
        {
            "id": "chart",
            "input_mode": "image_direct",
            "storage_uri": image_path.as_uri(),
            "mime_type": "image/png",
        }
    ]

    request = OpenAIAdapter().build_request(snapshot)

    image_part = request.payload["input"][-1]["content"][0]
    assert image_part["type"] == "input_image"
    assert image_part["detail"] == "auto"
    assert image_part["image_url"] == (
        "data:image/png;base64," + base64.b64encode(b"png bytes").decode("ascii")
    )


def test_openai_maps_file_artifact_inputs_to_content_parts(tmp_path) -> None:
    file_path = tmp_path / "memo.txt"
    file_path.write_text("memo file", encoding="utf-8")
    snapshot = _run_snapshot("openai", {})
    snapshot["model_input_snapshot"]["artifact_inputs"] = [
        {
            "id": "memo",
            "filename": "memo.txt",
            "input_mode": "direct_file",
            "storage_uri": file_path.as_uri(),
            "mime_type": "text/plain",
        }
    ]

    request = OpenAIAdapter().build_request(snapshot)

    file_part = request.payload["input"][-1]["content"][0]
    assert file_part == {
        "type": "input_file",
        "filename": "memo.txt",
        "file_data": "data:text/plain;base64,"
        + base64.b64encode(b"memo file").decode("ascii"),
    }


def test_openai_embeds_loopback_file_uri_artifact_inputs(tmp_path) -> None:
    file_path = tmp_path / "memo.txt"
    file_path.write_text("memo file", encoding="utf-8")
    loopback_uri = "file://127.0.0.1" + file_path.as_uri().removeprefix("file://")
    snapshot = _run_snapshot("openai", {})
    snapshot["model_input_snapshot"]["artifact_inputs"] = [
        {
            "id": "memo",
            "filename": "memo.txt",
            "input_mode": "direct_file",
            "storage_uri": loopback_uri,
            "mime_type": "text/plain",
        }
    ]

    request = OpenAIAdapter().build_request(snapshot)

    assert request.payload["input"][-1]["content"] == [
        {
            "type": "input_file",
            "filename": "memo.txt",
            "file_data": "data:text/plain;base64,"
            + base64.b64encode(b"memo file").decode("ascii"),
        }
    ]


def test_anthropic_adapter_builds_payload_with_system_and_chat_messages() -> None:
    adapter = AnthropicAdapter()

    request = adapter.build_request(
        _run_snapshot(
            "anthropic",
            {
                "thinking": {"type": "enabled", "budget_tokens": 4096},
                "stop_sequences": ["END"],
            },
        )
    )

    assert request.provider == "anthropic"
    assert request.model == "claude-opus-4"
    assert request.raw_provider_params["thinking"]["budget_tokens"] == 4096
    assert request.payload["system"] == "Follow the house style.\nUse compact bullets."
    assert request.payload["messages"] == [
        {"role": "user", "content": "Warm-up context."},
        {"role": "assistant", "content": "Acknowledged."},
        {"role": "user", "content": "Write the memo."},
    ]
    assert request.payload["max_tokens"] == 512
    assert request.payload["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert request.payload["stop_sequences"] == ["END"]

    normalized_reasoning_request = adapter.build_request(_run_snapshot("anthropic", {}))
    assert normalized_reasoning_request.payload["thinking"] == {
        "type": "enabled",
        "budget_tokens": 8192,
    }


def test_anthropic_positive_thinking_budget_overrides_reasoning_level() -> None:
    adapter = AnthropicAdapter()

    request = adapter.build_request(_run_snapshot("anthropic", {"thinking_budget": 2048}))

    assert request.payload["thinking"] == {"type": "enabled", "budget_tokens": 2048}


@pytest.mark.parametrize("thinking_budget", [True, False, 0, -1])
def test_anthropic_omits_invalid_numeric_thinking_budget(
    thinking_budget: object,
) -> None:
    adapter = AnthropicAdapter()

    request = adapter.build_request(_run_snapshot("anthropic", {"thinking_budget": thinking_budget}))

    assert "thinking" not in request.payload


def test_anthropic_maps_raw_max_output_tokens_to_max_tokens() -> None:
    adapter = AnthropicAdapter()
    snapshot = _run_snapshot("anthropic", {"max_output_tokens": 256})
    snapshot["model_config"]["max_output_tokens"] = 512

    request = adapter.build_request(snapshot)

    assert request.payload["max_tokens"] == 256
    assert "max_output_tokens" not in request.payload


def test_anthropic_raw_params_cannot_override_request_identity() -> None:
    adapter = AnthropicAdapter()

    request = adapter.build_request(
        _run_snapshot(
            "anthropic",
            {
                "model": "malicious-model",
                "messages": [{"role": "user", "content": "replaced"}],
                "system": "replacement system",
                "temperature": 0.4,
            },
        )
    )

    assert request.model == "claude-opus-4"
    assert request.payload["model"] == "claude-opus-4"
    assert request.payload["system"] == "Follow the house style.\nUse compact bullets."
    assert request.payload["messages"][0] == {"role": "user", "content": "Warm-up context."}
    assert request.payload["temperature"] == 0.4


def test_anthropic_maps_image_artifact_inputs_to_content_blocks(tmp_path) -> None:
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"png bytes")
    snapshot = _run_snapshot("anthropic", {})
    snapshot["model_input_snapshot"]["artifact_inputs"] = [
        {
            "id": "chart",
            "input_mode": "image_direct",
            "storage_uri": image_path.as_uri(),
            "mime_type": "image/png",
        }
    ]

    request = AnthropicAdapter().build_request(snapshot)

    image_block = request.payload["messages"][-1]["content"][0]
    assert image_block == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(b"png bytes").decode("ascii"),
        },
    }


def test_anthropic_maps_file_artifact_inputs_to_content_blocks(tmp_path) -> None:
    file_path = tmp_path / "memo.txt"
    file_path.write_text("memo file", encoding="utf-8")
    snapshot = _run_snapshot("anthropic", {})
    snapshot["model_input_snapshot"]["artifact_inputs"] = [
        {
            "id": "memo",
            "filename": "memo.txt",
            "input_mode": "direct_file",
            "storage_uri": file_path.as_uri(),
            "mime_type": "text/plain",
        }
    ]

    request = AnthropicAdapter().build_request(snapshot)

    document_block = request.payload["messages"][-1]["content"][0]
    assert document_block == {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "text/plain",
            "data": base64.b64encode(b"memo file").decode("ascii"),
        },
        "title": "memo.txt",
    }


def test_blank_and_unrecognized_local_only_env_fails_closed(monkeypatch) -> None:
    for value in ("", "maybe"):
        monkeypatch.setenv("MODEL_EVAL_LOCAL_ONLY", value)

        assert provider_config_from_env().local_only is True


def test_explicit_local_only_env_tokens_are_honored(monkeypatch) -> None:
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("MODEL_EVAL_LOCAL_ONLY", value)

        assert provider_config_from_env().local_only is True

    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("MODEL_EVAL_LOCAL_ONLY", value)

        assert provider_config_from_env().local_only is False


def test_dry_run_execution_uses_no_client_and_local_only_blocks_live_calls() -> None:
    adapter = OpenAIAdapter()
    request = adapter.build_request(_run_snapshot("openai", {}))
    config = ProviderExecutionConfig(local_only=True)

    dry_run_response = adapter.execute(request, config=config, dry_run=True)

    assert dry_run_response.dry_run is True
    assert dry_run_response.provider_response_id is None
    assert dry_run_response.response_payload["dry_run"] is True
    assert dry_run_response.output_text == ""

    try:
        adapter.execute(request, config=config, dry_run=False)
    except ProviderBlockedError as exc:
        assert exc.kind is ErrorKind.BLOCKED_BY_CONFIG
        assert classify_provider_error(exc) is ErrorKind.BLOCKED_BY_CONFIG
    else:
        raise AssertionError("local-only mode should block live provider calls")


def test_provider_allow_deny_config_blocks_disallowed_providers() -> None:
    request = OpenAIAdapter().build_request(_run_snapshot("openai", {}))

    try:
        OpenAIAdapter().execute(
            request,
            config=ProviderExecutionConfig(allowed_providers={"anthropic"}),
            dry_run=True,
        )
    except ProviderBlockedError as exc:
        assert "not in the allow list" in str(exc)
    else:
        raise AssertionError("provider allow list should block dry-run execution")

    try:
        OpenAIAdapter().execute(
            request,
            config=ProviderExecutionConfig(denied_providers={"openai"}),
            dry_run=True,
        )
    except ProviderBlockedError as exc:
        assert "deny list" in str(exc)
    else:
        raise AssertionError("provider deny list should block dry-run execution")


def test_response_normalization_extracts_ids_usage_tokens_and_cost() -> None:
    openai = OpenAIAdapter()
    request = openai.build_request(_run_snapshot("openai", {}))

    response = openai.normalize_response(
        request,
        {
            "id": "resp_123",
            "output_text": "Final memo",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 40,
                "total_tokens": 140,
            },
        },
    )

    assert response.provider_response_id == "resp_123"
    assert response.output_text == "Final memo"
    assert response.usage.input_tokens == 100
    assert response.usage.output_tokens == 40
    assert response.usage.total_tokens == 140
    assert openai.extract_tokens(response).total_tokens == 140
    assert openai.estimate_cost(response.usage, model="gpt-5.5") is not None

    anthropic = AnthropicAdapter()
    anthropic_request = anthropic.build_request(_run_snapshot("anthropic", {}))
    anthropic_response = anthropic.normalize_response(
        anthropic_request,
        {
            "id": "msg_123",
            "content": [{"type": "text", "text": "Claude memo"}],
            "usage": {"input_tokens": 90, "output_tokens": 30},
        },
    )

    assert anthropic_response.provider_response_id == "msg_123"
    assert anthropic_response.output_text == "Claude memo"
    assert anthropic_response.usage.total_tokens == 120


def test_openai_response_output_text_ignores_malformed_output_items() -> None:
    openai = OpenAIAdapter()
    request = openai.build_request(_run_snapshot("openai", {}))

    response = openai.normalize_response(
        request,
        {
            "output": [
                "not-a-message",
                {"content": "not-content"},
                {"content": ["not-content", {"type": "output_text", "text": "Final memo"}]},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )

    assert response.output_text == "Final memo"


def test_openai_response_output_text_ignores_malformed_output_container() -> None:
    openai = OpenAIAdapter()
    request = openai.build_request(_run_snapshot("openai", {}))

    response = openai.normalize_response(
        request,
        {
            "output": 42,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )

    assert response.output_text == ""


def test_execute_accepts_mocked_client_when_local_only_is_disabled() -> None:
    adapter = OpenAIAdapter()
    request = adapter.build_request(_run_snapshot("openai", {}))

    response = adapter.execute(
        request,
        config=ProviderExecutionConfig(
            local_only=False,
            client=lambda _: {
                "id": "resp_mocked",
                "output_text": "Mocked memo",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        ),
        dry_run=False,
    )

    assert response.provider_response_id == "resp_mocked"
    assert response.output_text == "Mocked memo"
    assert response.usage.total_tokens == 15


def test_provider_error_classification() -> None:
    assert classify_provider_error(TimeoutError("slow")) is ErrorKind.RETRYABLE
    assert classify_provider_error(ValueError("invalid request")) is ErrorKind.INVALID_REQUEST
    assert classify_provider_error(PermissionError("bad api key")) is ErrorKind.PROVIDER_AUTH
    assert classify_provider_error(RuntimeError("unexpected")) is ErrorKind.UNKNOWN


def test_experiment_stores_pricing_snapshot_for_manifest_models() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        workspace = create_workspace(session, slug="default", name="Default")
        project = create_project(session, workspace=workspace, slug="pricing", name="Pricing")
        manifest = parse_manifest(
            {
                "id": "pricing_exp",
                "name": "Pricing experiment",
                "cases": [{"id": "case", "prompt": "Write memo"}],
                "models": [
                    {
                        "id": "openai_model",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "params": {"temperature": 0.2},
                    },
                    {
                        "id": "anthropic_model",
                        "provider": "anthropic",
                        "model": "claude-opus-4",
                        "params": {"temperature": 0.2},
                    },
                ],
                "system_prompts": [{"id": "system", "prompt": "Be direct."}],
                "warmers": [{"id": "none", "messages": []}],
                "design": {"replicates": 1},
                "evaluation": {"evaluators": []},
            }
        )

        experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)

        assert experiment.pricing_snapshot == build_pricing_snapshot(
            [
                ("openai", "gpt-5.5"),
                ("anthropic", "claude-opus-4"),
            ]
        )
