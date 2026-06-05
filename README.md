# Model Eval

Model Eval is a workbench for measuring how prior conversational context changes model behavior on the same final task.

The core product wedge is conversation warmers as first-class experimental variables. A warmer is a structured conversation history that can be varied while holding the final task, model, system prompt, and artifacts constant. The app is designed to answer controlled questions such as:

- Does an expert-user warmer improve output quality, or just make answers longer?
- Which model is most robust across low-context, expert, adversarial, and misleading warmers?
- Does high reasoning effort justify its cost for a given task family?
- Which configuration is cheapest among outputs that pass human review?

## Repository Status

This repository now implements the V1/MVP workflow and the V2 local-only demo
path. The implemented surface includes:

- Product, architecture, data-model, privacy, analytics, V2 demo, and feature-inventory docs.
- Versioned project libraries for cases, artifacts, system prompts, warmers, model configs,
  deterministic evaluators, LLM judges, metric adapters, benchmark suites, reviewers, and
  failure taxonomies.
- A FastAPI backend with SQLAlchemy/Alembic persistence, immutable snapshots, execution
  controls, provider adapters, review queues, analytics, exports, Promptfoo interop, and
  OpenTelemetry-compatible metadata export.
- A Typer CLI (`evalbench`) for manifest preview/run, comparison, review export, deterministic
  scoring, judge execution, metric adapters, benchmark-suite runs, Promptfoo import, exports,
  and local demo generation.
- A React/Vite workbench covering library management, experiment building, run monitoring,
  blind review, results analytics, artifact preprocessing, judges, metric adapters, benchmark
  suites, Promptfoo import/export, and V2 demo smoke paths.
- Local-only copper memo and V2 benchmark-suite demos that can be rerun without provider keys
  or live model calls.

V2 is complete against the backlog in `docs/implementation-task-list.md` and
`docs/v2-implementation-task-list.md`. V3 items remain intentionally out of scope unless they
are explicitly promoted.

## Product Shape

The product has three operating modes:

1. Playground: fast, disposable exploration for one-off prompt/model/warmer checks.
2. Experiment: immutable, versioned, reproducible records of controlled studies.
3. Benchmark Suite: reusable regression packs that can be rerun as prompts, warmers, artifacts, or models change.

The V1/MVP workflow covers:

- System prompt library.
- Conversation warmer library.
- Case library.
- Model config library.
- Experiment builder with full-factorial run generation.
- OpenAI and Anthropic provider adapter boundaries.
- Async executor interface.
- Run snapshot storage.
- Side-by-side comparison.
- Blind pairwise review.
- Manual pass/fail, notes, and failure tags.
- Cost, token, and latency tracking.
- Markdown, CSV, and JSON export.
- CLI manifest runner.

V2 extends that base with LLM judge configuration and calibration, multi-reviewer queues,
benchmark suites and dataset splits, replicated uncertainty-aware attempts, artifact
preprocessing, local RAG/DeepEval-style metric adapters, Promptfoo import/export,
metadata-only OpenTelemetry export, cost-quality frontier views, and the local-only V2 demo.

## Local Development

Environment:

```bash
cp .env.example .env
```

`MODEL_EVAL_LOCAL_ONLY=true` is the default safety setting. Provider keys are optional for
local dry-runs, demo generation, preview commands, tests, and seed data.

Provider keys stay in local environment variables only; they are not stored in the database.
Keep `MODEL_EVAL_LOCAL_ONLY=true` unless you are intentionally testing provider execution
with mocked or explicitly configured clients.

Postgres and Redis:

```bash
docker compose up -d postgres redis
```

The compose file starts Postgres on `localhost:5432` with database/user/password `model_eval`, and Redis on `localhost:6379`. These match the local URLs in `.env.example`. Stop them with:

```bash
docker compose down
```

Install:

```bash
make install
source .venv/bin/activate
```

`make install` uses `python3.11` to create `.venv`. If your Python 3.11 executable has a different name, run `make install PYTHON_BOOTSTRAP=/path/to/python3.11`.

The repository also includes `uv.lock` for reproducible Python dependency resolution. When
using `uv`, run Python commands as `uv run ...` or include `--extra dev` for dev-only tools
when needed.

Backend:

```bash
make api
```

CLI:

```bash
make preview-example
python -m model_eval_cli.main seed copper-memo --format json
python -m model_eval_cli.main demo copper-memo --export-dir /tmp/model-eval-copper-demo
python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
```

Frontend:

```bash
make frontend
```

Common commands:

```bash
make lint
make test
make build
```

## Current Verification

Latest local verification for the docs/readiness branch was recorded on 2026-05-26:

```bash
git diff --check
uv run python -m compileall backend cli
PYTHONPATH=backend:cli uv run python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
uv run --extra dev ruff check .
PYTHONPATH=backend:cli uv run --extra dev pytest -q
(cd frontend && npm ci)
(cd frontend && npm run build)
(cd frontend && npm run test)
PYTHONPATH=backend MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-docs-alembic.sqlite3 uv run alembic upgrade head
PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo-docs-readiness.sqlite3 uv run --extra dev python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo-docs-readiness
```

The V2 demo is verified by the test suite and can also be exercised manually with a
temporary SQLite database:

```bash
PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo.sqlite3 uv run --extra dev python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo
```

## Design Docs

- [Product brief](docs/product-brief.md)
- [Architecture](docs/architecture.md)
- [ADR 0001: Warmers as first-class entities](docs/adr/0001-warmers-as-first-class-entities.md)
- [Data model](docs/data-model.md)
- [MVP roadmap](docs/mvp-roadmap.md)
- [Implementation task list](docs/implementation-task-list.md)
- [V2 implementation task list](docs/v2-implementation-task-list.md)
- [V2 demo](docs/v2-demo.md)
- [Runbooks](docs/runbooks/README.md)
- [Feature inventory](FEATURE_INVENTORY.md)
- [Privacy, reproducibility, and safety](docs/privacy-repro-safety.md)
- [Initial design spec](docs/superpowers/specs/2026-05-20-model-eval-design.md)
