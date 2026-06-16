from __future__ import annotations

import csv
from io import StringIO
import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from model_eval_api import main as api_module
from model_eval_api.headless import (
    compare_experiments,
    export_blind_review_queue,
    export_experiment,
    export_experiment_response,
    run_manifest,
    score_experiment,
)
from model_eval_api.manifest import parse_manifest
from model_eval_api.metric_adapter_execution import run_metric_adapters_for_experiment
from model_eval_api.otel_export import build_experiment_trace
from model_eval_api.persistence.models import AuditLog, Base, Experiment, Run, RunAttempt
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_failure_taxonomy,
    create_metric_adapter_config,
    create_project,
    create_review_set_from_completed_experiment,
    create_reviewer,
    create_workspace,
    record_assignment_decision,
    record_review_decision,
    record_score,
)
from model_eval_api.providers import ProviderRequest, ProviderResponse, ProviderUsage
from model_eval_cli import main as cli_module


class CapturingProviderAdapter:
    provider = "openai"

    def __init__(self) -> None:
        self.local_only_values: list[bool] = []

    def build_request(self, run_snapshot: dict) -> ProviderRequest:
        model_config = run_snapshot["model_config"]
        return ProviderRequest(
            provider=model_config["provider"],
            model=model_config["model"],
            payload={"model": model_config["model"], "input": []},
            raw_provider_params={},
            normalized_config={},
        )

    def execute(
        self,
        request: ProviderRequest,
        *,
        config=None,
        dry_run: bool = True,
    ) -> ProviderResponse:
        self.local_only_values.append(config.local_only)
        return ProviderResponse(
            provider=request.provider,
            model=request.model,
            response_payload={"output_text": "live answer"},
            output_text="live answer",
            usage=ProviderUsage(input_tokens=1, output_tokens=2, total_tokens=3),
            cost_usd=0.01,
            dry_run=dry_run,
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


def test_run_manifest_persists_and_executes_dry_run_local_only(
    session: Session, tmp_path
) -> None:
    manifest_path = tmp_path / "headless.yaml"
    manifest_path.write_text(_headless_manifest("Write a short memo."), encoding="utf-8")

    result = run_manifest(session, manifest_path, dry_run=True, local_only=True)

    assert result["experiment"]["slug"] == "headless_run"
    assert result["experiment"]["status"] == "complete"
    assert result["dry_run"] is True
    assert result["local_only"] is True
    assert result["preview"]["logical_runs"] == 1
    assert result["preview"]["run_attempts"] == 1
    assert result["execution"]["succeeded_attempts"] == 1
    assert result["execution"]["live_provider_calls"] == 0


def test_run_manifest_rejects_changed_manifest_for_existing_slug(session: Session, tmp_path) -> None:
    manifest_path = tmp_path / "headless.yaml"
    manifest_path.write_text(_headless_manifest("Original prompt."), encoding="utf-8")
    run_manifest(session, manifest_path, dry_run=True, local_only=True)
    manifest_path.write_text(_headless_manifest("Changed prompt."), encoding="utf-8")

    with pytest.raises(ValueError, match="different manifest"):
        run_manifest(session, manifest_path, dry_run=True, local_only=True)


def test_run_manifest_honors_local_only_cli_override_without_changing_snapshot(
    session: Session, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "provider.yaml"
    manifest_path.write_text(_headless_manifest("Provider override.", local_only=True), encoding="utf-8")
    adapter = CapturingProviderAdapter()
    monkeypatch.setattr(
        "model_eval_api.executor.default_provider_adapters",
        lambda: {"openai": adapter},
    )

    result = run_manifest(session, manifest_path, dry_run=False, local_only=False)
    experiment = _experiment_by_slug(session, "headless_run")

    assert result["local_only"] is False
    assert adapter.local_only_values == [False]
    assert experiment.controls_snapshot["local_only"] is True


def test_run_manifest_keeps_source_manifest_snapshot_across_cli_local_only_modes(
    session: Session, tmp_path
) -> None:
    manifest_path = tmp_path / "stable.yaml"
    manifest_path.write_text(_headless_manifest("Stable snapshot.", local_only=False), encoding="utf-8")

    run_manifest(session, manifest_path, dry_run=True, local_only=True)
    run_manifest(session, manifest_path, dry_run=True, local_only=False)
    experiment = _experiment_by_slug(session, "headless_run")

    assert experiment.controls_snapshot["local_only"] is False


def test_export_formats_have_stable_headers_and_json_shape(session: Session) -> None:
    experiment = _completed_experiment(session, slug="export_target")
    _record_human_scores(session, experiment)
    session.commit()

    exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    assert list(exported_json) == [
        "format_version",
        "experiment",
        "reproducibility",
        "snapshots",
        "runs",
        "attempts",
        "scores",
        "reviews",
        "analytics",
    ]
    assert exported_json["reproducibility"]["includes_manifest_snapshot"] is True
    assert exported_json["snapshots"]["warmers"]["none"]["messages"] == []
    assert exported_json["runs"][0]["run_id"]
    assert exported_json["attempts"][0]["response_payload"]["output_text"].startswith("Answer")
    assert exported_json["analytics"]["summary"]["attempt_count"] == 2

    exported_csv = export_experiment(session, experiment.slug, "csv").splitlines()
    assert exported_csv[0] == (
        "section,id,parent_id,experiment_id,run_id,attempt_id,case_slug,model_config_slug,"
        "system_prompt_slug,warmer_slug,replicate_index,replicate_group_id,attempt_kind,status,"
        "type,evaluator_type,criterion,"
        "metric_source,source_kind,label,reviewer_id,assignment_status,taxonomy_version,value,"
        "explanation,warning,warning_label,cost_usd,"
        "latency_ms,input_tokens,output_tokens,total_tokens,sample_count,variance,interval_lower,"
        "interval_upper,uncertainty_label,suite_slug,suite_split,quality_metric,quality_rate,"
        "dominance_status,dominated_by,is_frontier,frontier_key,quality_interval_lower,"
        "quality_interval_upper,cost_interval_lower,cost_interval_upper,latency_interval_lower,"
        "latency_interval_upper,promptfoo_provider_id,promptfoo_prompt_id,"
        "promptfoo_test_description,promptfoo_assertion_types"
    )
    assert any(line.startswith("run,") for line in exported_csv[1:])
    assert any(line.startswith("attempt,") for line in exported_csv[1:])
    assert any(line.startswith("score,") for line in exported_csv[1:])
    assert any(line.startswith("aggregate_summary,") for line in exported_csv[1:])

    exported_markdown = export_experiment(session, experiment.id, "markdown")
    assert "# Experiment export: Export Target" in exported_markdown
    assert "## Configs" in exported_markdown
    assert "## Scores" in exported_markdown
    assert "## Costs" in exported_markdown
    assert "## Failure Tags" in exported_markdown
    assert "## Key Examples" in exported_markdown


def test_exports_include_cost_quality_frontier_rows(
    client: TestClient,
    session: Session,
) -> None:
    experiment = _completed_experiment(session, slug="frontier_export")
    attempts = [
        run.attempts[0] for run in sorted(experiment.runs, key=lambda item: item.model_config_slug)
    ]
    for attempt in attempts:
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
    frontier_rows = exported_json["analytics"]["cost_quality_frontier"]
    dominated = next(row for row in frontier_rows if row["model_config_slug"] == "model_b")
    assert dominated["dominance_status"] == "dominated"
    assert dominated["quality_interval"]["label"] == "single_sample"

    csv_rows = list(csv.DictReader(StringIO(export_experiment(session, experiment.id, "csv"))))
    frontier_csv_rows = [row for row in csv_rows if row["section"] == "aggregate_frontier"]
    assert frontier_csv_rows[0]["type"] == "cost_quality_frontier"
    assert {row["dominance_status"] for row in frontier_csv_rows} == {"frontier", "dominated"}
    assert frontier_csv_rows[0]["quality_metric"] == "pass_rate"
    assert frontier_csv_rows[0]["quality_interval_lower"]

    exported_markdown = export_experiment(session, experiment.id, "markdown")
    assert "## Cost-Quality Frontier" in exported_markdown
    assert "dominated" in exported_markdown

    filtered_json = json.loads(
        export_experiment(session, experiment.id, "json", model_config_slug="model_a")
    )
    assert filtered_json["analytics"]["filters"]["model_config_slug"] == "model_a"
    assert [
        row["model_config_slug"]
        for row in filtered_json["analytics"]["cost_quality_frontier"]
    ] == ["model_a"]

    filtered_csv_rows = list(
        csv.DictReader(
            StringIO(export_experiment(session, experiment.id, "csv", model_config_slug="model_a"))
        )
    )
    assert [
        row["model_config_slug"]
        for row in filtered_csv_rows
        if row["section"] == "aggregate_frontier"
    ] == ["model_a"]

    response = client.get(
        f"/monitor/experiments/{experiment.id}/exports",
        params={"format": "json", "model_config_slug": "model_a"},
    )
    assert response.status_code == 200
    export_payload = json.loads(response.json()["content"])
    assert export_payload["analytics"]["filters"]["model_config_slug"] == "model_a"
    assert [
        row["model_config_slug"]
        for row in export_payload["analytics"]["cost_quality_frontier"]
    ] == ["model_a"]


def test_export_endpoint_returns_404_for_missing_experiment(client: TestClient) -> None:
    response = client.get("/monitor/experiments/9999/exports?format=json")

    assert response.status_code == 404


def test_markdown_export_formats_missing_and_zero_frontier_latency(session: Session) -> None:
    experiment = _completed_experiment(session, slug="frontier_latency_export")
    attempts = [
        run.attempts[0] for run in sorted(experiment.runs, key=lambda item: item.model_config_slug)
    ]
    attempts[0].latency_ms = 0
    attempts[1].latency_ms = None
    for attempt in attempts:
        record_score(
            session,
            run_attempt=attempt,
            type="pass_fail",
            evaluator_type="human",
            criterion="blind_pairwise_pass_fail",
            value={"passed": True},
        )
    session.commit()

    exported_markdown = export_experiment(session, experiment.id, "markdown")

    assert "latency 0ms" in exported_markdown
    assert "latency n/a" in exported_markdown
    assert "n/ams" not in exported_markdown


def test_exports_include_metric_adapter_score_rows(session: Session) -> None:
    experiment = _completed_experiment(session, slug="metric_adapter_export")
    create_metric_adapter_config(
        session,
        project=experiment.project,
        slug="retrieval_precision_local",
        name="Retrieval Precision Local",
        adapter_kind="retrieval_precision",
        adapter_version="local-1",
        required_inputs=["answer_text", "retrieved_chunks"],
        output_schema={"type": "object"},
    )
    attempt = experiment.runs[0].attempts[0]
    attempt.response_payload = {
        "output_text": "Copper demand rose on grid spend.",
        "retrieved_chunks": [{"chunk_text": "Copper demand rose on grid spend."}],
    }
    run_metric_adapters_for_experiment(
        session,
        experiment_id=experiment.id,
        adapter_config_slug="retrieval_precision_local",
        dry_run=False,
        local_only=True,
    )
    session.commit()

    exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    metric_score = next(
        score for score in exported_json["scores"] if score["evaluator_type"] == "metric_adapter"
    )
    assert metric_score["value"]["adapter_config"] == {
        "id": "retrieval_precision_local",
        "version": 1,
    }
    assert metric_score["value"]["source_kind"] == "deterministic_heuristic"

    csv_rows = list(csv.DictReader(StringIO(export_experiment(session, experiment.id, "csv"))))
    metric_row = next(row for row in csv_rows if row["evaluator_type"] == "metric_adapter")
    assert metric_row["metric_source"] == "local_metric_adapter"
    assert metric_row["source_kind"] == "deterministic_heuristic"
    assert metric_row["criterion"] == "retrieval_precision"

    exported_markdown = export_experiment(session, experiment.id, "markdown")
    assert "metric_adapter / retrieval_precision" in exported_markdown


def test_exports_include_divergence_and_carryover_analytics_rows(session: Session) -> None:
    experiment = _completed_divergence_experiment(session)
    baseline = _attempt_by_warmer(experiment, "none")
    comparison = _attempt_by_warmer(experiment, "analyst")
    record_score(
        session,
        run_attempt=comparison,
        type="divergence",
        evaluator_type="code",
        criterion="divergence_semantic_overlap",
        value={
            "metric_source": "deterministic_semantic_overlap",
            "comparison_scope": "case_model_system_prompt_warmer",
            "baseline_attempt_id": baseline.attempt_id,
            "comparison_attempt_id": comparison.attempt_id,
            "value": 0.62,
            "label": "high",
            "warning": "Semantic overlap is a deterministic heuristic.",
        },
        confidence=0.35,
    )
    session.commit()

    exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    divergence_summary = exported_json["analytics"]["divergence_summary"][0]
    assert divergence_summary["source_kind"] == "deterministic_heuristic"
    assert divergence_summary["sample_count"] == 1
    assert exported_json["analytics"]["carryover_summary"][0]["source_kind"] == (
        "deterministic_heuristic"
    )

    csv_rows = list(csv.DictReader(StringIO(export_experiment(session, experiment.id, "csv"))))
    divergence_rows = [row for row in csv_rows if row["section"] == "aggregate_divergence"]
    carryover_rows = [row for row in csv_rows if row["section"] == "aggregate_carryover"]
    assert divergence_rows[0]["source_kind"] == "deterministic_heuristic"
    assert divergence_rows[0]["sample_count"] == "1"
    assert divergence_rows[0]["warning_label"] == "heuristic"
    assert carryover_rows[0]["source_kind"] == "deterministic_heuristic"

    exported_markdown = export_experiment(session, experiment.id, "markdown")
    assert "## Divergence Metrics" in exported_markdown
    assert "deterministic heuristic" in exported_markdown
    assert "## Carryover Audit" in exported_markdown


def test_exports_include_replicate_metadata_and_uncertainty_rows(session: Session) -> None:
    experiment = _completed_experiment(session, slug="phase18_export", replicates=2)
    first = experiment.runs[0].attempts[0]
    second = experiment.runs[0].attempts[1]
    first.replicate_group_id = "case:model:system:none"
    second.replicate_group_id = "case:model:system:none"
    first.attempt_kind = "replicate"
    second.attempt_kind = "replicate"
    for run in experiment.runs:
        for attempt in run.attempts:
            attempt.cost_usd = None
    session.commit()

    exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    assert exported_json["attempts"][0]["replicate_group_id"] == "case:model:system:none"
    assert exported_json["attempts"][0]["attempt_kind"] == "replicate"
    model_rows = exported_json["analytics"]["nondeterminism_by_dimension"]["model_config_slug"]
    assert model_rows[0]["sample_count"] >= 2
    assert "cost_usd_interval" in model_rows[0]

    exported_csv = export_experiment(session, experiment.id, "csv")
    assert "replicate_group_id" in exported_csv.splitlines()[0]
    assert "aggregate_uncertainty" in exported_csv
    uncertainty_rows = [
        row
        for row in csv.DictReader(StringIO(exported_csv))
        if row["section"] == "aggregate_uncertainty"
        and row["label"] == "model_config_slug=model_a"
    ]
    assert uncertainty_rows[0]["sample_count"] == "2"
    assert uncertainty_rows[0]["uncertainty_label"] == "low_sample"


def test_otel_trace_has_stable_span_ids_and_parent_links(session: Session) -> None:
    experiment = _completed_experiment(session, slug="otel_trace")
    _record_human_scores(session, experiment)
    score_experiment(session, experiment.id, "no_empty_output_v1")
    record_score(
        session,
        run_attempt=experiment.runs[0].attempts[0],
        type="pairwise_preference",
        evaluator_type="llm_judge",
        criterion="judge_pairwise_preference",
        value={"label": "A", "source_kind": "judge_backed"},
    )
    export_experiment(session, experiment.id, "json")
    session.commit()

    first = build_experiment_trace(session, experiment.id)
    second = build_experiment_trace(session, experiment.id)

    assert first == second
    assert first["format_version"] == "model_eval_otel_trace_v1"
    assert len(first["trace_id"]) == 32
    spans = first["spans"]
    root = _span_by_name(spans, "model_eval.experiment")
    run = _span_by_name(spans, "model_eval.run")
    attempt = _span_by_name(spans, "model_eval.run_attempt")
    deterministic = _span_by_name(spans, "model_eval.deterministic_evaluator")
    judge = _span_by_name(spans, "model_eval.judge_evaluator")
    review_set = _span_by_name(spans, "model_eval.human_review_set")
    review_item = _span_by_name(spans, "model_eval.human_review_item")
    review_assignment = _span_by_name(spans, "model_eval.human_review_assignment")
    export_event = _span_by_name(spans, "model_eval.export_event")

    assert root["parent_span_id"] is None
    assert run["parent_span_id"] == root["span_id"]
    assert attempt["parent_span_id"] == run["span_id"]
    assert deterministic["parent_span_id"] == attempt["span_id"]
    assert judge["parent_span_id"] == attempt["span_id"]
    assert review_set["parent_span_id"] == root["span_id"]
    assert review_item["parent_span_id"] == review_set["span_id"]
    assert review_assignment["parent_span_id"] == review_item["span_id"]
    assert export_event["parent_span_id"] == root["span_id"]
    assert attempt["attributes"]["model_eval.cost_usd"] is not None
    assert attempt["attributes"]["model_eval.total_tokens"] is not None


def test_otel_trace_missing_integer_id_does_not_fall_back_to_numeric_slug(
    session: Session,
) -> None:
    _completed_experiment(session, slug="404")

    with pytest.raises(ValueError, match="404"):
        build_experiment_trace(session, 404)


def test_otel_json_export_response_records_audit_without_trace_payload(
    session: Session, client: TestClient
) -> None:
    experiment = _completed_experiment(session, slug="otel_export_surface")
    session.commit()

    payload = export_experiment_response(session, experiment.id, "otel-json")

    assert payload["format"] == "otel-json"
    assert payload["warnings"] == []
    trace = json.loads(payload["content"])
    assert trace["format_version"] == "model_eval_otel_trace_v1"
    assert trace["spans"][-1]["name"] == "model_eval.export_event"
    assert trace["spans"][-1]["attributes"]["model_eval.export.format"] == "otel-json"
    audit = session.scalar(
        select(AuditLog).where(
            AuditLog.experiment_id == experiment.id,
            AuditLog.event_kind == "export_generated",
        )
    )
    assert audit is not None
    assert audit.details == {"format": "otel-json"}
    assert "spans" not in json.dumps(audit.details)

    response = client.get(f"/monitor/experiments/{experiment.id}/exports?format=otel-json")
    assert response.status_code == 200
    api_payload = response.json()
    assert api_payload["format"] == "otel-json"
    assert json.loads(api_payload["content"])["format_version"] == "model_eval_otel_trace_v1"


def test_exports_include_review_assignments_taxonomy_and_disagreement(
    session: Session,
) -> None:
    experiment = _completed_experiment(session, slug="phase16_export")
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    taxonomy = create_failure_taxonomy(
        session,
        project=experiment.project,
        slug="memo-taxonomy",
        name="Memo taxonomy",
        tags=["too generic"],
        version=3,
    )
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="phase16-export-review",
        name="Phase 16 export review",
        random_seed=1,
        reviewer_slugs=["alice"],
        failure_taxonomy_slug=taxonomy.slug,
    )
    session.flush()
    record_assignment_decision(
        session,
        assignment=review_set.assignments[0],
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
    )
    session.commit()

    exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    assignment = exported_json["reviews"][0]["assignments"][0]
    assert assignment["reviewer_id"] == "alice"
    assert assignment["status"] == "submitted"
    assert assignment["taxonomy_snapshot"]["version"] == 3
    assert exported_json["analytics"]["reviewer_coverage"][0]["submitted_count"] == 1
    assert exported_json["analytics"]["failure_taxonomy_rollup"][0]["taxonomy_version"] == 3

    exported_csv = export_experiment(session, experiment.id, "csv")
    assert "review_assignment" in exported_csv
    assert ",alice,submitted,3," in exported_csv


def test_export_payload_eager_loads_review_assignment_reviewers(session: Session) -> None:
    experiment = _completed_experiment(session, slug="phase16_eager_export")
    reviewer_slugs = ["alice", "bob", "chris"]
    for reviewer_slug in reviewer_slugs:
        create_reviewer(
            session,
            project=experiment.project,
            slug=reviewer_slug,
            name=reviewer_slug.title(),
        )
    create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="phase16-eager-export-review",
        name="Phase 16 eager export review",
        random_seed=1,
        reviewer_slugs=reviewer_slugs,
    )
    session.commit()
    session.expire_all()

    statements: list[str] = []

    def capture_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", capture_selects)
    try:
        exported_json = json.loads(export_experiment(session, experiment.id, "json"))
    finally:
        event.remove(session.bind, "before_cursor_execute", capture_selects)

    assert len(exported_json["reviews"][0]["assignments"]) == len(reviewer_slugs)
    reviewer_selects = [
        statement for statement in statements if "from reviewers" in statement.lower()
    ]
    assert len(reviewer_selects) <= 1


