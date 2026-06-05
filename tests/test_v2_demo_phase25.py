from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from model_eval_api.persistence.models import (
    Artifact,
    ArtifactPreprocessingRun,
    Base,
    BenchmarkSuite,
    LLMJudgeConfig,
    MetricAdapterConfig,
    ProviderCallCache,
    ReviewAssignment,
    ReviewSet,
    Reviewer,
    Run,
    RunAttempt,
    Score,
)
from model_eval_api.v2_demo import build_v2_demo
from model_eval_cli import main as cli_module


def test_v2_demo_builds_complete_local_synthetic_suite(
    session: Session, tmp_path: Path
) -> None:
    result = build_v2_demo(session, export_dir=tmp_path)

    assert result["demo_id"] == "v2_copper_demo"
    assert result["mode"] == "local_only_synthetic"
    assert result["suite"] == {
        "slug": "v2_copper_benchmark_suite",
        "version": 1,
        "split": "all",
    }
    assert result["experiment"]["slug"] == "v2_copper_benchmark_suite_v1_all_suite_run"
    assert result["experiment"]["status"] == "complete"
    assert result["counts"] == {
        "benchmark_suites": 1,
        "runs": 16,
        "attempts": 32,
        "succeeded_attempts": 32,
        "review_items": 16,
        "reviewers": 2,
        "review_assignments": 32,
        "review_submissions": 32,
        "preprocessing_runs": 1,
        "judge_configs": 1,
        "judge_scores": 96,
        "metric_adapter_configs": 2,
        "metric_adapter_scores": 64,
        "divergence_scores": 24,
        "live_provider_calls": 0,
    }
    assert {export["extension"] for export in result["exports"]} == {".md", ".csv", ".json"}
    for export in result["exports"]:
        export_path = Path(export["path"])
        assert export_path.is_relative_to(tmp_path)
        assert export_path.exists()

    analytics = result["analytics"]
    assert analytics["summary"]["attempt_count"] == 32
    assert len(analytics["metric_adapter_scores"]) == 64
    assert any(
        row["criterion"] == "divergence_semantic_overlap"
        and row["source_kind"] == "deterministic_heuristic"
        for row in analytics["divergence_summary"]
    )
    assert any(
        row["criterion"] in {"divergence_claim", "divergence_conclusion"}
        and row["metric_source"] == "llm_judge_rubric"
        for row in analytics["divergence_summary"]
    )
    frontier = analytics["cost_quality_frontier"]
    assert any(row["is_frontier"] for row in frontier)
    assert any(row["dominance_status"] == "dominated" for row in frontier)
    assert any(row["divergence_summary"] for row in frontier)
    assert any(row["judge_calibration_overlays"] for row in frontier)
    calibration = analytics["judge_calibration"][0]
    assert calibration["evaluator_id"] == "v2_synthetic_judge"
    assert calibration["comparison_count"] > 0
    assert calibration["disagreement_count"] > 0
    assert calibration["low_confidence_count"] > 0
    assert analytics["reviewer_coverage"] == [
        {
            "review_set_id": session.scalar(select(ReviewSet.id)),
            "assigned_count": 32,
            "submitted_count": 32,
            "pending_count": 0,
            "reviewer_count": 2,
            "coverage_rate": 1.0,
        }
    ]
    assert any(row["pairwise_disagreement"] for row in analytics["reviewer_disagreement"])

    assert len(session.scalars(select(BenchmarkSuite)).all()) == 1
    assert len(session.scalars(select(ArtifactPreprocessingRun)).all()) == 1
    assert len(session.scalars(select(Artifact)).all()) >= 3
    assert len(session.scalars(select(LLMJudgeConfig)).all()) == 1
    assert len(session.scalars(select(MetricAdapterConfig)).all()) == 2
    assert len(session.scalars(select(Reviewer)).all()) == 2
    assert len(session.scalars(select(ReviewAssignment)).all()) == 32
    assert all(
        assignment.status == "submitted"
        for assignment in session.scalars(select(ReviewAssignment)).all()
    )
    assert len(session.scalars(select(Run)).all()) == 16
    assert len(session.scalars(select(RunAttempt)).all()) == 32
    assert len(session.scalars(select(ProviderCallCache)).all()) == 0


