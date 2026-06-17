from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from model_eval_api import main as api_module
from model_eval_api.headless import export_experiment
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import (
    Base,
    Case,
    Evaluator,
    MetricAdapterConfig,
    ModelConfig,
    SystemPrompt,
)
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_project,
    create_workspace,
    record_score,
)
from model_eval_api.promptfoo import (
    export_experiment_to_promptfoo,
    persist_promptfoo_import,
    preview_promptfoo_import,
)
from model_eval_cli import main as cli_module


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        yield db


@pytest.fixture()
def client(session: Session) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        yield TestClient(api_module.app)
    finally:
        api_module.app.dependency_overrides.clear()


def test_promptfoo_preview_maps_prompts_providers_tests_and_assertions(
    tmp_path: Path,
) -> None:
    config_path = _promptfoo_config(tmp_path)

    preview = preview_promptfoo_import(config_path)

    assert preview.manifest.name == "Copper promptfoo smoke"
    assert [case.id for case in preview.manifest.cases] == ["chile_copper_disruption"]
    assert preview.manifest.cases[0].prompt == "Chile copper disruption"
    assert preview.manifest.cases[0].model_extra["variables"] == {
        "topic": "Chile copper disruption",
        "reference": "grid spend",
    }
    assert [prompt.id for prompt in preview.manifest.system_prompts] == [
        "investment_memo_prompt"
    ]
    assert preview.manifest.system_prompts[0].prompt == (
        "You are a careful investment analyst. Answer {{topic}}."
    )
    assert [model.id for model in preview.manifest.models] == ["openai_gpt_5_5"]
    assert preview.manifest.models[0].provider == "openai"
    assert preview.manifest.models[0].model == "gpt-5.5"
    assert preview.manifest.models[0].raw_provider_params == {
        "max_tokens": 600,
        "promptfoo_provider_id": "openai:gpt-5.5",
        "reasoning_effort": "high",
        "temperature": 0.2,
    }
    assert [evaluator.id for evaluator in preview.manifest.evaluation.evaluators] == [
        "promptfoo_not_empty",
        "promptfoo_json_schema",
    ]
    assert preview.manifest.evaluation.evaluators[0].definition == {
        "kind": "no_empty_output",
        "criterion": "promptfoo_not_empty",
        "promptfoo_assertion_type": "not-empty",
    }
    assert preview.library_records["metric_adapter_configs"] == [
        {
            "slug": "promptfoo_answer_relevance",
            "name": "Promptfoo Answer Relevance",
            "adapter_kind": "answer_relevance",
            "adapter_version": "promptfoo-1",
            "required_inputs": ["answer_text", "reference_answers"],
            "output_schema": {"type": "object"},
            "capability_metadata": {
                "promptfoo_assertion_type": "answer-relevance",
                "threshold": 0.7,
            },
            "local_only": True,
        }
    ]
    assert preview.manifest.controls.max_parallel_requests == 3
    assert preview.run_preview.logical_runs == 1
    assert preview.run_preview.run_attempts == 1
    assert {
        (warning["code"], warning["path"])
        for warning in preview.warnings
    } >= {
        ("ambiguous_prompt_shape", "$.prompts[0]"),
        ("unsupported_option", "$.options.timeout"),
        ("unsupported_assertion", "$.tests[0].assert[3]"),
    }


