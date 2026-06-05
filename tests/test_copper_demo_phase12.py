from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from model_eval_api.copper_demo import build_copper_memo_demo
from model_eval_api.persistence.models import (
    Base,
    Case,
    ConversationWarmer,
    Evaluator,
    ModelConfig,
    ReviewItem,
    ReviewSet,
    Run,
    RunAttempt,
    Score,
    SystemPrompt,
)
from model_eval_cli import main as cli_module


def test_copper_demo_builds_complete_local_synthetic_experiment(
    session: Session, tmp_path
) -> None:
    result = build_copper_memo_demo(session, export_dir=tmp_path)

    assert result["experiment"]["slug"] == "copper_memo_context_sensitivity_mvp"
    assert result["experiment"]["status"] == "complete"
    assert result["counts"]["runs"] == 16
    assert result["counts"]["attempts"] == 32
    assert result["counts"]["review_items"] == 16
    assert result["counts"]["review_decisions"] == 16
    assert result["counts"]["live_provider_calls"] == 0
    assert result["library"] == {
        "cases": 1,
        "warmers": 4,
        "system_prompts": 2,
        "model_configs": 2,
        "evaluators": 4,
    }
    assert {path["format"] for path in result["exports"]} == {"markdown", "csv", "json"}
    assert {path["extension"] for path in result["exports"]} == {".md", ".csv", ".json"}
    for export in result["exports"]:
        assert (tmp_path / export["filename"]).exists()

    analytics = result["analytics"]
    assert analytics["summary"]["attempt_count"] == 32
    assert analytics["warmer_lift"]
    assert analytics["context_sensitivity"]
    assert analytics["failure_tag_frequency"]
    assert analytics["cost_quality_table"]
    assert analytics["latency_quality_table"]

    assert session.scalar(select(Case).where(Case.slug == "chile_copper_memo")) is not None
    assert len(session.scalars(select(ConversationWarmer)).all()) == 4
    assert len(session.scalars(select(SystemPrompt)).all()) == 2
    assert len(session.scalars(select(ModelConfig)).all()) == 2
    assert len(session.scalars(select(Evaluator)).all()) == 4
    assert len(session.scalars(select(Run)).all()) == 16
    assert len(session.scalars(select(RunAttempt)).all()) == 32
    assert all(attempt.attempt_number == 1 for attempt in session.scalars(select(RunAttempt)).all())
    assert len(session.scalars(select(ReviewItem)).all()) == 16
    assert all(item.reviewer_decision for item in session.scalars(select(ReviewItem)).all())
    answer_texts = [
        answer["text"]
        for item in session.scalars(select(ReviewItem)).all()
        for answer in item.answer_snapshot["answers"]
    ]
    assert all("openai_gpt_high" not in text for text in answer_texts)
    assert all("claude_high" not in text for text in answer_texts)
    assert session.scalar(select(Score).where(Score.evaluator_type == "human")) is not None
    assert session.scalar(select(Score).where(Score.evaluator_type == "code")) is not None


def test_copper_demo_is_idempotent_for_local_reruns(session: Session, tmp_path) -> None:
    first = build_copper_memo_demo(session, export_dir=tmp_path)
    second = build_copper_memo_demo(session, export_dir=tmp_path)

    assert second["experiment"]["id"] == first["experiment"]["id"]
    assert second["counts"]["runs"] == 16
    assert second["counts"]["attempts"] == 32
    assert second["counts"]["review_items"] == 16
    assert second["counts"]["review_decisions"] == 16
    assert len(session.scalars(select(ReviewSet)).all()) == 1


def test_cli_copper_demo_outputs_json_and_writes_exports(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    runner = CliRunner()

    result = runner.invoke(
        cli_module.app,
        ["demo", "copper-memo", "--format", "json", "--export-dir", str(export_dir)],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["counts"]["runs"] == 16
    assert payload["counts"]["attempts"] == 32
    assert payload["counts"]["review_items"] == 16
    assert payload["analytics"]["warmer_lift"]
    assert {export["extension"] for export in payload["exports"]} == {".md", ".csv", ".json"}
    cli_module.database._engine = None
    cli_module.database._session_factory = None


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