def test_blind_review_export_omits_hidden_metadata(session: Session) -> None:
    experiment = _completed_experiment(session, slug="blind_export")
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="blind-export",
        name="Blind export",
        random_seed=1,
    )
    session.commit()
    record_review_decision(
        session,
        review_item=review_set.items[0],
        reviewer_id="reviewer",
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
    )
    session.commit()

    payload = export_blind_review_queue(session, experiment.id)
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["review_set"]["id"] == review_set.id
    assert payload["review_set"]["metadata"]["blind"] is True
    assert payload["items"][0]["item_key"].startswith("review-item-")
    assert payload["items"][0]["answers"][0] == {"label": "A", "text": "Answer 2"}
    assert "run_attempt_id" not in encoded
    assert "model_a" not in encoded
    assert "model_b" not in encoded
    assert "system_prompt_slug" not in encoded
    assert "warmer_slug" not in encoded
    assert "reveal_metadata" not in encoded
    assert "source_experiment_id" not in encoded
    assert "reviewer_decision" not in encoded
    assert "reviewer" not in encoded
    assert "winner" not in encoded


def test_compare_and_score_commands_return_headless_summaries(session: Session) -> None:
    baseline = _completed_experiment(session, slug="baseline", cost_offset=0.0)
    candidate = _completed_experiment(session, slug="candidate", cost_offset=0.1)
    session.commit()

    comparison = compare_experiments(session, candidate.id, baseline.id)

    assert comparison["experiment"]["slug"] == "candidate"
    assert comparison["baseline"]["slug"] == "baseline"
    assert comparison["delta"]["average_cost_usd"] == pytest.approx(0.1)

    scored = score_experiment(session, candidate.id, "no_empty_output_v1")

    assert scored["experiment_id"] == candidate.id
    assert scored["evaluator_id"] == "no_empty_output_v1"
    assert scored["attempts_evaluated"] == 2
    assert scored["scores_recorded"] == 2


