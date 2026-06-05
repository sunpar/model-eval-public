# Task Workflows Runbook

Use this runbook to choose the right Model Eval workflow for a specific task. Commands assume
you are running from a checkout without an activated editable install, so they include
`PYTHONPATH=backend:cli`.

For disposable runs, set a temporary database URL first:

```bash
export MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-workflow.sqlite3
```

## Validate Or Preview A Manifest

Use this before persisting any experiment:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main validate examples/copper_memo_context_sensitivity.yaml
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main expand examples/copper_memo_context_sensitivity.yaml --format json
```

Use `preview` for human-readable counts and `expand --format json` when you need the logical
run and attempt metadata.

## Run A Local-Only Experiment

Use this when you want persisted experiment records without provider calls:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main run examples/copper_memo_context_sensitivity.yaml --dry-run --local-only --format json
```

The output includes the experiment ID and slug. Use either value in later `compare`, `review`,
`score`, `judge`, `metric-adapters`, and `export` commands.

## Run The MVP Copper Demo

Use this for the smaller end-to-end context-sensitivity demo:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main demo copper-memo --export-dir /tmp/model-eval-copper-demo
```

It creates synthetic attempts, sample blind reviews, deterministic scores, analytics, and
Markdown/CSV/JSON exports.

## Run The V2 Demo

Use this for the complete local-only V2 workbench path:

```bash
PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3 uv run --extra dev python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
```

This covers benchmark suites, artifact preprocessing, replicated attempts, multi-reviewer
queues, synthetic judge calibration, metric adapters, advanced analytics, and exports without
provider keys.

## Inspect A Demo Or Experiment In The UI

Start the API and frontend against the database that contains the experiment:

```bash
export MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3
make api
```

```bash
make frontend
```

UI task map:

| Task | Screen |
| --- | --- |
| Create or inspect library records | **Library** |
| Build a manifest from library dimensions | **Experiment Builder** |
| Queue, retry, cancel, or inspect attempts | **Run Monitor** |
| Do blind pairwise review or reveal metadata after review | **Comparison Workspace** |
| Inspect lift, divergence, uncertainty, calibration, metric, and frontier rows | **Results** |

## Export A Blind Review Queue

Use this when reviewers should not see model, prompt, warmer, run, cost, or token metadata:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main review <experiment-id-or-slug> --blind
```

Keep metadata hidden until review is complete. The review export is intentionally answer-text
focused.

## Run Evaluators, Judges, And Metric Adapters

Run deterministic evaluators when an evaluator ID is already present in the stored experiment
snapshot:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main score <experiment-id-or-slug> --evaluator <evaluator-id>
```

Run an LLM judge in dry-run local-only mode first:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main judge <experiment-id-or-slug> --judge <judge-id> --dry-run --local-only
```

Run all compatible local metric adapters, or restrict to one adapter:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main metric-adapters <experiment-id-or-slug>
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main metric-adapters <experiment-id-or-slug> --adapter <adapter-slug>
```

Use `--dry-run` on metric adapters when you want compatibility validation without recording
new scores. Use `--force` only when intentionally recording another score for an already-scored
attempt/config snapshot.

## Run A Benchmark Suite

After a suite exists in a project library, create and execute an experiment from it:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main suite run v2_copper_benchmark_suite --project v2-copper-demo --format text
```

Add `--split <split-name>` to run one dataset split. Keep `--dry-run --local-only` unless you
are intentionally testing live providers.

## Import Or Export Promptfoo

Preview a Promptfoo config without persisting anything:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main import promptfoo <promptfoo.yaml> --preview-only
```

Persist compatible mapped library records:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main import promptfoo <promptfoo.yaml> --persist --project default
```

Export an experiment in Promptfoo format:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main export <experiment-id-or-slug> --format promptfoo
```

Always read import/export warnings. Conversation warmers, reliability replicates, randomized
run order, unsupported evaluator kinds, and unsupported controls can be lossy when mapped to
Promptfoo.

## Export Reports Or Trace Metadata

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main export <experiment-id-or-slug> --format markdown
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main export <experiment-id-or-slug> --format csv
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main export <experiment-id-or-slug> --format json
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main export <experiment-id-or-slug> --format otel-json
```

`otel-json` is metadata-only. It excludes raw prompts, manifests, artifacts, warmer messages,
request and response payloads, credentials, model outputs, terminal failure details, score
values, filenames, checksums, local paths, OCR text, and screenshot-derived private metadata.

## Live Provider Execution

Live provider execution is not the default. Before using `--live` or `--allow-provider`:

1. Set provider credentials in local environment variables.
2. Confirm `MODEL_EVAL_LOCAL_ONLY` and project provider allow/deny policy are intentional.
3. Use a small manifest and cost cap.
4. Run `preview` first and verify run and attempt counts.
5. Keep generated exports and provider response payloads out of public docs.