def test_promptfoo_persistence_uses_library_versioning_rules(
    session: Session,
    tmp_path: Path,
) -> None:
    project = create_project(
        session,
        workspace=create_workspace(session, slug="default", name="Default"),
        slug="promptfoo",
        name="Promptfoo",
    )
    preview = preview_promptfoo_import(_promptfoo_config(tmp_path))

    first = persist_promptfoo_import(session, project=project, preview=preview)
    session.commit()
    second = persist_promptfoo_import(session, project=project, preview=preview)
    session.commit()

    assert first["created"] == {
        "cases": 1,
        "system_prompts": 1,
        "warmers": 1,
        "model_configs": 1,
        "evaluators": 2,
        "metric_adapter_configs": 1,
    }
    assert second["created"] == first["created"]

    models = session.scalars(
        select(ModelConfig)
        .where(ModelConfig.slug == "openai_gpt_5_5")
        .order_by(ModelConfig.version)
    ).all()
    assert [model.version for model in models] == [1, 2]
    assert models[0].raw_provider_params["promptfoo_provider_id"] == "openai:gpt-5.5"
    assert models[0].raw_provider_params["temperature"] == 0.2

    assert session.scalars(select(Case).where(Case.slug == "chile_copper_disruption")).all()
    assert session.scalars(
        select(SystemPrompt).where(SystemPrompt.slug == "investment_memo_prompt")
    ).all()
    assert session.scalars(select(Evaluator).where(Evaluator.slug == "promptfoo_not_empty")).all()
    adapters = session.scalars(
        select(MetricAdapterConfig)
        .where(MetricAdapterConfig.slug == "promptfoo_answer_relevance")
        .order_by(MetricAdapterConfig.version)
    ).all()
    assert [adapter.version for adapter in adapters] == [1, 2]