def test_slug_resolution_rejects_ambiguous_experiment_slugs(session: Session) -> None:
    _completed_experiment(session, slug="shared", workspace_slug="workspace-a", project_slug="project-a")
    _completed_experiment(session, slug="shared", workspace_slug="workspace-b", project_slug="project-b")
    session.commit()

    with pytest.raises(ValueError, match="matches multiple projects"):
        export_experiment(session, "shared", "json")


def test_numeric_string_ref_falls_back_to_slug_lookup_when_id_is_missing(session: Session) -> None:
    experiment = _completed_experiment(session, slug="2026")
    session.commit()

    exported_json = json.loads(export_experiment(session, "2026", "json"))

    assert exported_json["experiment"]["id"] == experiment.id
    assert exported_json["experiment"]["slug"] == "2026"


def test_cli_export_uses_local_database(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    database_path = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    runner = CliRunner()
    manifest_path = tmp_path / "cli.yaml"
    manifest_path.write_text(
        """
name: cli_headless
cases:
  - id: case
    prompt: Write a short memo.
models:
  - id: model_a
    provider: openai
    model: gpt-5.5
system_prompts:
  - id: system
    prompt: Be concise.
warmers:
  - id: none
    messages: []
design:
  replicates: 1
evaluation:
  evaluators: []
""",
        encoding="utf-8",
    )

    run_result = runner.invoke(cli_module.app, ["run", str(manifest_path), "--format", "json"])
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.stdout)

    json_export_result = runner.invoke(
        cli_module.app,
        ["export", str(run_payload["experiment"]["id"]), "--format", "json"],
    )
    assert json_export_result.exit_code == 0
    json_export_payload = json.loads(json_export_result.stdout)
    assert json_export_payload["experiment"]["slug"] == "cli_headless"

    export_result = runner.invoke(
        cli_module.app,
        ["export", str(run_payload["experiment"]["id"]), "--format", "otel-json"],
    )

    assert export_result.exit_code == 0
    export_payload = json.loads(export_result.stdout)
    assert export_payload["format_version"] == "model_eval_otel_trace_v1"
    assert any(span["name"] == "model_eval.export_event" for span in export_payload["spans"])
    cli_module.database._engine = None
    cli_module.database._session_factory = None


