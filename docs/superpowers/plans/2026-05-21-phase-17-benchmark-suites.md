# Phase 17 Benchmark Suites Implementation Plan

Status note, 2026-05-26: this historical plan has been implemented. Current benchmark-suite
status is summarized in `../../v2-implementation-task-list.md`, `../../v2-demo.md`, and
`../../../FEATURE_INVENTORY.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable benchmark suites with dataset splits, reproducible suite snapshots, API/CLI reruns, and a frontend suite management preview.

**Architecture:** Add versioned `BenchmarkSuite` library records plus `BenchmarkSuiteItem` membership rows. Suite previews and reruns build normal explicit full-factorial manifests from a locked suite snapshot, so existing run generation, execution, and export paths remain the source of truth.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, Typer, React/Vitest, pytest.

---

### Task 1: Persistence And Snapshots

**Files:**
- Modify: `backend/model_eval_api/persistence/models.py`
- Modify: `backend/model_eval_api/persistence/snapshots.py`
- Modify: `backend/model_eval_api/persistence/repositories.py`
- Create: `alembic/versions/g7h8i9j01217_add_benchmark_suites.py`
- Test: `tests/test_benchmark_suites_phase17.py`

- [ ] **Step 1: Write failing tests**

```python
def test_benchmark_suite_snapshot_locks_membership_and_excludes_archived_cases(session: Session) -> None:
    suite = create_benchmark_suite(...)
    preview = preview_benchmark_suite(session, suite=suite, split="dev")
    assert preview["preview"].logical_runs == 1
    assert preview["suite_snapshot"]["cases"][0]["split"] == "dev"
    assert "archived_case" not in [case["id"] for case in preview["suite_snapshot"]["cases"]]
```

Run: `.venv/bin/python -m pytest tests/test_benchmark_suites_phase17.py::test_benchmark_suite_snapshot_locks_membership_and_excludes_archived_cases`
Expected: FAIL because suite models/helpers do not exist.

- [ ] **Step 2: Add models and snapshot builder**

Add `Case.dataset_split`, `BenchmarkSuite`, `BenchmarkSuiteItem`, `build_benchmark_suite_snapshot`, and SQLAlchemy sync hooks. Allowed splits are `dev`, `validation`, `holdout`, and `archived`.

- [ ] **Step 3: Add repository helpers**

Add `create_benchmark_suite`, `list_benchmark_suites`, `archive_benchmark_suite_by_id`, `preview_benchmark_suite`, `benchmark_suite_manifest`, and `run_benchmark_suite`.

- [ ] **Step 4: Run targeted tests**

Run: `.venv/bin/python -m pytest tests/test_benchmark_suites_phase17.py`
Expected: PASS.

### Task 2: Manifest, API, And CLI

**Files:**
- Modify: `backend/model_eval_api/manifest.py`
- Modify: `backend/model_eval_api/schemas.py`
- Modify: `backend/model_eval_api/main.py`
- Modify: `backend/model_eval_api/headless.py`
- Modify: `cli/model_eval_cli/main.py`
- Test: `tests/test_benchmark_suites_phase17.py`
- Test: `tests/test_manifest_contract.py`

- [ ] **Step 1: Write failing tests**

```python
def test_manifest_accepts_suite_reference_and_split_filter() -> None:
    result = validate_manifest_payload({"name": "suite ref", "suite": {"id": "copper", "split": "dev"}})
    assert result.valid is True

def test_cli_suite_run_creates_local_only_experiment(monkeypatch, tmp_path) -> None:
    result = runner.invoke(cli_app, ["suite", "run", "copper", "--split", "validation", "--dry-run", "--local-only"])
    assert result.exit_code == 0
```

Run: `.venv/bin/python -m pytest tests/test_manifest_contract.py::test_manifest_accepts_suite_reference_and_split_filter tests/test_benchmark_suites_phase17.py::test_cli_suite_run_creates_local_only_experiment`
Expected: FAIL because suite schema/routes/CLI do not exist.

- [ ] **Step 2: Add manifest suite reference**

Add `BenchmarkSuiteReference`, optional `ExperimentManifest.suite`, and optional `DesignManifest.split`. Validation accepts suite-only manifests while normal manifests still require explicit dimensions.

- [ ] **Step 3: Add API endpoints**

Add `GET/POST /projects/{project_slug}/library/benchmark-suites`, `POST /projects/{project_slug}/library/benchmark-suites/{slug}/versions`, `DELETE /projects/{project_slug}/library/benchmark-suites/{suite_id}`, and `GET /projects/{project_slug}/library/benchmark-suites/{suite_id}/preview?split=...`.

- [ ] **Step 4: Add CLI command**

Add `suite_app = typer.Typer(...)` and `evalbench suite run <suite> --split <split> --dry-run --local-only --format json`.

- [ ] **Step 5: Run targeted tests**

Run: `.venv/bin/python -m pytest tests/test_benchmark_suites_phase17.py tests/test_manifest_contract.py`
Expected: PASS.

### Task 3: Frontend Suite Management

**Files:**
- Modify: `frontend/src/experimentBuilder.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/experimentBuilder.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
it("builds suite manifests with split filters", () => {
  const manifest = buildExperimentManifest({ ...draft, benchmarkSuiteId: "copper_suite", suiteSplit: "validation" });
  expect(manifest.suite).toEqual({ id: "copper_suite", split: "validation" });
});
```

Run: `npm test -- --run frontend/src/experimentBuilder.test.ts`
Expected: FAIL because suite draft fields do not exist.

- [ ] **Step 2: Add frontend types and API mapping**

Add `BenchmarkSuiteRecord`, include `benchmarkSuites` in `LibraryKind`/`LibraryState`, and map payloads to `/library/benchmark-suites`.

- [ ] **Step 3: Add UI controls**

Add a Benchmark suites tab with controls for membership IDs, split preview, and suite-run preview. Keep the UI dense and aligned with the existing Library/Experiment Builder layout.

- [ ] **Step 4: Run frontend tests**

Run: `npm test`
Expected: PASS.

### Task 4: Verification And PR

**Files:**
- Modify: `docs/v2-implementation-task-list.md`
- Modify: `docs/implementation-task-list.md`
- Optional modify: `FEATURE_INVENTORY.md`

- [ ] **Step 1: Mark Phase 17 tasks complete**

Update the Phase 17 checklist only after tests pass.

- [ ] **Step 2: Run full verification**

Run:
```bash
git diff --check
.venv/bin/python -m compileall backend cli
.venv/bin/python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
npm run build --prefix frontend
npm test --prefix frontend
```

Expected: all commands exit 0.

- [ ] **Step 3: Commit, push, open PR, request reviews**

Commit message: `[codex] Model Eval Phase 17: Benchmark Suites And Dataset Splits`.
