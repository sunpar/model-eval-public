from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from model_eval_api import headless
from model_eval_api import main as api_module
from model_eval_api.persistence import database
from model_eval_api.persistence.models import Base
from model_eval_api.persistence import repositories
from model_eval_api.persistence.repositories import (
    create_case,
    create_conversation_warmer,
    create_evaluator,
    create_model_config,
    create_project,
    create_system_prompt,
    create_workspace,
)
from model_eval_cli import main as cli_module


def test_benchmark_suite_snapshot_locks_membership_and_excludes_archived_cases(
    session: Session,
) -> None:
    project = _seed_suite_library(session)
    suite = repositories.create_benchmark_suite(
        session,
        project=project,
        slug="copper-suite",
        name="Copper suite",
        case_ids=["dev_case", "validation_case", "archived_split_case", "archived_flag_case"],
        model_config_ids=["model_a"],
        system_prompt_ids=["system_a"],
        warmer_ids=["none"],
        evaluator_ids=["sections_check"],
        controls={"replicates": 2, "local_only": True},
    )
    session.commit()

    preview = repositories.preview_benchmark_suite(session, suite=suite, split="dev")

    assert preview["preview"].logical_runs == 1
    assert preview["preview"].run_attempts == 2
    assert preview["manifest"].model_dump(mode="json")["cases"] == [
        {"id": "dev_case", "prompt": None, "prompt_ref": None, "version": 1}
    ]
    assert preview["manifest"].design.split == "dev"
    assert preview["suite_snapshot"]["version"] == 1
    assert [case["id"] for case in preview["suite_snapshot"]["cases"]] == ["dev_case"]
    assert preview["suite_snapshot"]["cases"][0]["split"] == "dev"


def test_benchmark_suite_rerun_creates_reproducible_experiment_from_snapshot(
    session: Session,
) -> None:
    project = _seed_suite_library(session)
    repositories.create_benchmark_suite(
        session,
        project=project,
        slug="copper-suite",
        name="Copper suite",
        case_ids=["dev_case", "validation_case"],
        model_config_ids=["model_a"],
        system_prompt_ids=["system_a"],
        warmer_ids=["none"],
        evaluator_ids=["sections_check"],
        controls={"replicates": 1, "local_only": True},
    )
    session.commit()

    first = repositories.run_benchmark_suite(
        session,
        project=project,
        suite_ref="copper-suite",
        split="validation",
        dry_run=True,
        local_only=True,
    )
    second = repositories.run_benchmark_suite(
        session,
        project=project,
        suite_ref="copper-suite",
        split="validation",
        dry_run=True,
        local_only=False,
    )

    assert first["experiment"]["id"] == second["experiment"]["id"]
    assert first["experiment"]["slug"] == "copper-suite_v1_validation_suite_run"
    assert first["experiment"]["status"] == "complete"
    assert first["preview"].logical_runs == 1
    assert first["experiment_record"].case_snapshots["validation_case"]["dataset_split"] == "validation"
    assert first["experiment_record"].manifest_snapshot["controls"]["local_only"] is None
    assert first["experiment_record"].manifest_snapshot["suite"] == {
        "id": "copper-suite",
        "version": 1,
        "split": "validation",
    }


def test_benchmark_suite_run_id_includes_suite_version(session: Session) -> None:
    project = _seed_suite_library(session)
    suite_v1 = repositories.create_benchmark_suite(
        session,
        project=project,
        slug="copper-suite",
        name="Copper suite",
        case_ids=["dev_case"],
        model_config_ids=["model_a"],
        system_prompt_ids=["system_a"],
        warmer_ids=["none"],
        evaluator_ids=["sections_check"],
        controls={"replicates": 1},
    )
    suite_v2 = repositories.create_benchmark_suite(
        session,
        project=project,
        slug="copper-suite",
        name="Copper suite v2",
        case_ids=["dev_case"],
        model_config_ids=["model_a"],
        system_prompt_ids=["system_a"],
        warmer_ids=["none"],
        evaluator_ids=["sections_check"],
        controls={"replicates": 1},
        version=2,
    )
    session.commit()

    assert repositories.benchmark_suite_manifest(
        suite_v1, split="dev"
    ).experiment_id == "copper-suite_v1_dev_suite_run"
    assert repositories.benchmark_suite_manifest(
        suite_v2, split="dev"
    ).experiment_id == "copper-suite_v2_dev_suite_run"