@pytest.mark.parametrize(
    "filter_options",
    [
        [
            "--case",
            "case",
            "--suite",
            "suite_alpha",
            "--split",
            "dev",
            "--model-config",
            "model_a",
            "--system-prompt",
            "system",
            "--warmer",
            "none",
            "--evaluator-source",
            "human",
            "--reviewer",
            "reviewer",
        ],
        [
            "--case-slug",
            "case",
            "--suite-slug",
            "suite_alpha",
            "--suite-split",
            "dev",
            "--model-config-slug",
            "model_a",
            "--system-prompt-slug",
            "system",
            "--warmer-slug",
            "none",
            "--evaluator-source",
            "human",
            "--reviewer-id",
            "reviewer",
        ],
    ],
    ids=["short-aliases", "slug-aliases"],
)
def test_cli_export_forwards_analytics_filters_to_headless_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path, filter_options: list[str]
) -> None:
    database_path = tmp_path / "cli-filter.sqlite3"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    runner = CliRunner()

    try:
        cli_module.headless.ensure_database_schema()
        with cli_module.database.get_session_factory()() as db:
            experiment = _completed_experiment(db, slug="cli_filter_export")
            _record_human_scores(db, experiment)
            experiment_id = str(experiment.id)

        export_result = runner.invoke(
            cli_module.app,
            [
                "export",
                experiment_id,
                "--format",
                "json",
                *filter_options,
            ],
        )

        assert export_result.exit_code == 0
        export_payload = json.loads(export_result.stdout)
        assert export_payload["analytics"]["filters"] == {
            "case_slug": "case",
            "suite_slug": "suite_alpha",
            "suite_split": "dev",
            "model_config_slug": "model_a",
            "system_prompt_slug": "system",
            "warmer_slug": "none",
            "evaluator_source": "human",
            "reviewer_id": "reviewer",
        }
    finally:
        cli_module.database._engine = None
        cli_module.database._session_factory = None


