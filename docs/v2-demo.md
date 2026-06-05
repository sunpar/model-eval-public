# V2 Demo

The V2 copper demo is a local-only synthetic workbench path for benchmark suites, artifact preprocessing records, replicated execution, multi-reviewer blind review, judge calibration overlays, advanced analytics, and stable exports. A new developer can run it without provider keys, live model calls, or private input files.

## Completion Summary

V2 is complete against the backlog tracked in [`docs/implementation-task-list.md`](implementation-task-list.md) and the phase checklist in [`docs/v2-implementation-task-list.md`](v2-implementation-task-list.md). The implemented work is organized as:

- Phases 14-18: judge config libraries, judge execution and calibration, review queues and taxonomies, benchmark suites and splits, and replicated uncertainty-aware runs.
- Phase 19: semantic, claim, conclusion, confidence, structure, token-length, failure-mode, and carryover divergence analytics.
- Phase 20: versioned artifact preprocessing for PDF text, page images, normalized images, OCR, figures, tables, retrieval chunks, paper cards, and run input snapshots.
- Phase 21: RAG and DeepEval-style metric adapter registration, execution, API/CLI controls, UI controls, and export inclusion.
- Phase 22: Promptfoo import and export with explicit warnings for unsupported or lossy mappings.
- Phase 23: metadata-only OpenTelemetry JSON export with redaction coverage for prompts, artifacts, manifests, warmer text, payloads, outputs, and credentials.
- Phase 24: cost-quality frontier analytics and Results UI views that combine dominance, uncertainty, judge calibration, and context-sensitivity.
- Phase 25: this local-only demo, UI smoke coverage, and completion audit.

V3 remains out of scope for this readiness milestone: production trace ingestion, active sampling, synthetic case generation, team administration, evaluator CI gates, prompt deployment, release comparison, custom providers, local model hosting, and scheduled drift monitoring are intentionally not implemented by the V2 demo.

## Run The Demo

From a local checkout, build the complete demo and write exports to a temporary directory.
Point the database at `/tmp` when you want a disposable smoke run that leaves the checkout
clean:

```bash
PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3 python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
```

When running through the development environment, use the same command under `uv`:

```bash
PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3 uv run --extra dev python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
```

The command creates or reuses the `v2-copper-demo` project and `v2_copper_benchmark_suite` benchmark suite. It is safe to rerun: existing demo records are reused, generated exports are rewritten in the requested export directory, and no provider calls are made.

Expected shape:

- 1 benchmark suite: `v2_copper_benchmark_suite`.
- 1 completed all-split experiment: `v2_copper_benchmark_suite_v1_all_suite_run`.
- 16 logical runs and 32 synthetic succeeded attempts.
- 1 artifact preprocessing run over committed text and SVG fixtures.
- 16 review items, 2 reviewers, and 32 submitted review assignments.
- 1 local synthetic judge config with pairwise, pass/fail, and rubric scores.
- 2 local metric adapter configs with deterministic retrieval and citation scores.
- 0 live provider calls.

## Workflow Stages

The suite setup starts from `examples/v2_copper_benchmark_suite.yaml`. The suite locks the copper memo case, two model configs, two system prompts, four conversation warmers, deterministic evaluators, the synthetic judge config, the V2 failure taxonomy, reviewers, metric adapters, and two local-only replicates.

Preprocessing records are created from safe committed fixtures in `tests/fixtures/v2_demo_copper_context.txt` and `tests/fixtures/v2_demo_copper_chart.svg`. The demo records derived retrieval chunks and local references without copying raw local paths, credentials, or private artifacts into exports.

Execution is synthetic and local-only. The builder fills all attempts with deterministic response payloads, costs, tokens, latency, retrieved chunks, citations, reference answers, and derived artifact references. The command is intended to prove the V2 workflow without provider keys.

Review creates the blind pairwise review set `v2-copper-demo-review`, assigns both `v2_alice` and `v2_bob`, submits deterministic reviewer decisions, and preserves blind answer labels until metadata is explicitly revealed in the UI.

Calibration stores synthetic LLM judge pairwise, pass/fail, and rubric scores under `v2_synthetic_judge`. These rows are for local smoke coverage and calibration surface validation; they are not a trusted production quality signal.

Analytics include replicate reliability, warmer lift, context sensitivity, reviewer coverage, reviewer disagreement, failure taxonomy rollups, deterministic and judge-backed divergence rows, carryover rows, metric adapter scores, and cost-quality frontier rows.

Export writes stable Markdown, CSV, and JSON files:

- `/tmp/model-eval-v2-demo/v2_copper_demo_report.md`
- `/tmp/model-eval-v2-demo/v2_copper_demo_report.csv`
- `/tmp/model-eval-v2-demo/v2_copper_demo_report.json`

These files are generated demo artifacts and should stay out of git unless a later task explicitly asks for checked-in sample output.

## Inspect In The UI

Start the API and frontend:

```bash
make api
make frontend
```

The API must read the same local database that the demo command populated. If you used the default database from the same checkout, `make api` is enough. If you built the demo into a temporary SQLite file, reuse that URL for both commands:

```bash
export MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3
PYTHONPATH=backend:cli python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
make api
```

Open the app and use this smoke path:

1. Library: open **Benchmark suites** and confirm `V2 Copper Benchmark Suite` / `v2_copper_benchmark_suite` is available.
2. Run Monitor: refresh if needed, find `V2 Copper Benchmark Suite all suite run`, and use the completed experiment for review and results.
3. Comparison Workspace: create or load the blind review set, inspect the V2 local answer pair, and keep metadata hidden until the review is complete.
4. Results: inspect cost-quality frontier rows, dominated/frontier status, uncertainty labels, `v2_synthetic_judge` calibration overlays, metric adapter scores such as `v2_retrieval_precision`, and divergence rows including judge-backed claim or conclusion divergence.

## Troubleshooting

If the CLI cannot import `model_eval_cli` from a checkout, set `PYTHONPATH=backend:cli` as shown above. If a previous local database already contains demo records, rerunning the command should reuse them and keep the same counts. To start from scratch, point `MODEL_EVAL_DATABASE_URL` at a new local SQLite file before running the command.
