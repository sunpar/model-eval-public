import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from typer.testing import CliRunner

from model_eval_api.main import app as api_app
from model_eval_api.manifest import (
    ControlsManifest,
    ManifestValidationError,
    expand_manifest,
    load_manifest_file,
    parse_manifest,
    validate_manifest_payload,
)
from model_eval_cli.main import app as cli_app


EXAMPLE_MANIFEST = Path("examples/copper_memo_context_sensitivity.yaml")


def _assert_load_manifest_error_startswith(manifest_path: Path, expected_prefix: str) -> None:
    with pytest.raises(ManifestValidationError) as error:
        load_manifest_file(manifest_path)

    assert len(error.value.errors) == 1
    assert error.value.errors[0].startswith(expected_prefix)


def _assert_cli_validate_failure(manifest_path: Path, expected_stderr: str) -> None:
    result = CliRunner().invoke(cli_app, ["validate", str(manifest_path)])

    assert result.exit_code == 1
    assert "valid: false" in result.stdout
    assert expected_stderr in result.stderr
    assert all(line.startswith("- ") for line in result.stderr.splitlines())
    assert result.exception is None or isinstance(result.exception, SystemExit)


def _manifest_payload_with_controls(controls: dict[str, object]) -> dict[str, object]:
    return {
        "name": "controls_manifest",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 1},
        "controls": controls,
        "evaluation": {"evaluators": ["eval_a"]},
    }


def test_copper_memo_manifest_expands_to_expected_counts() -> None:
    manifest = load_manifest_file(EXAMPLE_MANIFEST)
    preview = expand_manifest(manifest)

    assert preview.logical_runs == 16
    assert preview.run_attempts == 32
    assert preview.replicates == 2
    assert preview.randomize_run_order is True
    assert preview.random_seed is not None
    assert preview.estimated_token_count == 0
    assert preview.estimated_cost_usd == 0.0


def test_manifest_preserves_normalized_and_raw_provider_fields() -> None:
    manifest = load_manifest_file(EXAMPLE_MANIFEST)

    openai_model = manifest.models[0]
    anthropic_model = manifest.models[1]

    assert openai_model.provider == "openai"
    assert openai_model.model == "gpt-5.5"
    assert openai_model.temperature == 0.2
    assert openai_model.reasoning_level == "high"
    assert openai_model.raw_provider_params == {
        "reasoning_effort": "high",
        "temperature": 0.2,
    }
    assert anthropic_model.reasoning_level == "high"
    assert anthropic_model.raw_provider_params["thinking_budget"] == "high"


