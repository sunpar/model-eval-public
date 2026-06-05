from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.persistence.models import Base, Experiment, Run, RunAttempt


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


def test_phase6_library_endpoints_create_and_list_structured_resources(
    client: TestClient,
) -> None:
    project = "phase6"

    case_response = client.post(
        f"/projects/{project}/library/cases",
        json={"slug": "case_a", "name": "Case A", "prompt": "Write a memo."},
    )
    assert case_response.status_code == 201
    assert case_response.json()["snapshot"]["prompt"] == "Write a memo."

    system_prompt_response = client.post(
        f"/projects/{project}/library/system-prompts",
        json={"slug": "system_a", "name": "System A", "prompt": "Be concise."},
    )
    assert system_prompt_response.status_code == 201

    warmer_response = client.post(
        f"/projects/{project}/library/warmers",
        json={
            "slug": "warmer_a",
            "name": "Warmer A",
            "domain": "finance",
            "user_level": "novice",
            "intent": "Understand the request before answering.",
            "messages": [{"role": "user", "content": "I am new to this topic."}],
            "tags": ["finance", "novice"],
            "version_note": "Initial novice finance warmer.",
        },
    )
    assert warmer_response.status_code == 201
    assert warmer_response.json()["version_note"] == "Initial novice finance warmer."
    assert warmer_response.json()["snapshot"]["version_note"] == "Initial novice finance warmer."

    model_response = client.post(
        f"/projects/{project}/library/model-configs",
        json={
            "slug": "model_a",
            "name": "Model A",
            "provider": "openai",
            "model": "gpt-5.5",
            "reasoning_level": "medium",
            "temperature": 0.2,
            "max_output_tokens": 800,
            "capability_flags": {"json_mode": True},
            "raw_provider_params": {"api_key": "secret", "temperature": 0.2},
        },
    )
    assert model_response.status_code == 201
    assert model_response.json()["reasoning_level"] == "medium"
    assert model_response.json()["max_output_tokens"] == 800
    assert model_response.json()["raw_provider_params"]["api_key"] == "[redacted]"

    evaluator_response = client.post(
        f"/projects/{project}/library/evaluators",
        json={
            "slug": "eval_a",
            "name": "Evaluator A",
            "evaluator_type": "deterministic",
            "definition": {"criterion": "has_summary"},
        },
    )
    assert evaluator_response.status_code == 201

    artifact_response = client.post(
        f"/projects/{project}/library/artifacts",
        json={
            "slug": "artifact_a",
            "name": "Artifact A",
            "artifact_type": "text",
            "uri": "file://artifact-a.txt",
            "input_mode": "pdf_text",
            "metadata": {"source": "fixture"},
        },
    )
    assert artifact_response.status_code == 201

    warmers = client.get(f"/projects/{project}/library/warmers")
    assert warmers.status_code == 200
    assert warmers.json()[0]["slug"] == "warmer_a"
    assert warmers.json()[0]["messages"] == [
        {"role": "user", "content": "I am new to this topic."}
    ]

    models = client.get(f"/projects/{project}/library/model-configs")
    assert models.status_code == 200
    assert models.json()[0]["raw_provider_params"]["api_key"] == "[redacted]"


def test_preview_rejects_non_mapping_controls_without_server_error(client: TestClient) -> None:
    response = client.post(
        "/projects/phase6/experiments/preview",
        json={
            "id": "bad_controls",
            "name": "Bad controls",
            "cases": [{"id": "case", "prompt": "Write memo"}],
            "models": [{"id": "model", "provider": "openai", "model": "gpt-5.5"}],
            "system_prompts": [{"id": "system", "prompt": "Be concise."}],
            "warmers": [{"id": "none", "messages": []}],
            "design": {"replicates": 1},
            "controls": "not-a-mapping",
            "evaluation": {"evaluators": []},
        },
    )

    assert response.status_code == 422


