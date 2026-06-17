from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.execution_states import AttemptStatus
from model_eval_api.manifest import parse_manifest
from model_eval_api.metric_adapter_execution import run_metric_adapters_for_experiment
from model_eval_api.metric_adapters import (
    get_metric_adapter,
    map_deepeval_result_to_score,
    run_metric_adapter,
    validate_metric_adapter_inputs,
)
from model_eval_api.persistence.models import Base, MetricAdapterConfig, Run, Score
from model_eval_api.results_analytics import aggregate_experiment_results
from model_eval_api.persistence.repositories import (
    create_case,
    create_conversation_warmer,
    create_evaluator,
    create_experiment_from_manifest,
    create_metric_adapter_config,
    create_metric_adapter_config_version,
    create_model_config,
    create_project,
    create_system_prompt,
    create_workspace,
    record_run_attempt,
    record_score,
)


@pytest.fixture()
def session():
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
def client(session) -> Generator[TestClient, None, None]:
    def override_session():
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        yield TestClient(api_module.app)
    finally:
        api_module.app.dependency_overrides.clear()


def test_metric_adapter_configs_persist_project_scoped_versioned_snapshots(session) -> None:
    project = _project(session, slug="research")
    config = create_metric_adapter_config(
        session,
        project=project,
        slug="retrieval_precision_local",
        name="Retrieval Precision Local",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object", "required": ["score"]},
        capability_metadata={"supports_local_only": True, "max_chunks": 20},
    )
    session.commit()

    assert config.version == 1
    assert config.local_only is True
    assert config.archived is False
    assert config.snapshot == {
        "id": "retrieval_precision_local",
        "name": "Retrieval Precision Local",
        "adapter_kind": "retrieval_precision",
        "adapter_version": "local-1",
        "required_inputs": ["answer_text", "retrieved_chunks"],
        "output_schema": {"type": "object", "required": ["score"]},
        "capability_metadata": {"supports_local_only": True, "max_chunks": 20},
        "local_only": True,
        "version": 1,
        "archived": False,
    }

    config.name = "Mutated name"
    with pytest.raises(ValueError, match="Metric adapter config versions are immutable"):
        session.commit()
    session.rollback()

    persisted_config = session.get(MetricAdapterConfig, config.id)
    assert persisted_config is not None
    assert persisted_config.name == "Retrieval Precision Local"
    assert persisted_config.snapshot["name"] == "Retrieval Precision Local"

    version_2 = create_metric_adapter_config_version(
        session,
        project=project,
        slug="retrieval_precision_local",
        name="Retrieval Precision Local v2",
        adapter_kind="retrieval_precision",
        adapter_version="local-2",
        required_inputs=["answer_text", "retrieved_chunks", "derived_artifacts"],
        output_schema={"type": "object", "required": ["score", "label"]},
        capability_metadata={"supports_local_only": True},
    )
    other_project = _project(session, slug="other")
    other_config = create_metric_adapter_config(
        session,
        project=other_project,
        slug="retrieval_precision_local",
        name="Other Retrieval Precision",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object"},
    )
    session.commit()

    assert version_2.version == 2
    assert version_2.snapshot["required_inputs"] == [
        "answer_text",
        "retrieved_chunks",
        "derived_artifacts",
    ]
    assert other_config.version == 1
    assert session.scalars(select(MetricAdapterConfig)).all()

    create_metric_adapter_config(
        session,
        project=project,
        slug="retrieval_precision_local",
        name="Duplicate",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text"],
        output_schema={"type": "object"},
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_local_metric_adapter_configs_reject_unknown_adapter_kinds(session) -> None:
    project = _project(session)

    with pytest.raises(ValueError, match="adapter_version"):
        create_metric_adapter_config(
            session,
            project=project,
            slug="missing_version",
            name="Missing Version",
            adapter_kind="retrieval_precision",
            adapter_version=None,
            required_inputs=["answer_text"],
            output_schema={"type": "object"},
        )

    with pytest.raises(ValueError, match="Unsupported metric adapter kind"):
        create_metric_adapter_config(
            session,
            project=project,
            slug="unknown_local",
            name="Unknown Local",
            adapter_kind="unknown_metric",
            adapter_version="local-1",
            required_inputs=["answer_text"],
            output_schema={"type": "object"},
        )

    external = create_metric_adapter_config(
        session,
        project=project,
        slug="custom_deepeval",
        name="Custom DeepEval Shape",
        adapter_kind="custom-deepeval",
        adapter_version="deepeval-compatible",
        required_inputs=["answer_text"],
        output_schema={"type": "object"},
        local_only=False,
    )
    session.commit()

    assert external.adapter_kind == "custom_deepeval"
    assert external.local_only is False


def test_metric_adapter_required_inputs_distinguish_input_classes() -> None:
    result = validate_metric_adapter_inputs(
        [
            "answer_text",
            "retrieved_chunks",
            "citations",
            "reference_answers",
            "derived_artifacts",
        ],
        {
            "answer_text": "Copper demand rose.",
            "retrieved_chunks": [{"text": "Copper demand rose on grid spend."}],
            "citations": [],
            "reference_answers": None,
        },
    )

    assert result == {
        "valid": False,
        "present": ["answer_text", "retrieved_chunks"],
        "missing": ["citations", "reference_answers", "derived_artifacts"],
    }

    valid = validate_metric_adapter_inputs(
        [
            "answer_text",
            "retrieved_chunks",
            "citations",
            "reference_answers",
            "derived_artifacts",
        ],
        {
            "answer_text": "Copper demand rose.",
            "retrieved_chunks": [{"text": "Copper demand rose on grid spend."}],
            "citations": [{"id": "1"}],
            "reference_answers": ["Copper demand rose on grid spend."],
            "derived_artifacts": [{"input_mode": "pdf_text", "metadata": {"page_count": 1}}],
        },
    )
    assert valid == {
        "valid": True,
        "present": [
            "answer_text",
            "retrieved_chunks",
            "citations",
            "reference_answers",
            "derived_artifacts",
        ],
        "missing": [],
    }

    blank_reference_answers = validate_metric_adapter_inputs(
        ["answer_text", "reference_answers"],
        {
            "answer_text": "Copper demand rose.",
            "reference_answers": ["   ", {"text": ""}, {"reference_answer": "   "}],
        },
    )

    assert blank_reference_answers == {
        "valid": False,
        "present": ["answer_text"],
        "missing": ["reference_answers"],
    }

    fallback_reference_answer = validate_metric_adapter_inputs(
        ["answer_text", "reference_answers"],
        {
            "answer_text": "Copper demand rose.",
            "reference_answers": [{"text": "", "reference_answer": "Copper demand rose."}],
        },
    )

    assert fallback_reference_answer == {
        "valid": True,
        "present": ["answer_text", "reference_answers"],
        "missing": [],
    }


def test_local_metric_adapters_produce_deterministic_score_payloads() -> None:
    retrieval = run_metric_adapter(
        "retrieval_precision",
        {
            "answer_text": "Copper demand rose on grid spend.",
            "retrieved_chunks": [
                {"text": "Copper demand rose on grid spend."},
                {"chunk_text": "Unrelated shipping constraint."},
            ],
        },
    )
    assert retrieval.type == "metric_adapter"
    assert retrieval.criterion == "retrieval_precision"
    assert retrieval.value["metric_source"] == "local_metric_adapter"
    assert retrieval.value["retrieved_chunk_count"] == 2
    assert retrieval.value["score"] == 0.5

    citation = run_metric_adapter(
        "citation_coverage",
        {
            "answer_text": "Copper demand rose [0], while treatment charges fell [3].",
            "citations": [{"id": 0}, {"id": "2"}],
        },
    )
    assert citation.value["score"] == 0.5
    assert citation.value["cited_ids"] == ["0"]
    assert citation.value["uncited_ids"] == ["2"]

    grounded = run_metric_adapter(
        "groundedness_checklist",
        {
            "answer_text": "Copper demand rose on grid spend.",
            "retrieved_chunks": [{"text": "Copper demand rose."}],
            "derived_artifacts": [{"metadata": {"sections": [{"title": "Grid spend"}]}}],
        },
    )
    assert grounded.value["checklist"]["has_supporting_context"] is True
    assert grounded.value["checklist"]["has_derived_artifacts"] is True
    assert grounded.value["score"] > 0

    relevance = run_metric_adapter(
        "answer_relevance",
        {
            "answer_text": "Copper demand rose on grid spend.",
            "reference_answers": ["Grid spend lifted copper demand."],
        },
    )
    assert relevance.value["metric_source"] == "local_metric_adapter"
    assert relevance.value["score"] > 0


def test_deepeval_style_mapping_records_metric_adapter_score(session) -> None:
    project = _project(session)
    config = create_metric_adapter_config(
        session,
        project=project,
        slug="answer_relevance_deepeval",
        name="Answer Relevance DeepEval Shape",
        adapter_kind="answer_relevance",
        adapter_version="deepeval-compatible",
        required_inputs=["answer_text", "reference_answers"],
        output_schema={"type": "object"},
    )
    attempt = _run_attempt(session, project)
    session.commit()

    mapped = map_deepeval_result_to_score(
        config.snapshot,
        {
            "name": "Answer Relevancy",
            "score": 0.82,
            "success": True,
            "reason": "The answer addresses the reference.",
            "metadata": {"threshold": 0.7},
        },
    )
    score = record_score(
        session,
        run_attempt=attempt,
        type=mapped.type,
        evaluator_type="metric_adapter",
        criterion=mapped.criterion,
        value=mapped.value,
        explanation=mapped.explanation,
        confidence=mapped.confidence,
        evaluator_version=config.version,
    )
    session.commit()

    persisted = session.get(Score, score.id)
    assert persisted is not None
    assert persisted.type == "metric_adapter"
    assert persisted.evaluator_type == "metric_adapter"
    assert persisted.criterion == "answer_relevance"
    assert persisted.confidence == 0.82
    assert persisted.evaluator_version == 1
    assert persisted.value == {
        "metric_source": "deepeval_style",
        "adapter_kind": "answer_relevance",
        "adapter_version": "deepeval-compatible",
        "metric_name": "Answer Relevancy",
        "score": 0.82,
        "success": True,
        "source_kind": "judge_backed",
        "metadata": {"threshold": 0.7},
    }


def test_metric_adapter_alembic_upgrade_head_runs_against_temp_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "migration.sqlite"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{db_path}")

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as connection:
        table_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "select name from sqlite_master where type = 'table'"
            )
        }

    assert "metric_adapter_configs" in table_names