def test_cli_compare_review_and_score_use_local_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    database_path = tmp_path / "cli-workflow.sqlite3"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    cli_module.database._engine = None
    cli_module.database._session_factory = None
    runner = CliRunner()
    manifest_path = tmp_path / "cli.yaml"
    manifest_path.write_text(_headless_manifest("CLI workflow.", local_only=True), encoding="utf-8")

    run_result = runner.invoke(
        cli_module.app,
        ["run", str(manifest_path), "--allow-provider", "--format", "json"],
    )
    assert run_result.exit_code == 0
    run_payload = json.loads(run_result.stdout)
    assert run_payload["local_only"] is False
    experiment_id = str(run_payload["experiment"]["id"])

    compare_result = runner.invoke(
        cli_module.app,
        ["compare", experiment_id, "--baseline", experiment_id],
    )
    review_result = runner.invoke(cli_module.app, ["review", experiment_id, "--blind"])
    score_result = runner.invoke(
        cli_module.app,
        ["score", experiment_id, "--evaluator", "no_empty_output_v1"],
    )
    with cli_module.database.get_session_factory()() as db:
        experiment = db.get(Experiment, int(experiment_id))
        assert experiment is not None
        create_metric_adapter_config(
            db,
            project=experiment.project,
            slug="retrieval_precision_local",
            name="Retrieval Precision Local",
            adapter_kind="retrieval_precision",
            adapter_version="local-1",
            required_inputs=["answer_text", "retrieved_chunks"],
            output_schema={"type": "object"},
        )
        attempt = db.scalar(
            select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
        )
        assert attempt is not None
        attempt.response_payload = {
            "output_text": "Copper demand rose on grid spend.",
            "retrieved_chunks": [{"chunk_text": "Copper demand rose on grid spend."}],
        }
        db.commit()
    metric_result = runner.invoke(
        cli_module.app,
        ["metric-adapters", experiment_id, "--adapter", "retrieval_precision_local"],
    )

    assert compare_result.exit_code == 0
    assert json.loads(compare_result.stdout)["delta"]["failure_rate"] == 0
    assert review_result.exit_code == 0
    review_payload = json.loads(review_result.stdout)
    assert review_payload["review_set"]["metadata"]["blind"] is True
    assert "run_attempt_id" not in json.dumps(review_payload)
    assert score_result.exit_code == 0
    assert json.loads(score_result.stdout)["evaluator_id"] == "no_empty_output_v1"
    assert metric_result.exit_code == 0
    assert json.loads(metric_result.stdout)["scores_recorded"] == 1
    cli_module.database._engine = None
    cli_module.database._session_factory = None


