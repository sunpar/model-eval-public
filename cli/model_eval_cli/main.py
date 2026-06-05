import json
from pathlib import Path

import typer
from sqlalchemy import select

from model_eval_api import headless
from model_eval_api.copper_demo import build_copper_memo_demo
from model_eval_api.copper_seed import copper_memo_seed_payload
from model_eval_api.manifest import (
    ManifestValidationError,
    expand_manifest,
    load_manifest_file,
    validate_manifest_payload,
)
from model_eval_api.persistence import database, repositories
from model_eval_api.persistence.models import Project, Workspace
from model_eval_api.promptfoo import persist_promptfoo_import, preview_promptfoo_import
from model_eval_api.v2_demo import build_v2_demo

app = typer.Typer(help="Run and inspect Model Eval experiment manifests.")
import_app = typer.Typer(help="Import external eval formats.")
seed_app = typer.Typer(help="Generate local-only seed data for MVP demos.")
demo_app = typer.Typer(help="Build local-only synthetic product demos.")
suite_app = typer.Typer(help="Run versioned benchmark suites.")
app.add_typer(import_app, name="import")
app.add_typer(seed_app, name="seed")
app.add_typer(demo_app, name="demo")
app.add_typer(suite_app, name="suite")


def _echo_validation_errors(errors: list[str]) -> None:
    for error in errors:
        typer.echo(f"- {error}", err=True)


@app.command("validate")
def validate_command(manifest_path: Path) -> None:
    """Validate a manifest and print actionable errors."""

    try:
        manifest = load_manifest_file(manifest_path)
    except ManifestValidationError as error:
        typer.echo("valid: false")
        _echo_validation_errors(error.errors)
        raise typer.Exit(1) from error

    result = validate_manifest_payload(manifest)
    typer.echo(f"valid: {str(result.valid).lower()}")
    if not result.valid:
        _echo_validation_errors(result.errors)
        raise typer.Exit(1)


@app.command()
def preview(manifest_path: Path) -> None:
    """Preview run counts for a manifest without calling providers."""

    try:
        manifest = load_manifest_file(manifest_path)
        preview_result = expand_manifest(manifest)
    except ManifestValidationError as error:
        _echo_validation_errors(error.errors)
        raise typer.Exit(1) from error

    typer.echo(f"name: {manifest.name}")
    typer.echo(f"design: {preview_result.design_type}")
    typer.echo(f"cases: {preview_result.dimensions['cases']}")
    typer.echo(f"models: {preview_result.dimensions['models']}")
    typer.echo(f"system_prompts: {preview_result.dimensions['system_prompts']}")
    typer.echo(f"warmers: {preview_result.dimensions['warmers']}")
    typer.echo(f"replicates: {preview_result.replicates}")
    typer.echo(f"randomize_run_order: {str(preview_result.randomize_run_order).lower()}")
    typer.echo(f"random_seed: {preview_result.random_seed}")
    typer.echo(f"logical_runs: {preview_result.logical_runs}")
    typer.echo(f"run_attempts: {preview_result.run_attempts}")
    typer.echo(f"estimated_token_count: {preview_result.estimated_token_count}")
    typer.echo(f"estimated_cost_usd: {preview_result.estimated_cost_usd}")