def test_metric_adapter_registry_exposes_expected_local_adapters() -> None:
    assert get_metric_adapter("retrieval_precision").required_inputs == [
        "answer_text",
        "retrieved_chunks",
    ]
    assert get_metric_adapter("citation_coverage").required_inputs == [
        "answer_text",
        "citations",
    ]
    assert get_metric_adapter("groundedness_checklist").required_inputs == [
        "answer_text",
        "retrieved_chunks",
        "derived_artifacts",
    ]
    assert get_metric_adapter("answer_relevance").required_inputs == [
        "answer_text",
        "reference_answers",
    ]


def test_metric_adapter_execution_records_scores_and_explicit_skips(session) -> None:
    project = _project(session)
    create_metric_adapter_config(
        session,
        project=project,
        slug="retrieval_precision_local",
        name="Retrieval Precision Local",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object"},
    )
    create_metric_adapter_config(
        session,
        project=project,
        slug="citation_coverage_local",
        name="Citation Coverage Local",
        adapter_kind="citation_coverage",
        adapter_version="local-1",
        required_inputs=["answer_text", "citations"],
        output_schema={"type": "object"},
    )
    attempt = _run_attempt(
        session,
        project,
        response_payload={
            "output_text": "Copper demand rose on grid spend.",
            "retrieved_chunks": [{"chunk_text": "Copper demand rose on grid spend."}],
        },
    )
    session.commit()

    result = run_metric_adapters_for_experiment(
        session,
        experiment_id=attempt.run.experiment_id,
        dry_run=False,
        local_only=True,
    )
    session.commit()

    assert result["attempts_evaluated"] == 1
    assert result["scores_recorded"] == 1
    assert result["skipped"] == [
        {
            "adapter_config_id": "citation_coverage_local",
            "adapter_config_version": 1,
            "attempt_id": "attempt_metric_adapter",
            "reason": "missing_required_inputs",
            "missing_inputs": ["citations"],
        }
    ]
    score = session.scalar(select(Score).where(Score.evaluator_type == "metric_adapter"))
    assert score is not None
    assert score.criterion == "retrieval_precision"
    assert score.value["adapter_config"] == {"id": "retrieval_precision_local", "version": 1}
    assert score.value["metric_source"] == "local_metric_adapter"
    assert score.value["source_kind"] == "deterministic_heuristic"

    analytics = aggregate_experiment_results(session, experiment_id=attempt.run.experiment_id)
    assert analytics["metric_adapter_scores"] == [
        {
            "attempt_id": "attempt_metric_adapter",
            "case_slug": "case_a",
            "model_config_slug": "model_a",
            "system_prompt_slug": "sys_a",
            "warmer_slug": "none",
            "adapter_config_slug": "retrieval_precision_local",
            "adapter_config_version": 1,
            "criterion": "retrieval_precision",
            "metric_source": "local_metric_adapter",
            "source_kind": "deterministic_heuristic",
            "score": 1.0,
            "label": "strong",
            "explanation": "Measured retrieved chunk lexical overlap with the answer text.",
            "confidence": 0.7,
        }
    ]

    duplicate = run_metric_adapters_for_experiment(
        session,
        experiment_id=attempt.run.experiment_id,
        adapter_config_slug="retrieval_precision_local",
        dry_run=False,
        local_only=True,
    )
    session.commit()
    assert duplicate["scores_recorded"] == 0
    assert duplicate["skipped"][0]["reason"] == "duplicate_adapter_score"

    forced = run_metric_adapters_for_experiment(
        session,
        experiment_id=attempt.run.experiment_id,
        adapter_config_slug="retrieval_precision_local",
        dry_run=False,
        local_only=True,
        force=True,
    )
    session.commit()
    assert forced["scores_recorded"] == 1
    assert len(session.scalars(select(Score).where(Score.evaluator_type == "metric_adapter")).all()) == 2