def test_model_config_library_reference_can_be_id_only() -> None:
    payload = {
        "name": "model_ref_manifest",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": ["library_model_v1"],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 1},
        "evaluation": {"evaluators": ["eval_a"]},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is True
    assert result.errors == []


def test_manifest_rejects_suite_reference_without_expanded_dimensions() -> None:
    payload = {
        "name": "suite_ref_manifest",
        "suite": {"id": "copper_suite", "version": 2, "split": "validation"},
        "design": {"type": "full_factorial", "replicates": 1, "split": "validation"},
        "controls": {"local_only": True},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Manifest dimension 'cases' must include at least one item." in result.errors


def test_manifest_accepts_expanded_benchmark_suite_reference_and_split_filter() -> None:
    payload = {
        "name": "suite_ref_manifest",
        "suite": {"id": "copper_suite", "version": 2, "split": "validation"},
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 1, "split": "validation"},
        "controls": {"local_only": True},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is True
    assert result.errors == []


def test_manifest_rejects_archived_benchmark_suite_split() -> None:
    payload = {
        "name": "suite_ref_manifest",
        "suite": {"id": "copper_suite", "version": 2, "split": "archived"},
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 1},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Benchmark suite split 'archived' cannot be executed." in result.errors


def test_manifest_distinguishes_reliability_replicates_from_retry_recovery() -> None:
    payload = {
        "name": "replicate_controls",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 3},
        "controls": {"retry_failed": True, "reliability_replicates": 3},
        "evaluation": {"evaluators": ["eval_a"]},
    }

    manifest = parse_manifest(payload)
    preview = expand_manifest(manifest)

    assert preview.replicates == 3
    assert preview.reliability_replicates == 3
    assert preview.run_attempts == 3
    replicate_group_id = preview.replicate_groups[0]["replicate_group_id"]
    assert replicate_group_id.startswith("group_")
    assert ":" not in replicate_group_id
    assert preview.replicate_groups == [
        {
            "replicate_group_id": replicate_group_id,
            "sample_size": 3,
            "attempt_ids": [attempt.attempt_id for attempt in preview.runs[0].attempts],
        }
    ]


def test_preview_reliability_replicates_matches_expanded_attempts() -> None:
    payload = {
        "name": "replicate_controls",
        "cases": [{"id": "case:a", "prompt": "A"}],
        "models": [{"id": "model:a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt:a"],
        "warmers": ["warmer:a"],
        "design": {"type": "full_factorial", "replicates": 2},
        "controls": {"reliability_replicates": 5},
        "evaluation": {"evaluators": ["eval_a"]},
    }

    preview = expand_manifest(parse_manifest(payload))

    assert preview.run_attempts == 2
    assert preview.reliability_replicates == 2
    assert preview.replicate_groups[0]["replicate_group_id"].startswith("group_")


def test_preview_preserves_lower_reliability_replicate_control() -> None:
    payload = {
        "name": "replicate_controls",
        "cases": [{"id": "case:a", "prompt": "A"}],
        "models": [{"id": "model:a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt:a"],
        "warmers": ["warmer:a"],
        "design": {"type": "full_factorial", "replicates": 3},
        "controls": {"reliability_replicates": 1},
        "evaluation": {"evaluators": ["eval_a"]},
    }

    preview = expand_manifest(parse_manifest(payload))

    assert preview.run_attempts == 3
    assert preview.reliability_replicates == 1


def test_manifest_rejects_invalid_reliability_replicate_controls() -> None:
    payload = {
        "name": "bad_replicate_controls",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 2},
        "controls": {"reliability_replicates": 0},
        "evaluation": {"evaluators": ["eval_a"]},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Controls reliability_replicates must be an integer greater than or equal to 1." in result.errors


def test_manifest_accepts_positive_max_parallel_requests_control() -> None:
    payload = _manifest_payload_with_controls({"max_parallel_requests": 2})

    manifest = parse_manifest(payload)

    assert manifest.controls.max_parallel_requests == 2


def test_max_parallel_requests_schema_remains_positive_integer_contract() -> None:
    schema = ControlsManifest.model_json_schema()["properties"]["max_parallel_requests"]

    assert schema["type"] == "integer"
    assert schema["minimum"] == 1


@pytest.mark.parametrize("max_parallel_requests", [True, False, 0, -1, "2"])
def test_manifest_rejects_invalid_max_parallel_requests_controls(
    max_parallel_requests: object,
) -> None:
    payload = _manifest_payload_with_controls({"max_parallel_requests": max_parallel_requests})

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Controls max_parallel_requests must be an integer greater than or equal to 1." in result.errors


def test_validation_reports_duplicate_ids_and_unknown_design_references() -> None:
    payload = {
        "name": "bad_manifest",
        "cases": [{"id": "case_a", "prompt": "A"}, {"id": "case_a", "prompt": "B"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {
            "type": "full_factorial",
            "replicates": 1,
            "cases": ["missing_case"],
        },
        "evaluation": {"evaluators": ["eval_a"]},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert any("Duplicate case id 'case_a'" in error for error in result.errors)
    assert any("Unknown design case reference 'missing_case'" in error for error in result.errors)


def test_manifest_normalizes_artifacts_and_reports_duplicate_artifact_ids() -> None:
    payload = {
        "name": "artifact_manifest",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "artifacts": ["artifact_a", {"id": "artifact_b", "uri": "file://artifact-b.txt"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 1},
        "evaluation": {"evaluators": ["eval_a"]},
    }

    manifest = parse_manifest(payload)
    preview = expand_manifest(manifest)

    assert [artifact.id for artifact in manifest.artifacts] == ["artifact_a", "artifact_b"]
    assert preview.dimensions == {
        "cases": 1,
        "models": 1,
        "system_prompts": 1,
        "warmers": 1,
    }

    payload["artifacts"] = ["artifact_a", "artifact_a"]
    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Duplicate artifact id 'artifact_a'." in result.errors


def test_validation_reports_empty_dimensions_invalid_design_and_bad_params() -> None:
    payload = {
        "name": "empty_manifest",
        "cases": [],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": "hot"}],
        "system_prompts": [],
        "warmers": [],
        "design": {"type": "latin_square", "replicates": 0},
        "evaluation": {"evaluators": []},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Design type 'latin_square' is not supported; supported types: full_factorial." in result.errors
    assert "Design replicates must be an integer greater than or equal to 1." in result.errors
    assert "Manifest dimension 'cases' must include at least one item." in result.errors
    assert "Model 'model_a' params must be a mapping of provider parameters." in result.errors


def test_validation_rejects_boolean_replicates_and_duplicate_design_references() -> None:
    payload = {
        "name": "bad_design_refs",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {
            "type": "full_factorial",
            "replicates": True,
            "models": ["model_a", "model_a"],
        },
        "evaluation": {"evaluators": ["eval_a"]},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Design replicates must be an integer greater than or equal to 1." in result.errors
    assert "Duplicate design model reference 'model_a'." in result.errors


def test_validation_rejects_malformed_design_controls() -> None:
    payload = {
        "name": "bad_design_controls",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {
            "type": "full_factorial",
            "replicates": 1,
            "randomize_run_order": "yes",
            "random_seed": True,
        },
        "evaluation": {"evaluators": ["eval_a"]},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Design randomize_run_order must be a boolean." in result.errors
    assert "Design random_seed must be an integer when provided." in result.errors


def test_validation_rejects_empty_design_selections() -> None:
    payload = {
        "name": "empty_design_selection",
        "cases": [{"id": "case_a", "prompt": "A"}],
        "models": [{"id": "model_a", "provider": "openai", "model": "gpt-5.5", "params": {}}],
        "system_prompts": ["prompt_a"],
        "warmers": ["warmer_a"],
        "design": {
            "type": "full_factorial",
            "replicates": 1,
            "cases": [],
        },
        "evaluation": {"evaluators": ["eval_a"]},
    }

    result = validate_manifest_payload(payload)

    assert result.valid is False
    assert "Design case selection must include at least one item." in result.errors


def test_expansion_is_deterministic_and_attempts_include_replicate_ids() -> None:
    manifest = load_manifest_file(EXAMPLE_MANIFEST)

    first = expand_manifest(manifest).model_dump(mode="json")
    second = expand_manifest(manifest).model_dump(mode="json")

    assert first["runs"] == second["runs"]
    assert {attempt["replicate_index"] for attempt in first["runs"][0]["attempts"]} == {0, 1}

    attempt_ids = [attempt["attempt_id"] for run in first["runs"] for attempt in run["attempts"]]
    assert len(set(attempt_ids)) == first["run_attempts"]
    assert first["runs"][0]["attempts"][0]["attempt_id"] != first["runs"][0]["run_id"]


def test_cli_validate_and_expand_json() -> None:
    runner = CliRunner()

    validate_result = runner.invoke(cli_app, ["validate", str(EXAMPLE_MANIFEST)])
    assert validate_result.exit_code == 0
    assert "valid: true" in validate_result.stdout

    expand_result = runner.invoke(cli_app, ["expand", str(EXAMPLE_MANIFEST), "--format", "json"])
    assert expand_result.exit_code == 0
    payload = json.loads(expand_result.stdout)
    assert payload["logical_runs"] == 16
    assert payload["run_attempts"] == 32
    assert len(payload["runs"]) == 16


def test_load_manifest_file_reports_malformed_yaml(tmp_path: Path) -> None:
    manifest_path = tmp_path / "broken.yaml"
    manifest_path.write_text("name: [unterminated\n", encoding="utf-8")

    _assert_load_manifest_error_startswith(manifest_path, "Manifest file could not be parsed:")
    _assert_cli_validate_failure(manifest_path, "- Manifest file could not be parsed:")


def test_cli_validate_reports_missing_manifest_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.yaml"

    _assert_cli_validate_failure(missing_path, f"- Manifest file not found: {missing_path}")


def test_cli_validate_reports_invalid_manifest_encoding(tmp_path: Path) -> None:
    manifest_path = tmp_path / "invalid-encoding.yaml"
    manifest_path.write_bytes(b"name: \xff\n")

    _assert_load_manifest_error_startswith(
        manifest_path,
        f"Manifest file could not be read: {manifest_path}:",
    )
    _assert_cli_validate_failure(
        manifest_path,
        f"- Manifest file could not be read: {manifest_path}:",
    )


def test_api_validate_and_preview_routes_accept_manifest_body() -> None:
    manifest = load_manifest_file(EXAMPLE_MANIFEST)
    client = TestClient(api_app)

    validate_response = client.post("/manifests/validate", json=manifest.model_dump(mode="json"))
    preview_response = client.post("/manifests/preview", json=manifest.model_dump(mode="json"))

    assert validate_response.status_code == 200
    assert validate_response.json() == {"valid": True, "errors": []}
    assert preview_response.status_code == 200
    assert preview_response.json()["logical_runs"] == 16
