# Getting Started Runbook

Use this runbook to take a fresh checkout to a working local demo. The default path is
local-only and does not require provider keys.

## Prerequisites

- Python 3.11 or newer.
- Node.js and npm for the frontend.
- Docker Desktop or compatible Docker runtime if you want Postgres and Redis.
- `uv` is optional but useful for reproducible Python commands with `uv.lock`.

## 1. Prepare Local Environment

```bash
cp .env.example .env
```

Leave `MODEL_EVAL_LOCAL_ONLY=true` in place for normal development and demo work. Keep
`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` blank unless you are deliberately testing live
provider execution.

If you need Postgres and Redis instead of a temporary SQLite database:

```bash
docker compose up -d postgres redis
```

Stop them when finished:

```bash
docker compose down
```

## 2. Install Dependencies

The Makefile path creates an editable Python environment and installs frontend packages:

```bash
make install
source .venv/bin/activate
```

If `python3.11` is not your executable name:

```bash
make install PYTHON_BOOTSTRAP=/path/to/python3.11
source .venv/bin/activate
```

When using `uv` without activating the editable environment, prefer explicit checkout imports:

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
```

## 3. Run A First Verification Pass

```bash
git diff --check
uv run python -m compileall backend cli
PYTHONPATH=backend:cli uv run --extra dev pytest -q
(cd frontend && npm ci)
(cd frontend && npm run build)
(cd frontend && npm run test)
```

For a migration smoke check against disposable SQLite:

```bash
PYTHONPATH=backend MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-alembic-smoke.sqlite3 uv run alembic upgrade head
```

## 4. Preview The MVP Manifest

```bash
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
```

Expected shape:

- 1 case.
- 2 models.
- 2 system prompts.
- 4 warmers.
- 16 logical runs.
- 32 run attempts.

## 5. Build The V2 Demo

Use a temporary SQLite database so the checkout stays clean:

```bash
PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3 uv run --extra dev python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
```

Expected export files:

- `/tmp/model-eval-v2-demo/v2_copper_demo_report.md`
- `/tmp/model-eval-v2-demo/v2_copper_demo_report.csv`
- `/tmp/model-eval-v2-demo/v2_copper_demo_report.json`

## 6. Open The UI

Use the same database URL for the API that you used to build the demo:

```bash
export MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3
make api
```

In another terminal:

```bash
make frontend
```

Then open the frontend URL printed by Vite. The V2 smoke path is:

1. Open **Library** and confirm the V2 benchmark suite exists.
2. Open **Run Monitor** and find the completed V2 copper benchmark experiment.
3. Open **Comparison Workspace** and keep metadata hidden until review is complete.
4. Open **Results** and inspect uncertainty, judge calibration, metric adapter, divergence, and frontier rows.

## Troubleshooting

- If the CLI cannot import `model_eval_cli`, add `PYTHONPATH=backend:cli`.
- If Alembic cannot import `model_eval_api`, add `PYTHONPATH=backend`.
- If a demo leaves `model_eval.sqlite3` in the checkout, remove it unless a later task explicitly
  asks for a checked-in sample database.
- If frontend commands fail after dependency changes, rerun `(cd frontend && npm ci)`.
