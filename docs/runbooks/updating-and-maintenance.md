# Updating And Maintenance Runbook

Use this runbook when changing Model Eval itself. The goal is to keep code, tests, docs,
demos, and operational checklists aligned.

## Before You Edit

1. Work on an isolated branch or worktree, not directly on a protected branch.
2. Read the local task scope and avoid pulling unrelated cleanup into the change.
3. Check current status:

```bash
git status --short --branch
```

4. Identify the affected surfaces from the change map below.

## Change Map

| Change type | Usually update | Usually verify |
| --- | --- | --- |
| Persistence model or stored snapshot shape | `backend/model_eval_api/persistence/models.py`, `repositories.py`, `snapshots.py`, `schemas.py`, `alembic/versions`, backend tests, docs/data-model.md | Backend tests, Alembic upgrade on SQLite, relevant API/CLI smoke |
| API route or request/response payload | `backend/model_eval_api/main.py`, `schemas.py`, `frontend/src/api.ts`, frontend UI/tests, backend tests, docs/headless-workflow.md if exposed headlessly | Backend tests, frontend tests, API smoke |
| CLI workflow | `cli/model_eval_cli/main.py`, `backend/model_eval_api/headless.py`, tests, docs/headless-workflow.md, runbooks | CLI command smoke, backend tests |
| Frontend workflow | `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/styles.css`, `frontend/src/App.test.tsx`, runbooks if user workflow changes | `npm run test`, `npm run build`, browser smoke for material UI changes |
| Experiment expansion or manifest semantics | `manifest.py`, `run_generation.py`, `headless.py`, examples, tests, docs/data-model.md, docs/product-brief.md | Manifest validate/preview/expand, backend tests |
| Provider execution or safety gates | `executor.py`, `providers/`, `llm_judges.py`, `.env.example`, privacy docs, tests | Dry-run/local-only tests, targeted provider gate tests |
| Results analytics or scoring semantics | `results_analytics.py`, `deterministic_evaluators.py`, `metric_adapter_execution.py`, docs/results-analytics.md, exports, tests | Analytics/scoring tests, export smoke |
| Artifact preprocessing | `artifacts.py`, `artifact_types.py`, persistence models/snapshots, tests/fixtures, docs/privacy-repro-safety.md, docs/v2-demo.md | Artifact tests, export redaction tests |
| Demo behavior | `copper_demo.py`, `v2_demo.py`, examples, test fixtures, docs/copper-memo-demo.md, docs/v2-demo.md, runbooks | Demo command smoke and related tests |
| Dependencies | `pyproject.toml`, `uv.lock`, `frontend/package.json`, `frontend/package-lock.json`, docs if setup changes | Fresh install or lockfile-sensitive verification |
| CI or release gates | `.github/workflows/ci.yml`, README, runbooks, implementation checklists | Local equivalent of changed CI jobs |

## Docs To Revisit

Use this checklist when a change affects behavior:

- `README.md`: repository status, setup, core commands, verification.
- `docs/product-brief.md`: product boundary and positioning.
- `docs/architecture.md`: service boundaries, data flow, provider/artifact architecture.
- `docs/data-model.md`: entity semantics, supported designs, score/evaluator types.
- `docs/privacy-repro-safety.md`: provider egress, key handling, redaction, reproducibility.
- `docs/results-analytics.md`: analytics semantics and caveats.
- `docs/headless-workflow.md`: CLI commands, export behavior, Promptfoo, OpenTelemetry.
- `docs/copper-memo-demo.md` and `docs/v2-demo.md`: demo commands, expected counts, UI smoke path.
- `docs/implementation-task-list.md` and `docs/v2-implementation-task-list.md`: checklist status and verification.
- `FEATURE_INVENTORY.md`: implemented feature map when a feature surface changes.
- `docs/agentic-system/feature-model.md` and `.json`: repo model and known risks when doing feature-model maintenance.
- `docs/runbooks/`: operator steps whenever setup, workflow, verification, or maintenance changes.

Do not update every file mechanically. Update the files whose statements would become stale.

## Schema And Migration Updates

When changing persisted state:

1. Update the SQLAlchemy model.
2. Update repository and snapshot helpers that read or write the new field.
3. Add or update an Alembic migration in `alembic/versions`.
4. Update API schemas and CLI/export payloads if the field crosses a boundary.
5. Add tests that prove both new writes and existing reads behave correctly.
6. Run a migration smoke check:

```bash
PYTHONPATH=backend MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-migration-smoke.sqlite3 uv run alembic upgrade head
```

## CLI And Export Updates

When changing headless behavior:

1. Update `backend/model_eval_api/headless.py` first if the behavior is shared by CLI and API.
2. Update `cli/model_eval_cli/main.py` for the command surface.
3. Add backend tests around the behavior, including warning/error cases.
4. Update `docs/headless-workflow.md` and [Task Workflows](task-workflows.md).
5. Smoke the changed command with a disposable SQLite database.

## Frontend Updates

When changing the UI:

1. Update `frontend/src/api.ts` if backend payloads changed.
2. Update `frontend/src/App.tsx` and keep state transitions explicit.
3. Update `frontend/src/App.test.tsx` for user-visible behavior.
4. Run:

```bash
(cd frontend && npm run test)
(cd frontend && npm run build)
```

5. Do a browser smoke check for material UI changes, especially changes to Library, Experiment
   Builder, Run Monitor, Comparison Workspace, Results, or demo paths.

## Dependency Updates

Python dependency changes should update both:

- `pyproject.toml`
- `uv.lock`

Frontend dependency changes should update both:

- `frontend/package.json`
- `frontend/package-lock.json`

After dependency changes, run a fresh install path rather than relying only on existing local
packages.

## Release-Readiness Verification

Use this full local checklist before publishing code, schema, CLI, frontend, or demo changes:

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

For docs-only changes, `git diff --check` is the minimum. Run more of the checklist when the
docs include command examples or when the change could make documented behavior stale.

## Git Hygiene

- Do not commit generated databases, demo exports, `.venv`, `frontend/node_modules`,
  `frontend/dist`, caches, or `__pycache__`.
- Keep commits focused and describe test evidence in the PR.
- If a verification command creates local generated state, remove or ignore it before staging.
- Before committing, confirm the staged set:

```bash
git diff --cached --stat
git diff --cached --check
```

## When To Update Runbooks

Update runbooks when any of these change:

- Setup commands, environment variables, database defaults, or dependency managers.
- CLI command names, options, output formats, or safe defaults.
- UI navigation paths or screen responsibilities.
- Demo commands, expected counts, generated files, or smoke paths.
- Verification commands, CI expectations, migration commands, or generated-file policy.