def _completed_experiment(
    session: Session,
    *,
    slug: str = "experiment",
    cost_offset: float = 0.0,
    replicates: int = 1,
    workspace_slug: str | None = None,
    project_slug: str | None = None,
):
    workspace = create_workspace(
        session,
        slug=workspace_slug or f"workspace-{slug}",
        name=f"Workspace {workspace_slug or slug}",
    )
    project = create_project(
        session,
        workspace=workspace,
        slug=project_slug or slug,
        name=(project_slug or slug).title(),
    )
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": slug,
                "name": slug.replace("_", " ").title(),
                "cases": [{"id": "case", "prompt": "Write a memo."}],
                "models": [
                    {"id": "model_a", "provider": "openai", "model": "a"},
                    {"id": "model_b", "provider": "anthropic", "model": "b"},
                ],
                "system_prompts": [{"id": "system", "prompt": "Be concise."}],
                "warmers": [{"id": "none", "messages": []}],
                "design": {"replicates": replicates},
                "controls": {"local_only": True},
                "evaluation": {
                    "evaluators": [
                        {
                            "id": "no_empty_output_v1",
                            "type": "deterministic",
                            "definition": {"kind": "no_empty_output"},
                        }
                    ]
                },
            }
        ),
    )
    experiment.status = "complete"
    for index, run in enumerate(sorted(experiment.runs, key=lambda item: item.model_config_slug)):
        run.status = "complete"
        for replicate, attempt in enumerate(sorted(run.attempts, key=lambda item: item.replicate_index)):
            attempt.status = "succeeded"
            output_suffix = f"{index + 1}.{replicate + 1}" if replicates > 1 else f"{index + 1}"
            attempt.response_payload = {"output_text": f"Answer {output_suffix}"}
            attempt.cost_usd = 0.2 + cost_offset + (index * 0.1) + (replicate * 0.05)
            attempt.latency_ms = 1000 + (index * 100) + (replicate * 10)
            attempt.input_tokens = 100 + index + replicate
            attempt.output_tokens = 50 + index + replicate
            attempt.total_tokens = attempt.input_tokens + attempt.output_tokens
    session.commit()
    return experiment