def test_experiment_builder_preview_save_and_queue_from_library_refs(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = "phase6"
    _seed_minimal_library(client, project)

    manifest = {
        "id": "phase6_exp",
        "name": "Phase 6 Experiment",
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["system_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 2},
        "evaluation": {"evaluators": ["eval_a"]},
        "controls": {},
    }

    preview = client.post(f"/projects/{project}/experiments/preview", json=manifest)
    assert preview.status_code == 200
    assert preview.json()["logical_runs"] == 1
    assert preview.json()["run_attempts"] == 2
    assert preview.json()["estimated_cost_usd"] == 0.0

    draft = client.post(f"/projects/{project}/experiments/drafts", json=manifest)
    assert draft.status_code == 201
    draft_payload = draft.json()
    assert draft_payload["slug"] == "phase6_exp"
    assert draft_payload["status"] == "draft"
    assert draft_payload["preview"]["run_attempts"] == 2
    assert draft_payload["controls_snapshot"]["local_only"] is True

    experiment = session.scalar(select(Experiment).where(Experiment.slug == "phase6_exp"))
    assert experiment is not None
    runs = session.scalars(select(Run).where(Run.experiment_id == experiment.id)).all()
    attempts = session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
    ).all()
    assert len(runs) == 1
    assert len(attempts) == 2
    assert runs[0].run_snapshot["warmer"]["version_note"] == "Initial warmer."

    enqueued: list[int] = []

    def fake_enqueue(experiment_id: int) -> list[dict[str, Any]]:
        enqueued.append(experiment_id)
        return [{"id": "expand"}, {"id": "execute"}, {"id": "evaluate"}, {"id": "export"}]

    monkeypatch.setattr(api_module, "enqueue_experiment_execution", fake_enqueue)
    queued = client.post(f"/projects/{project}/experiments/{experiment.id}/queue")
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert queued.json()["queued_jobs"] == 4
    assert enqueued == [experiment.id]

    duplicate_queue = client.post(f"/projects/{project}/experiments/{experiment.id}/queue")
    assert duplicate_queue.status_code == 409
    assert duplicate_queue.json()["detail"] == "Only draft experiments can be queued."
    assert enqueued == [experiment.id]


def test_queue_failure_preserves_draft_status(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = "phase6"
    _seed_minimal_library(client, project)
    manifest = {
        "id": "phase6_queue_failure",
        "name": "Phase 6 Queue Failure",
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["system_a"],
        "warmers": ["warmer_a"],
        "evaluation": {"evaluators": ["eval_a"]},
    }
    draft = client.post(f"/projects/{project}/experiments/drafts", json=manifest)
    assert draft.status_code == 201
    experiment = session.scalar(select(Experiment).where(Experiment.slug == "phase6_queue_failure"))
    assert experiment is not None

    def fail_enqueue(experiment_id: int) -> list[dict[str, Any]]:
        raise RuntimeError(f"redis unavailable for {experiment_id}")

    monkeypatch.setattr(api_module, "enqueue_experiment_execution", fail_enqueue)
    queued = client.post(f"/projects/{project}/experiments/{experiment.id}/queue")

    assert queued.status_code == 503
    session.refresh(experiment)
    assert experiment.status == "draft"


def test_duplicate_experiment_draft_returns_conflict(client: TestClient) -> None:
    project = "phase6"
    _seed_minimal_library(client, project)
    manifest = {
        "id": "phase6_duplicate",
        "name": "Phase 6 Duplicate",
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["system_a"],
        "warmers": ["warmer_a"],
        "evaluation": {"evaluators": ["eval_a"]},
    }

    assert client.post(f"/projects/{project}/experiments/drafts", json=manifest).status_code == 201
    duplicate = client.post(f"/projects/{project}/experiments/drafts", json=manifest)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Resource already exists."


def test_update_draft_rebuilds_snapshots_and_attempts(client: TestClient, session: Session) -> None:
    project = "phase6"
    _seed_minimal_library(client, project)
    manifest = {
        "id": "phase6_update",
        "name": "Phase 6 Update",
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["system_a"],
        "warmers": ["warmer_a"],
        "evaluation": {"evaluators": ["eval_a"]},
        "design": {"type": "full_factorial", "replicates": 1},
    }

    draft = client.post(f"/projects/{project}/experiments/drafts", json=manifest)
    assert draft.status_code == 201
    experiment = session.scalar(select(Experiment).where(Experiment.slug == "phase6_update"))
    assert experiment is not None
    assert len(session.scalars(select(Run).where(Run.experiment_id == experiment.id)).all()) == 1

    updated_manifest = {
        **manifest,
        "design": {"type": "full_factorial", "replicates": 3},
    }
    updated = client.put(
        f"/projects/{project}/experiments/{experiment.id}/draft",
        json=updated_manifest,
    )

    assert updated.status_code == 200
    assert updated.json()["preview"]["run_attempts"] == 3
    session.refresh(experiment)
    assert experiment.design_snapshot["replicates"] == 3
    runs = session.scalars(select(Run).where(Run.experiment_id == experiment.id)).all()
    attempts = session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
    ).all()
    assert len(runs) == 1
    assert len(attempts) == 3


def test_experiment_draft_reports_manifest_and_library_errors(client: TestClient) -> None:
    response = client.post(
        "/projects/phase6/experiments/drafts",
        json={
            "name": "Invalid Experiment",
            "cases": [],
            "models": ["missing_model"],
            "system_prompts": ["missing_system"],
            "warmers": ["missing_warmer"],
        },
    )

    assert response.status_code == 422
    assert "Manifest dimension 'cases' must include at least one item." in response.json()["detail"]


def _seed_minimal_library(client: TestClient, project: str) -> None:
    assert client.post(
        f"/projects/{project}/library/cases",
        json={"slug": "case_a", "name": "Case A", "prompt": "Write a memo."},
    ).status_code == 201
    assert client.post(
        f"/projects/{project}/library/system-prompts",
        json={"slug": "system_a", "name": "System A", "prompt": "Be concise."},
    ).status_code == 201
    assert client.post(
        f"/projects/{project}/library/warmers",
        json={
            "slug": "warmer_a",
            "name": "Warmer A",
            "intent": "Ask one clarifying question.",
            "messages": [{"role": "user", "content": "I need help."}],
            "version_note": "Initial warmer.",
        },
    ).status_code == 201
    assert client.post(
        f"/projects/{project}/library/model-configs",
        json={
            "slug": "model_a",
            "name": "Model A",
            "provider": "openai",
            "model": "gpt-5.5",
            "reasoning_level": "low",
            "temperature": 0.1,
            "max_output_tokens": 500,
            "raw_provider_params": {"temperature": 0.1},
        },
    ).status_code == 201
    assert client.post(
        f"/projects/{project}/library/evaluators",
        json={
            "slug": "eval_a",
            "name": "Evaluator A",
            "evaluator_type": "deterministic",
            "definition": {"criterion": "memo"},
        },
    ).status_code == 201
