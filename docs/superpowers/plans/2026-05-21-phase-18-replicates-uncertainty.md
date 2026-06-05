# Phase 18 Replicates And Uncertainty Implementation Plan

Status note, 2026-05-26: this historical plan has been implemented. Current replicate and
uncertainty status is summarized in `../../v2-implementation-task-list.md`,
`../../results-analytics.md`, and `../../../FEATURE_INVENTORY.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class replicate metadata, nondeterminism summaries, simple confidence intervals, and uncertainty labels across API, UI, and exports.

**Architecture:** Keep the existing full-factorial run model and `replicate_index` behavior, then make retry-vs-reliability intent explicit through manifest controls and attempt metadata. Analytics will derive reliability samples from terminal root attempts only, so retry attempts remain recovery events and do not inflate nondeterminism or interval statistics.

**Tech Stack:** Python 3.11, SQLAlchemy, Pydantic, FastAPI, Typer, pytest, React, TypeScript, Vitest.

---

### Task 1: Manifest And Persistence Metadata

**Files:**
- Modify: `backend/model_eval_api/manifest.py`
- Modify: `backend/model_eval_api/persistence/models.py`
- Modify: `backend/model_eval_api/persistence/repositories.py`
- Create: `alembic/versions/b8d4f1a6c219_add_replicate_metadata.py`
- Test: `tests/test_manifest_contract.py`
- Test: `tests/test_executor_phase5.py`

- [ ] **Step 1: Write failing manifest tests**

Add tests that parse:

```python
{
    "name": "replicate controls",
    "cases": ["case"],
    "models": [{"id": "model", "provider": "openai", "model": "gpt"}],
    "system_prompts": ["system"],
    "warmers": ["none"],
    "design": {"replicates": 3},
    "controls": {"retry_failed": True, "reliability_replicates": 3},
}
```

Expected assertions: validation passes; `expand_manifest(...).replicate_groups[0]["sample_size"] == 3`; invalid `controls.reliability_replicates = 0` fails.

- [ ] **Step 2: Write failing executor tests**

Create an experiment with `design.replicates = 2` and a retryable failure on replicate 0. Assert each original attempt has `attempt_kind == "replicate"`, `replicate_group_id` set, and the retry has `attempt_kind == "retry"` while preserving the same `replicate_group_id`.

- [ ] **Step 3: Implement metadata**

Add `ControlsManifest.reliability_replicates`, extend `RunAttemptDefinition` and `RunAttempt` with `replicate_group_id` and `attempt_kind`, and teach `record_run_attempt` / `_create_retry_attempt` to stamp `"replicate"` versus `"retry"`.

- [ ] **Step 4: Run targeted checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_manifest_contract.py tests/test_executor_phase5.py
```

Expected: all selected tests pass.

### Task 2: Nondeterminism And Confidence Intervals

**Files:**
- Modify: `backend/model_eval_api/results_analytics.py`
- Modify: `tests/test_results_analytics_phase10.py`

- [ ] **Step 1: Write failing analytics tests**

Add tests for:

```python
analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
row = analytics["nondeterminism_by_dimension"]["model_config_slug"][0]
assert row["sample_count"] == 2
assert row["cost_usd_interval"]["label"] == "low_sample"
assert row["retry_attempt_count"] == 1
```

Also assert zero-sample intervals return `None` bounds and one-sample intervals use the observed value for lower and upper bounds with label `"single_sample"`.

- [ ] **Step 2: Implement transparent interval helpers**

Add helpers for Wilson score intervals for rates and sample mean intervals for numeric values. Use labels: `no_samples`, `single_sample`, `low_sample`, and `stable_sample`.

- [ ] **Step 3: Exclude retries from reliability samples**

Filter analytics reliability rows to attempts where `attempt_kind == "replicate"` and `parent_attempt_id is None`; separately count retries in `retry_attempt_count`.

- [ ] **Step 4: Run targeted analytics tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_results_analytics_phase10.py
```

Expected: all analytics tests pass.

### Task 3: Export And UI Surface

**Files:**
- Modify: `backend/model_eval_api/headless.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `docs/results-analytics.md`
- Test: `tests/test_exports_phase11.py`
- Test: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write failing export and UI tests**

Assert CSV headers include `replicate_group_id`, `attempt_kind`, `sample_count`, `variance`, `interval_lower`, `interval_upper`, and `uncertainty_label`. Assert JSON analytics includes `nondeterminism_by_dimension`. Add a React test that renders an uncertainty table row showing sample size and `low sample`.

- [ ] **Step 2: Implement export fields**

Add attempt metadata columns to attempt rows and add aggregate uncertainty rows derived from analytics.

- [ ] **Step 3: Implement UI labels**

Extend `ApiResultsAnalytics` types and render a compact results panel with sample size, variance, interval lower/upper, and uncertainty label.

- [ ] **Step 4: Update docs and checklist**

Document that intervals are transparent directional statistics, not calibrated quality claims. Mark Phase 18 tasks complete only after validation passes.

- [ ] **Step 5: Run full validation**

Run:

```bash
git diff --check
.venv/bin/python -m compileall backend cli
.venv/bin/python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
npm run build
npm test
```

Expected: all checks pass.

### Self-Review

- Spec coverage: manifest controls, run/attempt metadata, nondeterminism summaries, confidence intervals, UI labels, export fields, tests, docs, and checklist updates all map to tasks above.
- Placeholder scan: no task depends on TBD behavior; field names and labels are explicit.
- Type consistency: `replicate_group_id`, `attempt_kind`, `sample_count`, `variance`, `interval_lower`, `interval_upper`, and `uncertainty_label` are the shared names across backend, exports, and UI.
