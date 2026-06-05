# Runbooks

These runbooks are the operator-facing layer for Model Eval. Use them when you need to run
the app, exercise a workflow, or update the system without rediscovering the moving parts.

## Which Runbook To Use

| Need | Runbook |
| --- | --- |
| Set up a checkout, install dependencies, run the demos, and open the UI | [Getting Started](getting-started.md) |
| Run a manifest, use the UI, review results, import/export Promptfoo, or build demos | [Task Workflows](task-workflows.md) |
| Change schemas, API routes, CLI commands, frontend surfaces, demos, docs, or dependencies | [Updating And Maintenance](updating-and-maintenance.md) |

## Operating Defaults

- Keep `MODEL_EVAL_LOCAL_ONLY=true` unless you are intentionally exercising provider calls.
- Provider keys belong in local environment variables only; do not persist them in the database or docs.
- Use `MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/<name>.sqlite3` for disposable smoke runs.
- Keep generated databases, demo exports, `.venv`, `frontend/node_modules`, `frontend/dist`,
  caches, and `__pycache__` directories out of git.
- When running Python directly from a checkout without an activated editable install, use
  `PYTHONPATH=backend:cli`.

## Minimum Verification Before Publishing Changes

For docs-only work, run at least:

```bash
git diff --check
```

For code, schema, CLI, or frontend changes, use the fuller checklist in
[Updating And Maintenance](updating-and-maintenance.md#release-readiness-verification).