@import_app.command("promptfoo")
def import_promptfoo(
    promptfoo_path: Path,
    persist: bool = typer.Option(
        False,
        "--persist/--preview-only",
        help="Persist mapped library records using the existing versioning rules.",
    ),
    project_slug: str = typer.Option("default", "--project", help="Project slug for persistence."),
) -> None:
    """Preview a Promptfoo config as a Model Eval manifest without provider calls."""

    try:
        promptfoo_preview = preview_promptfoo_import(promptfoo_path)
        payload = promptfoo_preview.to_payload()
        if persist:
            payload["persisted"] = _with_session(
                lambda session: _persist_promptfoo_import(
                    session, project_slug=project_slug, preview=promptfoo_preview
                )
            )
    except (ManifestValidationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command()
def expand(
    manifest_path: Path,
    output_format: str = typer.Option("json", "--format", help="Output format. Phase 1 supports json."),
) -> None:
    """Expand a manifest into deterministic logical runs and attempt metadata."""

    if output_format != "json":
        raise typer.BadParameter("Only json output is supported in Phase 1.")
    try:
        manifest = load_manifest_file(manifest_path)
        preview_result = expand_manifest(manifest)
    except ManifestValidationError as error:
        _echo_validation_errors(error.errors)
        raise typer.Exit(1) from error

    typer.echo(json.dumps(preview_result.model_dump(mode="json"), indent=2))


@app.command()
def run(
    manifest_path: Path,
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Avoid live provider calls."),
    local_only: bool = typer.Option(
        True,
        "--local-only/--allow-provider",
        help="Block non-local provider egress unless explicitly disabled.",
    ),
    output_format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Persist and execute a manifest in headless local-first mode."""

    if output_format not in {"text", "json"}:
        raise typer.BadParameter("Run format must be text or json.")
    try:
        payload = _with_session(
            lambda session: headless.run_manifest(
                session,
                manifest_path,
                dry_run=dry_run,
                local_only=local_only,
            )
        )
    except ManifestValidationError as error:
        _echo_validation_errors(error.errors)
        raise typer.Exit(1) from error
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    experiment = payload["experiment"]
    execution = payload["execution"]
    typer.echo(f"experiment_id: {experiment['id']}")
    typer.echo(f"slug: {experiment['slug']}")
    typer.echo(f"status: {experiment['status']}")
    typer.echo(f"dry_run: {str(payload['dry_run']).lower()}")
    typer.echo(f"local_only: {str(payload['local_only']).lower()}")
    typer.echo(f"succeeded_attempts: {execution['succeeded_attempts']}")


@app.command()
def compare(
    experiment: str,
    baseline: str = typer.Option(..., "--baseline", help="Baseline experiment id or slug."),
) -> None:
    """Compare an experiment against a baseline using stored analytics."""

    try:
        payload = _with_session(
            lambda session: headless.compare_experiments(session, experiment, baseline)
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command()
def review(
    experiment: str,
    blind: bool = typer.Option(True, "--blind/--reveal", help="Export a blind review queue."),
) -> None:
    """Generate a review queue export for an experiment."""

    if not blind:
        typer.echo("Only blind review queue export is supported in Phase 11.", err=True)
        raise typer.Exit(1)
    try:
        payload = _with_session(lambda session: headless.export_blind_review_queue(session, experiment))
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command()
def score(
    experiment: str,
    evaluator_id: str = typer.Option(..., "--evaluator", help="Evaluator id to run."),
) -> None:
    """Run a deterministic evaluator against a stored experiment."""

    try:
        payload = _with_session(
            lambda session: headless.score_experiment(session, experiment, evaluator_id)
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command()
def judge(
    experiment: str,
    evaluator_id: str = typer.Option(..., "--judge", help="LLM judge evaluator id to run."),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Avoid live judge provider calls."),
    local_only: bool = typer.Option(
        True,
        "--local-only/--allow-provider",
        help="Block non-local provider egress unless explicitly disabled.",
    ),
    position_swap: bool = typer.Option(
        True,
        "--position-swap/--no-position-swap",
        help="Run pairwise comparisons in both answer orders.",
    ),
) -> None:
    """Run an LLM judge evaluator against a stored experiment."""

    try:
        payload = _with_session(
            lambda session: headless.judge_experiment(
                session,
                experiment,
                evaluator_id,
                dry_run=dry_run,
                local_only=local_only,
                position_swap=position_swap,
            )
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command("metric-adapters")
def metric_adapters(
    experiment: str,
    adapter_config_slug: str | None = typer.Option(
        None,
        "--adapter",
        help="Metric adapter config slug to run. Omit to run all compatible configs.",
    ),
    adapter_config_version: int | None = typer.Option(
        None,
        "--adapter-version",
        help="Metric adapter config version to run.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--record",
        help="Validate compatible adapter runs without recording scores.",
    ),
    local_only: bool = typer.Option(
        True,
        "--local-only/--allow-provider",
        help="Block non-local metric adapters unless explicitly disabled.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Record a new score even when the adapter config snapshot already scored an attempt.",
    ),
) -> None:
    """Run metric adapters against stored experiment attempts."""

    try:
        payload = _with_session(
            lambda session: headless.run_metric_adapters(
                session,
                experiment,
                adapter_config_slug=adapter_config_slug,
                adapter_config_version=adapter_config_version,
                dry_run=dry_run,
                local_only=local_only,
                force=force,
            )
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command("export")
def export_command(
    experiment: str,
    output_format: str = typer.Option(
        "markdown",
        "--format",
        help="Output format: markdown, csv, json, promptfoo, or otel-json.",
    ),
) -> None:
    """Export an experiment for review, sharing, or outside analysis."""

    try:
        payload = _with_session(
            lambda session: headless.export_experiment(session, experiment, output_format)
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(payload, nl=not payload.endswith("\n"))


@suite_app.command("run")
def suite_run(
    suite: str,
    split: str | None = typer.Option(None, "--split", help="Dataset split to run."),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="Avoid live provider calls."),
    local_only: bool = typer.Option(
        True,
        "--local-only/--allow-provider",
        help="Block non-local provider egress unless explicitly disabled.",
    ),
    project_slug: str = typer.Option("default", "--project", help="Project slug."),
    output_format: str = typer.Option("json", "--format", help="Output format: json or text."),
) -> None:
    """Create and execute an experiment from a benchmark suite."""

    if output_format not in {"json", "text"}:
        raise typer.BadParameter("Suite run format must be json or text.")
    try:
        payload = _with_session(
            lambda session: headless.run_suite(
                session,
                suite,
                split=split,
                dry_run=dry_run,
                local_only=local_only,
                project_slug=project_slug,
            )
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    typer.echo(f"suite: {payload['suite']['slug']}")
    typer.echo(f"split: {payload['split'] or 'all'}")
    typer.echo(f"experiment_id: {payload['experiment']['id']}")
    typer.echo(f"status: {payload['experiment']['status']}")
    typer.echo(f"logical_runs: {payload['preview']['logical_runs']}")
    typer.echo(f"run_attempts: {payload['preview']['run_attempts']}")


@seed_app.command("copper-memo")
def seed_copper_memo(
    output_format: str = typer.Option(
        "json",
        "--format",
        help="Output format. Phase 0 supports json only.",
    ),
) -> None:
    """Emit local-only seed data for the copper memo context-sensitivity demo."""

    if output_format != "json":
        raise typer.BadParameter("Only json output is supported in Phase 0.")
    typer.echo(json.dumps(copper_memo_seed_payload(), indent=2))


@demo_app.command("copper-memo")
def demo_copper_memo(
    output_format: str = typer.Option("json", "--format", help="Output format: json or text."),
    export_dir: Path | None = typer.Option(
        None,
        "--export-dir",
        help="Directory for Markdown, CSV, and JSON demo exports.",
    ),
) -> None:
    """Build the complete copper memo context-sensitivity demo locally."""

    if output_format not in {"json", "text"}:
        raise typer.BadParameter("Demo format must be json or text.")
    try:
        payload = _with_session(
            lambda session: build_copper_memo_demo(session, export_dir=export_dir)
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    typer.echo(f"experiment_id: {payload['experiment']['id']}")
    typer.echo(f"slug: {payload['experiment']['slug']}")
    typer.echo(f"status: {payload['experiment']['status']}")
    typer.echo(f"runs: {payload['counts']['runs']}")
    typer.echo(f"attempts: {payload['counts']['attempts']}")
    typer.echo(f"review_items: {payload['counts']['review_items']}")
    for export in payload["exports"]:
        typer.echo(f"export_{export['format']}: {export['path']}")


@demo_app.command("v2")
def demo_v2(
    output_format: str = typer.Option("json", "--format", help="Output format: json or text."),
    export_dir: Path | None = typer.Option(
        None,
        "--export-dir",
        help="Directory for Markdown, CSV, and JSON V2 demo exports.",
    ),
) -> None:
    """Build the complete local-only synthetic V2 demo."""

    if output_format not in {"json", "text"}:
        raise typer.BadParameter("Demo format must be json or text.")
    try:
        payload = _with_session(lambda session: build_v2_demo(session, export_dir=export_dir))
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    typer.echo(f"experiment_id: {payload['experiment']['id']}")
    typer.echo(f"slug: {payload['experiment']['slug']}")
    typer.echo(f"status: {payload['experiment']['status']}")
    typer.echo(f"runs: {payload['counts']['runs']}")
    typer.echo(f"attempts: {payload['counts']['attempts']}")
    typer.echo(f"review_assignments: {payload['counts']['review_assignments']}")
    for export in payload["exports"]:
        typer.echo(f"export_{export['format']}: {export['path']}")


def _with_session(callback):
    headless.ensure_database_schema()
    with database.get_session_factory()() as session:
        return callback(session)


def _get_or_create_project(session, slug: str) -> Project:
    workspace = session.scalar(select(Workspace).where(Workspace.slug == "default"))
    if workspace is None:
        workspace = repositories.create_workspace(session, slug="default", name="Default")
        session.flush()
    project = session.scalar(
        select(Project).where(Project.workspace_id == workspace.id, Project.slug == slug)
    )
    if project is None:
        project = repositories.create_project(session, workspace=workspace, slug=slug, name=slug)
        session.flush()
    return project


def _persist_promptfoo_import(session, *, project_slug: str, preview):
    payload = persist_promptfoo_import(
        session,
        project=_get_or_create_project(session, project_slug),
        preview=preview,
    )
    session.commit()
    return payload


if __name__ == "__main__":
    app()