def test_v2_demo_is_idempotent_for_local_reruns(session: Session, tmp_path: Path) -> None:
    first = build_v2_demo(session, export_dir=tmp_path / "first")
    second = build_v2_demo(session, export_dir=tmp_path / "second")

    assert second["experiment"]["id"] == first["experiment"]["id"]
    assert second["counts"] == first["counts"]
    assert len(session.scalars(select(BenchmarkSuite)).all()) == 1
    assert len(session.scalars(select(ArtifactPreprocessingRun)).all()) == 1
    assert len(session.scalars(select(ReviewSet)).all()) == 1
    assert len(session.scalars(select(ReviewAssignment)).all()) == 32
    assert len(
        session.scalars(select(Score).where(Score.evaluator_type == "llm_judge")).all()
    ) == 96
    assert len(
        session.scalars(select(Score).where(Score.evaluator_type == "metric_adapter")).all()
    ) == 64


def test_cli_v2_demo_outputs_json_and_writes_temp_exports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "demo.sqlite3"
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    runner = CliRunner()

    result = runner.invoke(
        cli_module.app,
        ["demo", "v2", "--format", "json", "--export-dir", str(export_dir)],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["counts"]["runs"] == 16
    assert payload["counts"]["attempts"] == 32
    assert payload["counts"]["review_assignments"] == 32
    assert payload["counts"]["judge_scores"] == 96
    assert payload["counts"]["metric_adapter_scores"] == 64
    assert payload["analytics"]["divergence_summary"]
    assert payload["analytics"]["cost_quality_frontier"]
    assert payload["analytics"]["judge_calibration"]
    assert {export["extension"] for export in payload["exports"]} == {".md", ".csv", ".json"}
    assert all(Path(export["path"]).is_relative_to(export_dir) for export in payload["exports"])
    cli_module.database._engine = None
    cli_module.database._session_factory = None


def test_v2_demo_committed_fixtures_and_suite_definition_are_safe_assets() -> None:
    text_fixture = Path("tests/fixtures/v2_demo_copper_context.txt")
    image_fixture = Path("tests/fixtures/v2_demo_copper_chart.svg")
    suite_definition = Path("examples/v2_copper_benchmark_suite.yaml")

    assert text_fixture.exists()
    assert "SYNTHETIC LOCAL TEST FIXTURE" in text_fixture.read_text(encoding="utf-8")
    image_text = image_fixture.read_text(encoding="utf-8")
    assert image_text.startswith("<svg")
    assert "api_key" not in image_text.lower()

    suite = yaml.safe_load(suite_definition.read_text(encoding="utf-8"))
    assert suite["id"] == "v2_copper_benchmark_suite"
    assert suite["controls"]["local_only"] is True
    assert suite["controls"]["replicates"] == 2
    assert suite["splits"]["dev"]["cases"] == ["chile_copper_memo"]
    assert suite["system_prompts"][0] == {"id": "expert_investment_analyst_v3", "version": 3}
    assert suite["warmers"][1] == {"id": "copper_expert_user_v2", "version": 2}
    assert "judge_config" in suite
    assert "reviewers" in suite
    assert "metric_adapters" in suite


def test_generated_v2_exports_are_not_committed() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    committed_export_names = {
        "v2_copper_demo_report.md",
        "v2_copper_demo_report.csv",
        "v2_copper_demo_report.json",
    }

    assert not any((repo_root / name).exists() for name in committed_export_names)
    assert not any((repo_root / "examples" / name).exists() for name in committed_export_names)


def test_v2_demo_docs_name_local_workflow_commands_and_outputs() -> None:
    doc = Path("docs/v2-demo.md")

    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    expected_fragments = [
        "python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo",
        "v2_copper_benchmark_suite",
        "v2_copper_demo_report.md",
        "v2_copper_demo_report.csv",
        "v2_copper_demo_report.json",
        "without provider keys",
        "local-only",
        "suite setup",
        "preprocessing",
        "execution",
        "review",
        "calibration",
        "analytics",
        "export",
    ]

    for fragment in expected_fragments:
        assert fragment in text


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
