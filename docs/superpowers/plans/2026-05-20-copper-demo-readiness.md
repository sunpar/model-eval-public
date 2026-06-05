# Copper Memo Demo Readiness Implementation Plan

Status note, 2026-05-26: this historical plan has been implemented. The current demo
workflow is documented in `../../copper-memo-demo.md` and `../../v2-demo.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a rerunnable, local-only copper memo demo that proves warmer-driven context sensitivity through persisted demo data, blind review, analytics, exports, and UI workflow documentation.

**Architecture:** Add a backend demo builder that seeds the copper memo library objects, creates the full-factorial experiment, populates synthetic local-only attempts, records sample blind human reviews, and reuses the existing analytics/export stack. Keep UI changes small: let an existing API experiment from Run Monitor become the selected experiment for Comparison Workspace and Results. Document the end-to-end local workflow rather than committing generated demo outputs.

**Tech Stack:** FastAPI support code, Typer CLI, SQLAlchemy repositories, existing deterministic evaluator/analytics/export services, React/Vite/TypeScript tests, Markdown docs.

---

### Task 1: Backend Demo Builder And CLI

**Files:**
- Create: `backend/model_eval_api/copper_demo.py`
- Modify: `cli/model_eval_cli/main.py`
- Test: `tests/test_copper_demo_phase12.py`

- [x] **Step 1: Write failing backend and CLI tests**

```python
def test_build_copper_memo_demo_creates_full_reviewed_demo(session: Session, tmp_path: Path) -> None:
    summary = build_copper_memo_demo(session, export_dir=tmp_path)
    assert summary["preview"]["logical_runs"] == 16
    assert summary["preview"]["run_attempts"] == 32
    assert summary["review_set"]["item_count"] == 16
    assert summary["review_set"]["completed_item_count"] == 16
    assert summary["analytics"]["warmer_lift"]
    assert summary["analytics"]["context_sensitivity"]
    assert summary["analytics"]["failure_tag_frequency"]
    assert summary["analytics"]["summary"]["average_cost_usd"] is not None
    assert summary["analytics"]["summary"]["average_latency_ms"] is not None
    assert (tmp_path / "copper_memo_demo.md").exists()
    assert (tmp_path / "copper_memo_demo.csv").exists()
    assert (tmp_path / "copper_memo_demo.json").exists()
```

Run: `.venv/bin/python -m pytest tests/test_copper_demo_phase12.py -q`
Expected: FAIL because `model_eval_api.copper_demo` does not exist.

- [x] **Step 2: Implement the builder**

Create `build_copper_memo_demo(session, export_dir=None, project_slug="default")` that creates library records from `copper_memo_seed_payload()`, parses `examples/copper_memo_context_sensitivity.yaml`, creates the experiment, fills 32 local synthetic attempts with output text/tokens/cost/latency, records deterministic scores, creates a blind pairwise review set, records sample human decisions, aggregates results, and writes exports when requested.

- [x] **Step 3: Add CLI command**

Add `evalbench demo copper-memo --format json --export-dir <dir>` so a local DB can be populated from the command line without live provider calls.

- [x] **Step 4: Verify task**

Run: `.venv/bin/python -m pytest tests/test_copper_demo_phase12.py -q`
Expected: PASS.

### Task 2: UI Selection For Existing Demo Experiments

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.test.tsx`

- [x] **Step 1: Write failing UI test**

Add a test that opens Run Monitor, clicks a new action for the API-backed copper experiment, lands in Comparison Workspace, and can create a blind review set for that existing experiment ID.

Run: `npm test -- App.test.tsx`
Expected: FAIL because the action is not implemented.

- [x] **Step 2: Implement monitor-to-review selection**

Pass an `onUseExperiment` callback into `RunMonitorScreen`, add an icon button for API experiments, create a `DraftExperimentRecord` with the API experiment id/name/slug and monitor-derived preview counts, then route to Comparison Workspace.

- [x] **Step 3: Verify task**

Run: `npm test -- App.test.tsx`
Expected: PASS.

### Task 3: Demo Workflow Docs And Checklist

**Files:**
- Create: `docs/copper-memo-demo.md`
- Modify: `docs/implementation-task-list.md`

- [x] **Step 1: Document the workflow**

Cover `evalbench preview`, `evalbench demo copper-memo --export-dir`, Run Monitor refresh, Use for review/results, Comparison Workspace blind review, Results analytics, and Markdown/CSV/JSON export locations.

- [x] **Step 2: Mark Phase 12 tasks complete**

Mark only Phase 12 checkboxes complete. Leave Phase 13 and all V2/V3 backlog untouched.

- [x] **Step 3: Verify task**

Run the full phase validation matrix before committing.