def test_promptfoo_preview_keeps_distinct_supported_assertion_configs(tmp_path: Path) -> None:
    config_path = tmp_path / "promptfoo-distinct.yaml"
    config_path.write_text(
        """
description: Distinct assertions
prompts:
  - Summarize {{topic}}.
providers:
  - openai:gpt-5.5
tests:
  - description: Case A
    vars:
      topic: copper
    assert:
      - type: is-json
        value:
          type: object
          required: [summary]
      - type: answer-relevance
        threshold: 0.6
  - description: Case B
    vars:
      topic: lithium
    assert:
      - type: is-json
        value:
          type: object
          required: [risks]
      - type: answer-relevance
        threshold: 0.9
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    json_schema_evaluators = [
        evaluator
        for evaluator in preview.manifest.evaluation.evaluators
        if evaluator.definition["kind"] == "json_schema"
    ]
    assert [evaluator.id for evaluator in json_schema_evaluators] == [
        "promptfoo_json_schema",
        "promptfoo_json_schema_2",
    ]
    assert [evaluator.definition["schema"]["required"] for evaluator in json_schema_evaluators] == [
        ["summary"],
        ["risks"],
    ]
    adapter_configs = preview.library_records["metric_adapter_configs"]
    assert [config["slug"] for config in adapter_configs] == [
        "promptfoo_answer_relevance",
        "promptfoo_answer_relevance_2",
    ]
    assert [config["capability_metadata"]["threshold"] for config in adapter_configs] == [
        0.6,
        0.9,
    ]


def test_promptfoo_preview_applies_default_test_vars_and_warns_on_default_options(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "promptfoo-defaults.yaml"
    config_path.write_text(
        """
description: Defaults
prompts:
  - Summarize {{topic}}.
providers:
  - openai:gpt-5.5
defaultTest:
  vars:
    topic: copper defaults
  options:
    provider: openai:gpt-5.5
  assert:
    - type: not-empty
tests:
  - description: Uses defaults
    vars:
      reference: grid spend
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert preview.manifest.cases[0].prompt == "copper defaults"
    assert preview.manifest.cases[0].model_extra["variables"] == {
        "topic": "copper defaults",
        "reference": "grid spend",
    }
    assert [evaluator.id for evaluator in preview.manifest.evaluation.evaluators] == [
        "promptfoo_not_empty"
    ]
    assert ("unsupported_option", "$.defaultTest.options.provider") in {
        (warning["code"], warning["path"]) for warning in preview.warnings
    }


def test_promptfoo_preview_warns_when_vars_are_malformed(tmp_path: Path) -> None:
    config_path = tmp_path / "promptfoo-bad-vars.yaml"
    config_path.write_text(
        """
description: Bad vars
prompts:
  - Summarize {{topic}}.
providers:
  - openai:gpt-5.5
defaultTest:
  vars:
tests:
  - description: Bad case vars
    vars: 7
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert preview.manifest.cases[0].prompt == "Bad case vars"
    assert preview.manifest.cases[0].model_extra["variables"] == {}
    assert {
        ("unsupported_default_test_vars", "$.defaultTest.vars"),
        ("unsupported_test_vars", "$.tests[0].vars"),
    }.issubset({(warning["code"], warning["path"]) for warning in preview.warnings})


def test_promptfoo_preview_warns_when_vars_use_unsupported_external_sources(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "promptfoo-file-vars.yaml"
    config_path.write_text(
        """
description: File vars
prompts:
  - Summarize {{topic}}.
providers:
  - openai:gpt-5.5
defaultTest:
  vars: file://defaults.yaml
tests:
  - description: File case vars
    vars:
      - file://vars-a.yaml
      - file://vars-b.yaml
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert preview.manifest.cases[0].prompt == "File case vars"
    assert preview.manifest.cases[0].model_extra["variables"] == {}
    assert {
        ("unsupported_default_test_vars_source", "$.defaultTest.vars"),
        ("unsupported_test_vars_source", "$.tests[0].vars"),
    }.issubset({(warning["code"], warning["path"]) for warning in preview.warnings})


def test_promptfoo_preview_accepts_targets_and_evaluate_options_with_colon_models(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "promptfoo-targets.yaml"
    config_path.write_text(
        """
description: Targets alias
prompts:
  - Summarize {{topic}}.
targets:
  - openai:ft:gpt-5.5:custom
evaluateOptions:
  maxConcurrency: 7
tests:
  - description: Case A
    vars:
      topic: copper
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert preview.manifest.models[0].provider == "openai"
    assert preview.manifest.models[0].model == "ft:gpt-5.5:custom"
    assert preview.manifest.controls.max_parallel_requests == 7
    assert {
        (warning["code"], warning["path"]) for warning in preview.warnings
    }.isdisjoint(
        {
            ("unsupported_top_level_field", "$.targets"),
            ("unsupported_top_level_field", "$.evaluateOptions"),
        }
    )


def test_promptfoo_preview_warns_on_boolean_max_concurrency(tmp_path: Path) -> None:
    config_path = tmp_path / "promptfoo-bool-concurrency.yaml"
    config_path.write_text(
        """
description: Bool concurrency
prompts:
  - Summarize {{topic}}.
providers:
  - openai:gpt-5.5
options:
  maxConcurrency: true
tests:
  - description: Case A
    vars:
      topic: copper
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert preview.manifest.controls.max_parallel_requests is None
    assert ("unsupported_option", "$.options.maxConcurrency") in {
        (warning["code"], warning["path"]) for warning in preview.warnings
    }


def test_promptfoo_preview_missing_targets_warning_uses_targets_path(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "promptfoo-empty-targets.yaml"
    config_path.write_text(
        """
description: Empty targets alias
prompts:
  - Summarize {{topic}}.
targets: []
tests:
  - description: Case A
    vars:
      topic: copper
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert ("missing_provider", "$.targets") in {
        (warning["code"], warning["path"]) for warning in preview.warnings
    }


def test_promptfoo_preview_uses_prompt_cases_and_default_assertions_without_tests(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "promptfoo-prompt-case.yaml"
    config_path.write_text(
        """
description: Prompt case only
prompts:
  - id: user_case
    messages:
      - role: user
        content: Explain copper demand.
providers:
  - openai:gpt-5.5
defaultTest:
  assert:
    - type: not-empty
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert [case.id for case in preview.manifest.cases] == ["user_case"]
    assert [evaluator.id for evaluator in preview.manifest.evaluation.evaluators] == [
        "promptfoo_not_empty"
    ]
    assert ("missing_tests", "$.tests") not in {
        (warning["code"], warning["path"]) for warning in preview.warnings
    }


def test_promptfoo_preview_generates_stable_unique_case_ids_for_repeated_descriptions(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "promptfoo-duplicate-descriptions.yaml"
    config_path.write_text(
        """
description: Duplicate descriptions
prompts:
  - Summarize {{topic}}.
providers:
  - openai:gpt-5.5
tests:
  - description: Shared case
    vars:
      topic: copper
  - description: Shared case
    vars:
      topic: lithium
""",
        encoding="utf-8",
    )

    preview = preview_promptfoo_import(config_path)

    assert [case.id for case in preview.manifest.cases] == ["shared_case", "shared_case_2"]
    assert ("duplicate_case_id", "$.tests[1].description") in {
        (warning["code"], warning["path"]) for warning in preview.warnings
    }


def test_promptfoo_cli_emits_manifest_preview_without_provider_execution(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        cli_module.app, ["import", "promptfoo", str(_promptfoo_config(tmp_path))]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["manifest"]["name"] == "Copper promptfoo smoke"
    assert payload["preview"]["run_attempts"] == 1
    assert payload["manifest"]["models"][0]["raw_provider_params"] == {
        "max_tokens": 600,
        "promptfoo_provider_id": "openai:gpt-5.5",
        "reasoning_effort": "high",
        "temperature": 0.2,
    }
    assert payload["warnings"]
    assert "execution" not in payload


def test_promptfoo_cli_rejects_malformed_yaml_with_single_line_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "broken-promptfoo.yaml"
    config_path.write_text(":\n  - broken", encoding="utf-8")

    result = CliRunner().invoke(cli_module.app, ["import", "promptfoo", str(config_path)])

    assert result.exit_code == 1
    assert "could not be parsed" in result.stderr
    assert len(result.stderr.splitlines()) == 1


def test_promptfoo_cli_persist_writes_library_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "promptfoo-cli.sqlite3"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None

    result = CliRunner().invoke(
        cli_module.app,
        [
            "import",
            "promptfoo",
            str(_promptfoo_config(tmp_path)),
            "--persist",
            "--project",
            "promptfoo_cli",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["persisted"]["created"]["model_configs"] == 1
    with cli_module.database.get_session_factory()() as db:
        assert db.scalar(select(ModelConfig).where(ModelConfig.slug == "openai_gpt_5_5")) is not None
        assert (
            db.scalar(
                select(MetricAdapterConfig).where(
                    MetricAdapterConfig.slug == "promptfoo_answer_relevance"
                )
            )
            is not None
        )
    cli_module.database._engine = None
    cli_module.database._session_factory = None


def test_promptfoo_export_round_trip_compatible_config_has_no_warnings(
    session: Session,
) -> None:
    experiment = _promptfoo_export_experiment(session)

    exported = export_experiment_to_promptfoo(experiment)

    assert exported.warnings == []
    payload = yaml.safe_load(exported.content)
    assert payload == {
        "description": "Promptfoo Export",
        "prompts": [{"id": "system_a", "raw": "Answer {{prompt}} carefully."}],
        "providers": [
            {
                "id": "openai:gpt-5.5",
                "config": {
                    "max_tokens": 600,
                    "temperature": 0.2,
                },
            }
        ],
        "options": {"maxConcurrency": 2},
        "tests": [
            {
                "description": "Case A",
                "vars": {"prompt": "Chile copper disruption"},
                "assert": [
                    {"type": "not-empty"},
                    {"type": "is-json", "value": {"type": "object"}},
                ],
            }
        ],
    }


def test_promptfoo_compatible_json_export_includes_frontier_mapping_fields(
    session: Session,
) -> None:
    experiment = _promptfoo_export_experiment(session)
    attempt = experiment.runs[0].attempts[0]
    attempt.run.status = "complete"
    attempt.status = "succeeded"
    attempt.response_payload = {"output_text": "answer"}
    attempt.cost_usd = 0.12
    attempt.latency_ms = 900
    record_score(
        session,
        run_attempt=attempt,
        type="pass_fail",
        evaluator_type="human",
        criterion="blind_pairwise_pass_fail",
        value={"passed": True},
    )
    session.commit()

    exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    frontier_row = exported_json["analytics"]["cost_quality_frontier"][0]

    assert frontier_row["promptfoo_provider_id"] == "openai:gpt-5.5"
    assert frontier_row["promptfoo_prompt_id"] == "system_a"
    assert frontier_row["promptfoo_test_description"] == "Case A"
    assert frontier_row["promptfoo_assertion_types"] == ["not-empty", "is-json"]


def test_promptfoo_export_surfaces_lossy_mappings(session: Session) -> None:
    experiment = _promptfoo_export_experiment(
        session,
        extra_warmer=True,
        unsupported_evaluator=True,
    )

    exported = export_experiment_to_promptfoo(experiment)

    assert {
        (warning["code"], warning["path"])
        for warning in exported.warnings
    } >= {
        ("lossy_warmer_mapping", "$.warmers.analyst"),
        ("unsupported_evaluator_mapping", "$.evaluation.evaluators.unsupported_eval"),
    }


def test_promptfoo_export_preserves_case_variables_and_warns_on_prompt_ref(
    session: Session,
) -> None:
    experiment = _promptfoo_export_experiment(
        session,
        case_variables={"topic": "Chile copper", "reference": "grid spend"},
        prompt_ref_case=True,
    )

    exported = export_experiment_to_promptfoo(experiment)

    payload = yaml.safe_load(exported.content)
    tests_by_description = {item["description"]: item for item in payload["tests"]}
    assert tests_by_description["Case A"]["vars"] == {
        "topic": "Chile copper",
        "reference": "grid spend",
    }
    assert tests_by_description["Case Ref"]["vars"] == {}
    assert {
        (warning["code"], warning["path"])
        for warning in exported.warnings
    } >= {("lossy_case_prompt_mapping", "$.cases.case_ref")}


def test_promptfoo_export_warns_for_array_vars_and_falsey_unsupported_controls(
    session: Session,
) -> None:
    experiment = _promptfoo_export_experiment(
        session,
        case_variables={"topics": ["copper", "grid"], "reference": "grid spend"},
        controls={
            "max_parallel_requests": 2,
            "truncation_policy": "fail_on_over_budget",
            "local_only": False,
            "max_total_cost_usd": 0,
        },
    )

    exported = export_experiment_to_promptfoo(experiment)

    assert {
        (warning["code"], warning["path"])
        for warning in exported.warnings
    } >= {
        ("lossy_case_var_expansion", "$.cases.case_a.vars.topics"),
        ("unsupported_control_mapping", "$.controls.local_only"),
        ("unsupported_control_mapping", "$.controls.max_total_cost_usd"),
    }


def test_promptfoo_export_is_available_from_headless_and_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "promptfoo-export.sqlite3"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    cli_module.headless.ensure_database_schema()
    with cli_module.database.get_session_factory()() as db:
        experiment = _promptfoo_export_experiment(db)
        db.commit()
        experiment_id = str(experiment.id)

    with cli_module.database.get_session_factory()() as db:
        direct_export = export_experiment(db, experiment_id, "promptfoo")
    cli_result = CliRunner().invoke(
        cli_module.app,
        ["export", experiment_id, "--format", "promptfoo"],
    )

    assert yaml.safe_load(direct_export)["description"] == "Promptfoo Export"
    assert cli_result.exit_code == 0
    assert yaml.safe_load(cli_result.stdout)["providers"][0]["id"] == "openai:gpt-5.5"
    cli_module.database._engine = None
    cli_module.database._session_factory = None


def test_promptfoo_api_import_preview_and_export_surface_warnings(
    client: TestClient,
    session: Session,
) -> None:
    experiment = _promptfoo_export_experiment(session, unsupported_evaluator=True)
    session.commit()

    preview_response = client.post(
        "/projects/default/imports/promptfoo/preview",
        json={"content": _promptfoo_config_text()},
    )
    export_response = client.get(f"/monitor/experiments/{experiment.id}/exports?format=promptfoo")

    assert preview_response.status_code == 200
    assert preview_response.json()["warnings"]
    assert preview_response.json()["preview"]["run_attempts"] == 1
    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload["format"] == "promptfoo"
    assert "prompts:" in export_payload["content"]
    assert any(
        warning["code"] == "unsupported_evaluator_mapping"
        for warning in export_payload["warnings"]
    )


def test_promptfoo_api_import_preview_rejects_malformed_yaml(client: TestClient) -> None:
    response = client.post(
        "/projects/default/imports/promptfoo/preview",
        json={"content": ":\n  - broken"},
    )

    assert response.status_code == 422
    assert "could not be parsed" in response.json()["detail"]
    assert "\n" not in response.json()["detail"]


def _promptfoo_config(tmp_path: Path) -> Path:
    path = tmp_path / "promptfoo.yaml"
    path.write_text(_promptfoo_config_text(), encoding="utf-8")
    return path


def _promptfoo_config_text() -> str:
    return """
description: Copper promptfoo smoke
prompts:
  - id: investment_memo_prompt
    raw: You are a careful investment analyst. Answer {{topic}}.
providers:
  - id: openai:gpt-5.5
    config:
      temperature: 0.2
      max_tokens: 600
      reasoning_effort: high
options:
  maxConcurrency: 3
  timeout: 60000
tests:
  - description: Chile copper disruption
    vars:
      topic: Chile copper disruption
      reference: grid spend
    assert:
      - type: not-empty
      - type: is-json
        value:
          type: object
      - type: answer-relevance
        threshold: 0.7
      - type: javascript
        value: output.includes("copper")
"""


def _promptfoo_export_experiment(
    session: Session,
    *,
    case_variables: dict[str, object] | None = None,
    controls: dict[str, object] | None = None,
    extra_warmer: bool = False,
    prompt_ref_case: bool = False,
    unsupported_evaluator: bool = False,
):
    workspace = create_workspace(session, slug="promptfoo-export", name="Promptfoo Export")
    project = create_project(
        session,
        workspace=workspace,
        slug="promptfoo_export",
        name="Promptfoo Export",
    )
    warmers = [{"id": "none", "messages": []}]
    if extra_warmer:
        warmers.append(
            {
                "id": "analyst",
                "messages": [{"role": "user", "content": "Use valuation context."}],
            }
        )
    evaluators = [
        {
            "id": "promptfoo_not_empty",
            "type": "deterministic",
            "definition": {
                "kind": "no_empty_output",
                "criterion": "promptfoo_not_empty",
                "promptfoo_assertion_type": "not-empty",
            },
        },
        {
            "id": "promptfoo_json_schema",
            "type": "deterministic",
            "definition": {
                "kind": "json_schema",
                "criterion": "promptfoo_json_schema",
                "schema": {"type": "object"},
                "promptfoo_assertion_type": "is-json",
            },
        },
    ]
    if unsupported_evaluator:
        evaluators.append(
            {
                "id": "unsupported_eval",
                "type": "deterministic",
                "definition": {"kind": "required_sections", "sections": ["risks"]},
            }
        )
    cases: list[dict[str, object]] = [{"id": "case_a", "prompt": "Chile copper disruption"}]
    if case_variables is not None:
        cases[0]["variables"] = case_variables
    if prompt_ref_case:
        cases.append({"id": "case_ref", "prompt_ref": "private/case-ref.txt"})
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": "promptfoo_export",
                "name": "Promptfoo Export",
                "cases": cases,
                "models": [
                    {
                        "id": "openai_gpt",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "params": {
                            "promptfoo_provider_id": "openai:gpt-5.5",
                            "temperature": 0.2,
                            "max_tokens": 600,
                        },
                    }
                ],
                "system_prompts": [
                    {"id": "system_a", "prompt": "Answer {{prompt}} carefully."}
                ],
                "warmers": warmers,
                "design": {"replicates": 1},
                "controls": controls or {"max_parallel_requests": 2},
                "evaluation": {"evaluators": evaluators},
            }
        ),
    )
    experiment.status = "complete"
    return experiment