def test_metric_adapter_execution_falls_back_to_nested_output_when_direct_text_is_blank(
    session,
) -> None:
    project = _project(session, slug="nested_metric_output")
    create_metric_adapter_config(
        session,
        project=project,
        slug="retrieval_precision_local",
        name="Retrieval Precision Local",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object"},
    )
    attempt = _run_attempt(
        session,
        project,
        response_payload={
            "output_text": "  ",
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Copper demand rose on grid spend.",
                        }
                    ]
                }
            ],
            "retrieved_chunks": [{"chunk_text": "Copper demand rose on grid spend."}],
        },
    )
    session.commit()

    result = run_metric_adapters_for_experiment(
        session,
        experiment_id=attempt.run.experiment_id,
        dry_run=False,
        local_only=True,
    )
    session.commit()

    assert result["scores_recorded"] == 1
    assert result["skipped"] == []
    score = session.scalar(select(Score).where(Score.evaluator_type == "metric_adapter"))
    assert score is not None
    assert score.value["score"] == 1.0


def test_metric_adapter_execution_dry_run_local_only_and_api_surface(
    client: TestClient, session
) -> None:
    project = _project(session, slug="api_metric")
    create_metric_adapter_config(
        session,
        project=project,
        slug="retrieval_precision_local",
        name="Retrieval Precision Local",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object"},
    )
    create_metric_adapter_config(
        session,
        project=project,
        slug="external_answer_check",
        name="External Answer Check",
        adapter_kind="external-answer-check",
        adapter_version="external-1",
        required_inputs=["answer_text"],
        output_schema={"type": "object"},
        local_only=False,
    )
    attempt = _run_attempt(
        session,
        project,
        response_payload={
            "output_text": "Copper demand rose on grid spend.",
            "retrieved_chunks": [{"chunk_text": "Copper demand rose on grid spend."}],
        },
    )
    session.commit()

    dry_run = client.post(
        f"/monitor/experiments/{attempt.run.experiment_id}/metric-adapters/run",
        json={"adapter_config_slug": "retrieval_precision_local", "dry_run": True},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["scores_recorded"] == 0
    assert dry_run.json()["planned_scores"] == 1
    assert session.scalars(select(Score).where(Score.evaluator_type == "metric_adapter")).all() == []

    blocked = run_metric_adapters_for_experiment(
        session,
        experiment_id=attempt.run.experiment_id,
        adapter_config_slug="external_answer_check",
        dry_run=False,
        local_only=True,
    )
    assert blocked["scores_recorded"] == 0
    assert blocked["skipped"][0]["reason"] == "non_local_adapter_blocked"

    recorded = client.post(
        f"/monitor/experiments/{attempt.run.experiment_id}/metric-adapters/run",
        json={"adapter_config_slug": "retrieval_precision_local", "dry_run": False},
    )
    assert recorded.status_code == 200
    assert recorded.json()["scores_recorded"] == 1


def test_metric_adapter_api_returns_404_for_missing_experiment(client: TestClient) -> None:
    response = client.post("/monitor/experiments/999/metric-adapters/run", json={})

    assert response.status_code == 404
    assert response.json()["detail"] == "Experiment 999 does not exist."


def test_metric_adapter_api_preserves_422_for_invalid_adapter_filters(
    client: TestClient, session
) -> None:
    project = _project(session, slug="adapter_filter_validation")
    attempt = _run_attempt(session, project)
    session.commit()

    response = client.post(
        f"/monitor/experiments/{attempt.run.experiment_id}/metric-adapters/run",
        json={"adapter_config_version": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "adapter_config_version requires adapter_config_slug."


def test_metric_adapter_execution_uses_registry_required_inputs_for_local_adapters(
    session,
) -> None:
    project = _project(session, slug="underdeclared_metric")
    create_metric_adapter_config(
        session,
        project=project,
        slug="retrieval_precision_underdeclared",
        name="Retrieval Precision Underdeclared",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text"],
        output_schema={"type": "object"},
    )
    attempt = _run_attempt(
        session,
        project,
        response_payload={"output_text": "Copper demand rose on grid spend."},
    )
    session.commit()

    result = run_metric_adapters_for_experiment(
        session,
        experiment_id=attempt.run.experiment_id,
        adapter_config_slug="retrieval_precision_underdeclared",
        dry_run=False,
        local_only=True,
    )

    assert result["scores_recorded"] == 0
    assert result["skipped"] == [
        {
            "adapter_config_id": "retrieval_precision_underdeclared",
            "adapter_config_version": 1,
            "attempt_id": "attempt_metric_adapter",
            "reason": "missing_required_inputs",
            "missing_inputs": ["retrieved_chunks"],
        }
    ]


def _project(session, *, slug: str = "research"):
    workspace = create_workspace(session, slug=f"{slug}_workspace", name=f"{slug} Workspace")
    return create_project(session, workspace=workspace, slug=slug, name=slug.title())


def _run_attempt(session, project, response_payload: dict | None = None):
    create_case(session, project=project, slug="case_a", name="Case A", prompt="Write answer")
    create_model_config(
        session,
        project=project,
        slug="model_a",
        name="Model A",
        provider="openai",
        model="gpt-5.5",
    )
    create_system_prompt(session, project=project, slug="sys_a", name="Sys A", prompt="System")
    create_conversation_warmer(
        session, project=project, slug="none", name="None", messages=[]
    )
    create_evaluator(
        session,
        project=project,
        slug="placeholder",
        name="Placeholder",
        evaluator_type="deterministic",
        definition={"kind": "no_empty_output"},
    )
    manifest = parse_manifest(
        {
            "name": "metric_adapter_mapping",
            "cases": ["case_a"],
            "models": ["model_a"],
            "system_prompts": ["sys_a"],
            "warmers": ["none"],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": ["placeholder"]},
        }
    )
    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))
    assert run is not None
    return record_run_attempt(
        session,
        run=run,
        attempt_id="attempt_metric_adapter",
        replicate_index=0,
        response_payload=response_payload or {"text": "Copper demand rose on grid spend."},
        status=AttemptStatus.SUCCEEDED.value,
    )