def _completed_divergence_experiment(session: Session):
    workspace = create_workspace(
        session,
        slug="workspace-divergence-export",
        name="Workspace Divergence Export",
    )
    project = create_project(
        session,
        workspace=workspace,
        slug="divergence_export",
        name="Divergence Export",
    )
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": "divergence_export",
                "name": "Divergence Export",
                "cases": [{"id": "case", "prompt": "Write a memo."}],
                "models": [{"id": "model_a", "provider": "openai", "model": "a"}],
                "system_prompts": [{"id": "system", "prompt": "Be concise."}],
                "warmers": [
                    {"id": "none", "messages": []},
                    {
                        "id": "analyst",
                        "messages": [
                            {
                                "role": "user",
                                "content": "Use drawdown and inventory context.",
                            }
                        ],
                    },
                ],
                "design": {"replicates": 1},
                "controls": {"local_only": True},
                "evaluation": {"evaluators": []},
            }
        ),
    )
    experiment.status = "complete"
    for run in experiment.runs:
        run.status = "complete"
        attempt = run.attempts[0]
        attempt.status = "succeeded"
        attempt.response_payload = {
            "output_text": (
                "Claim: Drawdown supports inventory risk. Conclusion: Hold capacity."
                if run.warmer_slug == "analyst"
                else "Claim: Inventory risk is stable. Conclusion: Hold capacity."
            )
        }
    session.commit()
    return experiment


