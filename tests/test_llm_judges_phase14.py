from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base, LLMJudgeConfig
from model_eval_api.persistence.repositories import (
    archive_llm_judge_config,
    create_case,
    create_conversation_warmer,
    create_evaluator,
    create_experiment_from_manifest,
    create_llm_judge_config,
    create_llm_judge_config_version,
    create_model_config,
    create_project,
    create_system_prompt,
    create_workspace,
)


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


def test_llm_judge_configs_persist_versioned_snapshots(session: Session) -> None:
    project = _seed_project(session)

    judge = create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Score the memo against the rubric.",
        rubric_dimensions=[{"name": "specificity", "scale": "1-5"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
        raw_provider_params={"api_key": "secret", "temperature": 0.1},
        calibration_status="draft",
    )
    session.commit()

    persisted = session.get(LLMJudgeConfig, judge.id)
    assert persisted is not None
    assert persisted.version == 1
    assert persisted.snapshot["judge_prompt"] == "Score the memo against the rubric."
    assert persisted.snapshot["judge_model_config_ref"] == {"id": "judge_model", "version": 1}
    assert persisted.snapshot["raw_provider_params"]["api_key"] == "[redacted]"
    assert persisted.snapshot["calibration_status"] == "draft"


def test_llm_judge_config_duplicate_versions_are_rejected_and_new_versions_increment(
    session: Session,
) -> None:
    project = _seed_project(session)
    with pytest.raises(ValueError, match="LLM judge config reference 'missing_judge'"):
        create_llm_judge_config_version(
            session,
            project=project,
            slug="missing_judge",
            name="Missing Judge v2",
            judge_prompt="Revised prompt.",
            rubric_dimensions=[],
            output_schema=_score_schema(),
            judge_model_config_slug="judge_model",
        )

    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Original prompt.",
        rubric_dimensions=[],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    session.commit()

    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Duplicate Memo Quality Judge",
        judge_prompt="Duplicate prompt.",
        rubric_dimensions=[],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    version_2 = create_llm_judge_config_version(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge v2",
        judge_prompt="Revised prompt.",
        rubric_dimensions=[{"name": "decision_usefulness"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    archived = archive_llm_judge_config(
        session, project=project, slug="memo_quality_judge", version=1
    )
    session.commit()

    assert version_2.version == 2
    assert archived.archived is True


def test_llm_judge_config_validation_rejects_missing_model_and_malformed_schema(
    session: Session,
) -> None:
    project = _seed_project(session)

    with pytest.raises(ValueError, match="Model config reference 'missing_model'"):
        create_llm_judge_config(
            session,
            project=project,
            slug="missing_model_judge",
            name="Missing Model Judge",
            judge_prompt="Score.",
            rubric_dimensions=[],
            output_schema=_score_schema(),
            judge_model_config_slug="missing_model",
        )

    with pytest.raises(ValueError, match="Output schema"):
        create_llm_judge_config(
            session,
            project=project,
            slug="bad_schema_judge",
            name="Bad Schema Judge",
            judge_prompt="Score.",
            rubric_dimensions=[],
            output_schema={"type": "array"},
            judge_model_config_slug="judge_model",
        )


def test_experiment_snapshots_llm_judge_references_immutably(session: Session) -> None:
    project = _seed_project(session)
    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Original judge prompt.",
        rubric_dimensions=[{"name": "specificity"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
        raw_provider_params={"api_key": "secret"},
    )
    session.commit()

    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            _base_manifest(
                [
                    {
                        "id": "memo_quality_judge_eval",
                        "type": "llm_judge",
                        "definition": {"judge_config_id": "memo_quality_judge"},
                    }
                ]
            )
        ),
    )
    original_snapshot = experiment.evaluator_snapshots["memo_quality_judge_eval"]

    create_llm_judge_config_version(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge v2",
        judge_prompt="Changed judge prompt.",
        rubric_dimensions=[{"name": "decision_usefulness"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    session.commit()
    session.refresh(experiment)

    assert experiment.evaluator_snapshots["memo_quality_judge_eval"] == original_snapshot
    judge_config = original_snapshot["definition"]["judge_config"]
    assert original_snapshot["type"] == "llm_judge"
    assert judge_config["id"] == "memo_quality_judge"
    assert judge_config["version"] == 1
    assert judge_config["judge_prompt"] == "Original judge prompt."
    assert judge_config["raw_provider_params"]["api_key"] == "[redacted]"


def test_manifest_can_pin_versioned_judge_config_references(session: Session) -> None:
    project = _seed_project(session)
    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Original judge prompt.",
        rubric_dimensions=[{"name": "specificity"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    create_llm_judge_config_version(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge v2",
        judge_prompt="Changed judge prompt.",
        rubric_dimensions=[{"name": "decision_usefulness"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    session.commit()

    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            _base_manifest(
                [
                    {
                        "id": "memo_quality_judge_eval",
                        "type": "llm_judge",
                        "definition": {
                            "judge_config_ref": {"id": "memo_quality_judge", "version": 1}
                        },
                    }
                ]
            )
        ),
    )

    judge_config = experiment.evaluator_snapshots["memo_quality_judge_eval"]["definition"][
        "judge_config"
    ]
    assert judge_config["version"] == 1
    assert judge_config["judge_prompt"] == "Original judge prompt."


def test_manifest_supports_legacy_criterion_judge_config_reference(session: Session) -> None:
    project = _seed_project(session)
    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Original judge prompt.",
        rubric_dimensions=[{"name": "specificity"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    session.commit()

    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            _base_manifest(
                [
                    {
                        "id": "memo_quality_judge_eval",
                        "type": "llm_judge",
                        "definition": {"criterion": "memo_quality_judge"},
                    }
                ]
            )
        ),
    )

    judge_config = experiment.evaluator_snapshots["memo_quality_judge_eval"]["definition"][
        "judge_config"
    ]
    assert judge_config["id"] == "memo_quality_judge"


def test_library_llm_judge_evaluator_snapshots_judge_config(session: Session) -> None:
    project = _seed_project(session)
    create_llm_judge_config(
        session,
        project=project,
        slug="memo_quality_judge",
        name="Memo Quality Judge",
        judge_prompt="Original judge prompt.",
        rubric_dimensions=[{"name": "specificity"}],
        output_schema=_score_schema(),
        judge_model_config_slug="judge_model",
    )
    create_evaluator(
        session,
        project=project,
        slug="library_judge_eval",
        name="Library Judge Evaluator",
        evaluator_type="llm_judge",
        definition={"judge_config_id": "memo_quality_judge"},
    )
    session.commit()

    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(_base_manifest(["library_judge_eval"])),
    )

    snapshot = experiment.evaluator_snapshots["library_judge_eval"]
    assert snapshot["type"] == "llm_judge"
    assert snapshot["definition"]["judge_config"]["id"] == "memo_quality_judge"
    assert snapshot["definition"]["judge_config"]["judge_prompt"] == "Original judge prompt."


def test_manifest_supports_inline_llm_judge_definitions(session: Session) -> None:
    project = _seed_project(session)

    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            _base_manifest(
                [
                    {
                        "id": "inline_quality_judge",
                        "type": "llm_judge",
                        "definition": {
                            "judge_prompt": "Score answer quality.",
                            "rubric_dimensions": [{"name": "specificity"}],
                            "output_schema": _score_schema(),
                            "judge_model_config_id": "judge_model",
                            "raw_provider_params": {"api_key": "secret"},
                            "calibration_status": "uncalibrated",
                        },
                    }
                ]
            )
        ),
    )

    inline_snapshot = experiment.evaluator_snapshots["inline_quality_judge"]
    judge_config = inline_snapshot["definition"]["judge_config"]
    assert inline_snapshot["type"] == "llm_judge"
    assert judge_config["id"] == "inline_quality_judge"
    assert judge_config["judge_model_config_ref"] == {"id": "judge_model", "version": 1}
    assert judge_config["calibration_status"] == "uncalibrated"
    assert judge_config["raw_provider_params"]["api_key"] == "[redacted]"


def test_manifest_rejects_inline_llm_judge_without_prompt(session: Session) -> None:
    project = _seed_project(session)

    manifest = parse_manifest(
        _base_manifest(
            [
                {
                    "id": "inline_quality_judge",
                    "type": "llm_judge",
                    "definition": {
                        "rubric_dimensions": [{"name": "specificity"}],
                        "output_schema": _score_schema(),
                        "judge_model_config_id": "judge_model",
                    },
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="must include judge_prompt"):
        create_experiment_from_manifest(session, project=project, manifest=manifest)


def test_llm_judge_config_api_create_list_validate_version_and_archive(
    client: TestClient,
) -> None:
    project = "phase14-api"
    assert client.post(
        f"/projects/{project}/library/model-configs",
        json={
            "slug": "judge_model",
            "name": "Judge Model",
            "provider": "openai",
            "model": "gpt-5.5",
        },
    ).status_code == 201

    create_response = client.post(
        f"/projects/{project}/library/llm-judge-configs",
        json=_api_judge_payload(),
    )
    assert create_response.status_code == 201
    assert create_response.json()["snapshot"]["judge_prompt"] == "Score the answer."

    create_model_v2 = client.post(
        f"/projects/{project}/library/model-configs",
        json={
            "slug": "judge_model",
            "name": "Judge Model v2",
            "provider": "openai",
            "model": "gpt-5.5",
            "version": 2,
        },
    )
    assert create_model_v2.status_code == 201

    duplicate = client.post(
        f"/projects/{project}/library/llm-judge-configs",
        json=_api_judge_payload(),
    )
    assert duplicate.status_code == 409

    missing_model = client.post(
        f"/projects/{project}/library/llm-judge-configs",
        json={**_api_judge_payload(slug="missing-model"), "judge_model_config_slug": "missing"},
    )
    assert missing_model.status_code == 422
    assert "Model config reference 'missing'" in missing_model.json()["detail"]

    malformed_schema = client.post(
        f"/projects/{project}/library/llm-judge-configs",
        json={**_api_judge_payload(slug="bad-schema"), "output_schema": {"type": "array"}},
    )
    assert malformed_schema.status_code == 422
    assert "Output schema" in malformed_schema.json()["detail"]

    version_response = client.post(
        f"/projects/{project}/library/llm-judge-configs/memo_quality_judge/versions",
        json={
            **_api_judge_payload(),
            "judge_prompt": "Score the answer with stricter evidence.",
            "judge_model_config_version": 1,
        },
    )
    assert version_response.status_code == 201
    assert version_response.json()["version"] == 2
    assert version_response.json()["judge_model_config_version"] == 1

    missing_version_base = client.post(
        f"/projects/{project}/library/llm-judge-configs/missing_judge/versions",
        json={**_api_judge_payload(), "slug": "ignored"},
    )
    assert missing_version_base.status_code == 422
    assert "LLM judge config reference 'missing_judge'" in missing_version_base.json()["detail"]

    archive_response = client.delete(
        f"/projects/{project}/library/llm-judge-configs/{create_response.json()['id']}"
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["archived"] is True

    list_response = client.get(f"/projects/{project}/library/llm-judge-configs")
    assert list_response.status_code == 200
    assert [item["version"] for item in list_response.json()] == [1, 2]


def _seed_project(session: Session):
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="phase14", name="Phase 14")
    create_case(session, project=project, slug="case_a", name="Case A", prompt="Write memo.")
    create_system_prompt(
        session, project=project, slug="system_a", name="System A", prompt="Be precise."
    )
    create_conversation_warmer(
        session, project=project, slug="warmer_a", name="Warmer A", messages=[]
    )
    create_model_config(
        session,
        project=project,
        slug="model_a",
        name="Model A",
        provider="openai",
        model="gpt-5.5",
    )
    create_model_config(
        session,
        project=project,
        slug="judge_model",
        name="Judge Model",
        provider="openai",
        model="gpt-5.5",
    )
    session.flush()
    return project


def _base_manifest(evaluators: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "phase14_experiment",
        "name": "Phase 14 Experiment",
        "cases": ["case_a"],
        "models": ["model_a"],
        "system_prompts": ["system_a"],
        "warmers": ["warmer_a"],
        "design": {"type": "full_factorial", "replicates": 1},
        "evaluation": {"evaluators": evaluators},
    }


def _score_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "score": {"type": "number"},
            "explanation": {"type": "string"},
        },
        "required": ["score", "explanation"],
    }


def _api_judge_payload(slug: str = "memo_quality_judge") -> dict[str, Any]:
    return {
        "slug": slug,
        "name": "Memo Quality Judge",
        "judge_prompt": "Score the answer.",
        "rubric_dimensions": [{"name": "specificity"}],
        "output_schema": _score_schema(),
        "judge_model_config_slug": "judge_model",
        "raw_provider_params": {"temperature": 0.1},
        "calibration_status": "draft",
        "version": 1,
    }