def test_benchmark_suite_rejects_archived_split(session: Session) -> None:
    project = _seed_suite_library(session)
    suite = repositories.create_benchmark_suite(
        session,
        project=project,
        slug="copper-suite",
        name="Copper suite",
        case_ids=["dev_case", "archived_split_case"],
        model_config_ids=["model_a"],
        system_prompt_ids=["system_a"],
        warmer_ids=["none"],
        evaluator_ids=["sections_check"],
        controls={"replicates": 1},
    )
    session.commit()

    with pytest.raises(ValueError, match="archived"):
        repositories.preview_benchmark_suite(session, suite=suite, split="archived")


def test_benchmark_suite_api_create_preview_and_archive(session: Session) -> None:
    _seed_suite_library(session)

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        created = client.post(
            "/projects/suite/library/benchmark-suites",
            json={
                "slug": "api-suite",
                "name": "API suite",
                "case_ids": ["dev_case", "validation_case"],
                "model_config_ids": ["model_a"],
                "system_prompt_ids": ["system_a"],
                "warmer_ids": ["none"],
                "evaluator_ids": ["sections_check"],
                "controls": {"replicates": 1, "local_only": True},
            },
        )
        assert created.status_code == 201
        suite_id = created.json()["id"]
        assert created.json()["snapshot"]["case_count"] == 2

        preview = client.get(
            f"/projects/suite/library/benchmark-suites/{suite_id}/preview?split=validation"
        )
        assert preview.status_code == 200
        assert preview.json()["preview"]["logical_runs"] == 1
        assert preview.json()["suite_snapshot"]["cases"][0]["id"] == "validation_case"

        archived = client.delete(f"/projects/suite/library/benchmark-suites/{suite_id}")
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
    finally:
        api_module.app.dependency_overrides.clear()


def test_cli_suite_run_creates_local_only_experiment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    database_path = tmp_path / "suite.sqlite3"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    headless.ensure_database_schema()
    with database.get_session_factory()() as db:
        project = _seed_suite_library(db, project_slug="default")
        repositories.create_benchmark_suite(
            db,
            project=project,
            slug="cli-suite",
            name="CLI suite",
            case_ids=["dev_case"],
            model_config_ids=["model_a"],
            system_prompt_ids=["system_a"],
            warmer_ids=["none"],
            evaluator_ids=["sections_check"],
            controls={"replicates": 1, "local_only": True},
        )
        db.commit()

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "suite",
            "run",
            "cli-suite",
            "--split",
            "dev",
            "--dry-run",
            "--local-only",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["suite"]["slug"] == "cli-suite"
    assert payload["split"] == "dev"
    assert payload["dry_run"] is True
    assert payload["local_only"] is True
    assert payload["preview"]["logical_runs"] == 1
    cli_module.database._engine = None
    cli_module.database._session_factory = None


def _seed_suite_library(session: Session, *, project_slug: str = "suite"):
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug=project_slug, name="Suite")
    create_case(
        session,
        project=project,
        slug="dev_case",
        name="Dev case",
        prompt="Dev prompt",
        dataset_split="dev",
    )
    create_case(
        session,
        project=project,
        slug="validation_case",
        name="Validation case",
        prompt="Validation prompt",
        dataset_split="validation",
    )
    create_case(
        session,
        project=project,
        slug="archived_split_case",
        name="Archived split case",
        prompt="Archived split prompt",
        dataset_split="archived",
    )
    create_case(
        session,
        project=project,
        slug="archived_flag_case",
        name="Archived flag case",
        prompt="Archived flag prompt",
        dataset_split="dev",
        archived=True,
    )
    create_model_config(
        session,
        project=project,
        slug="model_a",
        name="Model A",
        provider="openai",
        model="gpt-5.5",
        raw_provider_params={"temperature": 0},
    )
    create_system_prompt(
        session,
        project=project,
        slug="system_a",
        name="System A",
        prompt="System prompt",
    )
    create_conversation_warmer(
        session,
        project=project,
        slug="none",
        name="No context",
        messages=[],
    )
    create_evaluator(
        session,
        project=project,
        slug="sections_check",
        name="Sections check",
        evaluator_type="deterministic",
        definition={"required_sections": ["thesis"]},
    )
    session.commit()
    return project


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    with _session_factory()() as db:
        yield db