def _attempt_by_warmer(experiment, warmer_slug: str):
    for run in experiment.runs:
        if run.warmer_slug == warmer_slug:
            return run.attempts[0]
    raise AssertionError("attempt not found")


def _span_by_name(spans: list[dict[str, object]], name: str) -> dict[str, object]:
    matches = [span for span in spans if span["name"] == name]
    assert matches, f"missing span {name}"
    return matches[0]


def _experiment_by_slug(session: Session, slug: str):
    from model_eval_api.persistence.models import Experiment

    experiment = session.scalar(select(Experiment).where(Experiment.slug == slug))
    assert experiment is not None
    return experiment


def _headless_manifest(prompt: str, *, local_only: bool = False) -> str:
    return f"""
name: headless_run
cases:
  - id: case
    prompt: {prompt}
models:
  - id: model_a
    provider: openai
    model: gpt-5.5
system_prompts:
  - id: system
    prompt: Be concise.
warmers:
  - id: none
    messages: []
design:
  replicates: 1
controls:
  local_only: {str(local_only).lower()}
evaluation:
  evaluators:
    - id: no_empty_output_v1
      type: deterministic
      definition:
        kind: no_empty_output
"""


def _record_human_scores(session: Session, experiment) -> None:
    attempts = [
        run.attempts[0] for run in sorted(experiment.runs, key=lambda item: item.model_config_slug)
    ]
    record_score(
        session,
        run_attempt=attempts[0],
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "winner", "label": "A", "winner": "A"},
    )
    record_score(
        session,
        run_attempt=attempts[1],
        type="pairwise_preference",
        evaluator_type="human",
        criterion="blind_pairwise_preference",
        value={"outcome": "loser", "label": "B", "winner": "A"},
    )
    record_score(
        session,
        run_attempt=attempts[1],
        type="failure_tags",
        evaluator_type="human",
        criterion="blind_pairwise_failure_tags",
        value={"tags": ["too generic"]},
    )
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug=f"{experiment.slug}-review",
        name=f"{experiment.name} review",
        random_seed=1,
        reviewer_slugs=["reviewer"],
    )
    session.flush()
    record_review_decision(
        session,
        review_item=review_set.items[0],
        reviewer_id="reviewer",
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={"A": "stronger", "B": "thin"},
    )
