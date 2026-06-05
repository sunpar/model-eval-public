# Model Eval V2 PR-Sized Task List

Status: completed through Phase 25. This file is retained as the execution record for the
V2 buildout; it is no longer the source of remaining V2 work.

This task list decomposed the unchecked V2 phases from
`docs/v2-implementation-task-list.md` into units that fit
the local autonomous task PR runner workflow.

The runner contract below remains useful for future phase-style work. Completed V2 evidence
now lives in `docs/v2-implementation-task-list.md`, `docs/v2-demo.md`, and
`FEATURE_INVENTORY.md`.

## Runner Contract

- Start every task from the latest `origin/main`.
- Use one branch, one worktree, and one PR per task.
- Skip checked tasks and resume existing matching branches, worktrees, or PRs.
- Preserve local-only and dry-run behavior in tests. Do not require provider keys.
- Do not commit private prompts, private artifacts, model outputs, raw traces, or
  generated export payloads.
- Keep V3 backlog items out of scope unless explicitly promoted.
- After each merge, refresh `origin/main` before starting dependent tasks.
- Treat UI changes as requiring frontend tests and browser smoke coverage when
  practical.
- Treat migrations as requiring an Alembic upgrade check.

## Shared Validation Commands

Run the most relevant targeted commands during implementation, then run the full
ongoing checklist before each PR unless a command is unavailable and a substitute
is documented in the PR body.

- `git diff --check`
- `python -m compileall backend cli`
- `python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml`
- `ruff check .`
- `pytest`
- `npm run build` from `frontend/`
- `npm test` from `frontend/`
- Alembic upgrade check when migrations are added.
- Browser smoke test after material UI changes.
- Host-service smoke check when Postgres, Redis, API, worker, or setup docs change.

## Dependency Map

- Phase 19 tasks are sequential: `19A -> 19B -> 19C`.
- Phase 20 tasks are sequential:
  `20A -> 20B -> 20C -> 20D -> 20E -> 20F -> 20G`.
- Phase 21 depends on Phase 20 derived artifact records for artifact-backed
  adapter inputs: `21A -> 21B`.
- Phase 22 depends on Phase 21 for metric-adapter assertion mapping:
  `22A -> 22B`.
- Phase 23 depends on Phase 20 so trace exports can include artifact
  preprocessing events: `23A -> 23B`.
- Phase 24 depends on Phases 18, 19, 21, and 22.
- Phase 25 depends on all preceding V2 tasks.
- After Phase 21, Promptfoo and OpenTelemetry work can proceed in parallel if the
  runner has clean independent worktrees: `22A` and `23A` are the first safe
  parallel candidates.

## Task Units

- [x] `task_id: v2-phase19a-divergence-foundation`

  **Title:** Deterministic Divergence Metrics Foundation

  **Branch:** `codex/v2-phase19a-divergence-foundation`

  **PR title:** `[codex] Add deterministic divergence metrics foundation`

  **Source:** Phase 19, tasks 1-3 and part of task 8.

  **Order:** `19.1`

  **Dependencies:** Phase 18 merged.

  **Parallel group:** sequential Phase 19.

  **Scope:**

  - Add typed local divergence score criteria for semantic overlap,
    section-structure, token-length, confidence-language, and failure-mode spread.
  - Implement semantic-overlap divergence using deterministic local token-set and
    keyphrase overlap against the no-warmer baseline, with explicit
    `metric_source: deterministic_semantic_overlap` warnings so the runner does
    not treat it as calibrated semantic judging.
  - Implement deterministic text-analysis helpers for section headings, required
    section order, token-length deltas, and confidence-language markers.
  - Record deterministic divergence scores through the existing `Score` table
    rather than adding a new score table unless the implementation proves a
    schema change is necessary.
  - Ensure score payloads include `metric_source`, `comparison_scope`,
    `baseline_attempt_id`, `comparison_attempt_id`, `value`, `label`, and
    `warning`.

  **Likely files:**

  - `backend/model_eval_api/deterministic_evaluators.py`
  - `backend/model_eval_api/results_analytics.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `tests/test_deterministic_evaluators_phase9.py`
  - `tests/test_results_analytics_phase10.py`
  - `docs/results-analytics.md`

  **Acceptance criteria:**

  - Section-structure divergence works when outputs contain markdown headings.
  - Semantic divergence produces a typed score or analytics row from local
    lexical/keyphrase overlap and labels the method as an uncalibrated
    deterministic heuristic.
  - Token-length divergence is deterministic and uses local text only.
  - Confidence-language divergence flags hedging, unsupported certainty, and
    missing confidence language without LLM calls.
  - Failure-mode spread can summarize existing human failure tags across warmers.
  - Zero-baseline, one-output, and missing-output cases return explainable
    `unavailable` labels instead of crashing.

  **Validation:**

  - `pytest tests/test_deterministic_evaluators_phase9.py tests/test_results_analytics_phase10.py`
  - `ruff check backend/model_eval_api/deterministic_evaluators.py backend/model_eval_api/results_analytics.py tests/test_deterministic_evaluators_phase9.py tests/test_results_analytics_phase10.py`

  **Out of scope:**

  - Claim/conclusion semantic judging.
  - Results UI tables.
  - Promptfoo or OpenTelemetry exports.

- [x] `task_id: v2-phase19b-claim-conclusion-carryover`

  **Title:** Claim, Conclusion, And Carryover Divergence Signals

  **Branch:** `codex/v2-phase19b-claim-conclusion-carryover`

  **PR title:** `[codex] Add claim and carryover divergence signals`

  **Source:** Phase 19, tasks 4-5 and part of task 8.

  **Order:** `19.2`

  **Dependencies:** `v2-phase19a-divergence-foundation`.

  **Parallel group:** sequential Phase 19.

  **Scope:**

  - Add interfaces for claim and conclusion divergence that can consume existing
    Phase 15 judge outputs when present.
  - Add deterministic fallback rows when judge-backed claim/conclusion evidence
    is unavailable.
  - Add conversation carryover audit rows that classify warmer evidence as
    `reused`, `ignored`, `overfit`, or `unknown` when structured judge output or
    local heuristics support the label.
  - Keep all outputs local-only and fixture-driven in tests.

  **Likely files:**

  - `backend/model_eval_api/results_analytics.py`
  - `backend/model_eval_api/llm_judges.py`
  - `backend/model_eval_api/headless.py`
  - `tests/test_llm_judge_execution_phase15.py`
  - `tests/test_results_analytics_phase10.py`
  - `docs/results-analytics.md`

  **Acceptance criteria:**

  - Existing judge rubric or structured-output scores can feed claim and
    conclusion divergence rows without rerunning a judge.
  - Missing judge evidence produces deterministic fallback rows with explicit
    `metric_source: deterministic_fallback` and a warning label.
  - Carryover audit rows include case, model config, system prompt, warmer,
    source evidence, status, and explanation.
  - Tests cover mixed metric sources and missing no-warmer baselines.

  **Validation:**

  - `pytest tests/test_llm_judge_execution_phase15.py tests/test_results_analytics_phase10.py`
  - `ruff check backend/model_eval_api/results_analytics.py backend/model_eval_api/llm_judges.py backend/model_eval_api/headless.py`

  **Out of scope:**

  - New live judge execution behavior.
  - Frontend rendering.

- [x] `task_id: v2-phase19c-divergence-results-ui`

  **Title:** Divergence Analytics API, UI, And Exports

  **Branch:** `codex/v2-phase19c-divergence-results-ui`

  **PR title:** `[codex] Surface advanced divergence analytics`

  **Source:** Phase 19, tasks 6-8.

  **Order:** `19.3`

  **Dependencies:** `v2-phase19b-claim-conclusion-carryover`.

  **Parallel group:** sequential Phase 19.

  **Scope:**

  - Extend analytics payloads with divergence rows grouped by case, model,
    system prompt, and warmer.
  - Add frontend API types and Results tables for divergence and carryover audit
    rows.
  - Add warnings when rows are based on shallow deterministic heuristics instead
    of calibrated judge or human labels.
  - Add Markdown, CSV, and JSON export rows for divergence metrics.

  **Likely files:**

  - `backend/model_eval_api/results_analytics.py`
  - `backend/model_eval_api/headless.py`
  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/App.test.tsx`
  - `docs/results-analytics.md`
  - `tests/test_results_analytics_phase10.py`
  - `tests/test_exports_phase11.py`

  **Acceptance criteria:**

  - API, UI, and exports use the same analytics source of truth.
  - Rows distinguish deterministic heuristic, judge-backed, and human-backed
    sources.
  - UI shows sample counts and warning labels without implying false precision.
  - Export shape is stable and covered by tests.

  **Validation:**

  - `pytest tests/test_results_analytics_phase10.py tests/test_exports_phase11.py`
  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of the Results page.

  **Out of scope:**

  - Cost-quality frontier redesign. That belongs to Phase 24.

- [x] `task_id: v2-phase20a-preprocessing-records`

  **Title:** Artifact Preprocessing Records And Snapshots

  **Branch:** `codex/v2-phase20a-preprocessing-records`

  **PR title:** `[codex] Add artifact preprocessing records`

  **Source:** Phase 20, tasks 1 and snapshot portions of tasks 8-9.

  **Order:** `20.1`

  **Dependencies:** Phase 19 complete.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Add persistence for preprocessing runs that link source artifact ID, parser
    name, parser version, derived artifact IDs, extraction timestamp, checksums,
    local storage URI, status, and error metadata.
  - Snapshot derived artifact records so historical runs remain reproducible.
  - Keep source files and derived previews in local artifact storage, not in git.
  - Add repository helpers for creating, completing, failing, listing, and
    snapshotting preprocessing records.

  **Likely files:**

  - `backend/model_eval_api/persistence/models.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `backend/model_eval_api/persistence/snapshots.py`
  - `backend/model_eval_api/artifacts.py`
  - `backend/model_eval_api/artifact_types.py`
  - `tests/test_artifacts_phase3.py`
  - Alembic migration

  **Acceptance criteria:**

  - Derived artifact metadata is immutable once attached to a preprocessing
    record.
  - Missing local source files are reported as preprocessing failures with
    non-secret error metadata.
  - Checksums are stable across repeated preprocessing of the same source.
  - Alembic migration upgrades from current head.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/artifacts.py backend/model_eval_api/artifact_types.py backend/model_eval_api/persistence tests/test_artifacts_phase3.py`
  - Alembic upgrade check.

  **Out of scope:**

  - PDF text extraction and screenshot generation.
  - API and UI controls.

- [x] `task_id: v2-phase20b-pdf-text-extraction`

  **Title:** PDF Text Preprocessing Extractor

  **Branch:** `codex/v2-phase20b-pdf-text-extraction`

  **PR title:** `[codex] Add local PDF text preprocessing`

  **Source:** Phase 20, task 2 and related tests.

  **Order:** `20.2`

  **Dependencies:** `v2-phase20a-preprocessing-records`.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Add local PDF text extraction with page-level text metadata.
  - Use tiny committed fixtures only. Do not commit private PDFs/images or
    generated derived outputs.
  - If a new dependency is necessary, keep it minimal, document why standard
    library support is insufficient, update `pyproject.toml`, and avoid any live
    external services.

  **Likely files:**

  - `backend/model_eval_api/artifacts.py`
  - `backend/model_eval_api/artifact_types.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `tests/test_artifacts_phase3.py`
  - `pyproject.toml` only if an extractor dependency is required
  - `docs/data-model.md`

  **Acceptance criteria:**

  - PDF text output records page number, character count, checksum, and derived
    artifact ID.
  - Empty pages and encrypted/unreadable PDFs fail with non-secret preprocessing
    error metadata.
  - Repeated extraction of the same fixture produces stable page metadata and
    checksums.
  - Tests do not require external binaries, provider keys, or private source
    files.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/artifacts.py backend/model_eval_api/artifact_types.py tests/test_artifacts_phase3.py`

  **Out of scope:**

  - PDF screenshots, image normalization, OCR, retrieval chunking, paper cards,
    and UI controls.

- [x] `task_id: v2-phase20c-visual-ocr-extractors`

  **Title:** Visual And OCR Preprocessing Extractors

  **Branch:** `codex/v2-phase20c-visual-ocr-extractors`

  **PR title:** `[codex] Add visual and OCR preprocessing`

  **Source:** Phase 20, tasks 3-4 and related tests.

  **Order:** `20.3`

  **Dependencies:** `v2-phase20b-pdf-text-extraction`.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Add PDF page screenshot generation with per-page image metadata.
  - Add image normalization metadata for direct image artifacts.
  - Add OCR text capture when a local OCR backend is configured, and deterministic
    `ocr_unavailable` metadata when OCR is not available.
  - Keep generated screenshots and normalized images in local artifact storage,
    not in git.

  **Likely files:**

  - `backend/model_eval_api/artifacts.py`
  - `backend/model_eval_api/artifact_types.py`
  - `tests/test_artifacts_phase3.py`
  - `docs/data-model.md`
  - `pyproject.toml` only if a visual extraction dependency is required

  **Acceptance criteria:**

  - PDF screenshot output records page number, image dimensions, checksum, and
    derived artifact ID.
  - Image preprocessing records original dimensions, normalized dimensions, and
    checksum.
  - OCR metadata distinguishes captured text from `ocr_unavailable`.
  - Tests do not require external services, provider keys, or private source
    files.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/artifacts.py backend/model_eval_api/artifact_types.py tests/test_artifacts_phase3.py`

  **Out of scope:**

  - Selected figure/table records, retrieval chunks, paper cards, run input
    snapshots, and UI controls.

- [x] `task_id: v2-phase20d-figure-table-records`

  **Title:** Figure And Table Derived Artifact Records

  **Branch:** `codex/v2-phase20d-figure-table-records`

  **PR title:** `[codex] Add figure and table derived artifact records`

  **Source:** Phase 20, task 5 and related tests.

  **Order:** `20.4`

  **Dependencies:** `v2-phase20c-visual-ocr-extractors`.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Add selected figure extraction records with source page, region metadata,
    parser version, derived artifact ID, and source checksum.
  - Add table extraction records with source page, region metadata, parser
    version, structured table metadata, derived artifact ID, and source checksum.
  - Keep extracted figure/table previews in local artifact storage, not in git.

  **Likely files:**

  - `backend/model_eval_api/artifacts.py`
  - `backend/model_eval_api/artifact_types.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `backend/model_eval_api/persistence/snapshots.py`
  - `tests/test_artifacts_phase3.py`
  - `docs/data-model.md`

  **Acceptance criteria:**

  - Figure and table records preserve source artifact ID, page number, region,
    parser name/version, source checksum, and derived artifact ID.
  - Missing or invalid regions fail with explicit preprocessing errors.
  - Snapshot output is immutable and stable across repeated fixture processing.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/artifacts.py backend/model_eval_api/artifact_types.py backend/model_eval_api/persistence tests/test_artifacts_phase3.py`

  **Out of scope:**

  - Retrieval chunks, paper cards, run input snapshots, and UI controls.

- [x] `task_id: v2-phase20e-retrieval-paper-cards`

  **Title:** Retrieval Chunks And Paper Card Artifacts

  **Branch:** `codex/v2-phase20e-retrieval-paper-cards`

  **PR title:** `[codex] Add retrieval chunks and paper cards`

  **Source:** Phase 20, tasks 6-7 and related tests.

  **Order:** `20.5`

  **Dependencies:** `v2-phase20d-figure-table-records`.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Add retrieval chunk records with chunk text, source offsets, source checksum,
    parser version, and derived artifact ID.
  - Add paper card summary artifacts with citation and section metadata.
  - Generate paper card summaries from deterministic local fixtures or local
    extraction metadata only, not live LLM calls.

  **Likely files:**

  - `backend/model_eval_api/artifacts.py`
  - `backend/model_eval_api/artifact_types.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `backend/model_eval_api/persistence/snapshots.py`
  - `tests/test_artifacts_phase3.py`
  - `docs/data-model.md`

  **Acceptance criteria:**

  - Retrieval chunks preserve chunk text, source offsets, source checksum, and
    derived artifact ID.
  - Paper card artifacts include citation and section metadata and are
    reproducible from local inputs.
  - Tests cover checksum stability, missing source text, and snapshot
    immutability.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/artifacts.py backend/model_eval_api/artifact_types.py backend/model_eval_api/persistence tests/test_artifacts_phase3.py`

  **Out of scope:**

  - Run input snapshots and UI controls.

- [x] `task_id: v2-phase20f-run-input-snapshots`

  **Title:** Derived Artifact Run Input Snapshots

  **Branch:** `codex/v2-phase20f-run-input-snapshots`

  **PR title:** `[codex] Add derived artifact input snapshots`

  **Source:** Phase 20, model-input portions of task 9.

  **Order:** `20.6`

  **Dependencies:** `v2-phase20e-retrieval-paper-cards`.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Extend run/input snapshots so each run records whether it saw direct files,
    extracted PDF text, page screenshots, selected figures, tables, OCR text,
    retrieval chunks, paper cards, or a mixed derived bundle.
  - Add derived bundle checksums based on source checksum, parser versions, input
    mode, and selected derived artifact IDs.
  - Reject unsupported mixed modes before provider execution.

  **Likely files:**

  - `backend/model_eval_api/executor.py`
  - `backend/model_eval_api/manifest.py`
  - `backend/model_eval_api/artifact_types.py`
  - `backend/model_eval_api/persistence/snapshots.py`
  - `tests/test_artifacts_phase3.py`
  - `tests/test_executor_phase5.py`
  - `tests/test_manifest_contract.py`
  - `docs/data-model.md`

  **Acceptance criteria:**

  - Executor snapshots include the concrete input mode and derived artifact IDs.
  - Mixed derived bundles have stable checksums.
  - Unsupported mixed modes fail before provider execution.
  - Tests cover model input snapshots for every supported input mode.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py tests/test_executor_phase5.py tests/test_manifest_contract.py`
  - `python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml`
  - `ruff check backend/model_eval_api/executor.py backend/model_eval_api/manifest.py backend/model_eval_api/artifact_types.py backend/model_eval_api/persistence/snapshots.py tests/test_artifacts_phase3.py tests/test_executor_phase5.py tests/test_manifest_contract.py`

  **Out of scope:**

  - API and frontend preprocessing controls.

- [x] `task_id: v2-phase20g-preprocessing-api-ui`

  **Title:** Artifact Preprocessing API And UI Controls

  **Branch:** `codex/v2-phase20g-preprocessing-api-ui`

  **PR title:** `[codex] Add artifact preprocessing API and UI`

  **Source:** Phase 20, task 8 and final task 9 coverage.

  **Order:** `20.7`

  **Dependencies:** `v2-phase20f-run-input-snapshots`.

  **Parallel group:** sequential Phase 20.

  **Scope:**

  - Add API endpoints for starting preprocessing, listing preprocessing records,
    inspecting derived artifacts, and selecting run input mode.
  - Add frontend controls in the artifact/library and experiment builder flows
    for preprocessing, derived artifact inspection, and input-mode selection.
  - Keep generated previews local and reference them by metadata, not committed
    files.

  **Likely files:**

  - `backend/model_eval_api/main.py`
  - `backend/model_eval_api/schemas.py`
  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/experimentBuilder.ts`
  - `frontend/src/App.test.tsx`
  - `frontend/src/experimentBuilder.test.ts`
  - `tests/test_artifacts_phase3.py`
  - `docs/data-model.md`

  **Acceptance criteria:**

  - Users can start preprocessing for an artifact and inspect derived outputs.
  - Experiment builder can choose a supported input mode for a case/artifact.
  - UI can inspect derived outputs from PDF text, screenshots, figures, tables,
    OCR, retrieval chunks, and paper cards without exposing private file
    contents by default.
  - UI communicates local-only storage and missing-file failures without exposing
    private local paths unnecessarily.
  - Browser smoke confirms the preprocessing controls render and do not overlap
    existing Library or builder controls.

  **Validation:**

  - `pytest tests/test_artifacts_phase3.py`
  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of Library artifacts and experiment builder.

  **Out of scope:**

  - Metric adapters that consume derived artifacts.

- [x] `task_id: v2-phase21a-metric-adapter-registry`

  **Title:** Metric Adapter Configs And Local Registry

  **Branch:** `codex/v2-phase21a-metric-adapter-registry`

  **PR title:** `[codex] Add metric adapter registry`

  **Source:** Phase 21, tasks 1-3 and part of task 7.

  **Order:** `21.1`

  **Dependencies:** Phase 20 complete.

  **Parallel group:** sequential Phase 21.

  **Scope:**

  - Add `MetricAdapterConfig` persistence with adapter kind, version, required
    input fields, output schema, local-only capability metadata, archived status,
    and immutable snapshot JSON.
  - Add an adapter registry with local implementations for retrieval precision,
    citation coverage, groundedness checklist, and answer relevance.
  - Add a DeepEval-style wrapper contract that maps external-shaped metric
    results into Model Eval score records without importing external SDKs in
    tests.

  **Likely files:**

  - `backend/model_eval_api/persistence/models.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `backend/model_eval_api/persistence/snapshots.py`
  - `backend/model_eval_api/deterministic_evaluators.py`
  - `backend/model_eval_api/schemas.py`
  - `tests/test_metric_adapters_phase21.py`
  - Alembic migration

  **Acceptance criteria:**

  - Adapter config versions are immutable and project-scoped.
  - Required-input validation distinguishes answer text, retrieved chunks,
    citations, reference answers, and derived artifacts.
  - Local metric implementations produce deterministic typed score payloads.
  - DeepEval-style mapping is tested with fixture dictionaries only.

  **Validation:**

  - `pytest tests/test_metric_adapters_phase21.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api tests/test_metric_adapters_phase21.py`
  - Alembic upgrade check.

  **Out of scope:**

  - CLI/API/UI execution controls.
  - Promptfoo import/export.

- [x] `task_id: v2-phase21b-metric-adapter-execution`

  **Title:** Metric Adapter Execution Surfaces

  **Branch:** `codex/v2-phase21b-metric-adapter-execution`

  **PR title:** `[codex] Add metric adapter execution`

  **Source:** Phase 21, tasks 4-7.

  **Order:** `21.2`

  **Dependencies:** `v2-phase21a-metric-adapter-registry`.

  **Parallel group:** sequential Phase 21.

  **Scope:**

  - Run compatible metric adapters after successful attempts and after artifact
    preprocessing when required inputs exist.
  - Add API and CLI commands to run one adapter or all compatible adapters for
    an experiment.
  - Add frontend controls for configuring adapter evaluators and viewing adapter
    scores.
  - Include adapter scores in Markdown, CSV, and JSON exports.

  **Likely files:**

  - `backend/model_eval_api/deterministic_evaluators.py`
  - `backend/model_eval_api/headless.py`
  - `backend/model_eval_api/main.py`
  - `cli/model_eval_cli/main.py`
  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/App.test.tsx`
  - `tests/test_metric_adapters_phase21.py`
  - `tests/test_exports_phase11.py`

  **Acceptance criteria:**

  - Adapter execution skips incompatible attempts with explicit reasons.
  - Dry-run/local-only behavior does not call external services.
  - Duplicate adapter runs are prevented for the same adapter config snapshot and
    attempt unless forced by an explicit option.
  - Adapter score exports match existing score export conventions.

  **Validation:**

  - `pytest tests/test_metric_adapters_phase21.py tests/test_exports_phase11.py`
  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of adapter controls.

  **Out of scope:**

  - Promptfoo parser mappings.

- [x] `task_id: v2-phase22a-promptfoo-import`

  **Title:** Promptfoo Import And Manifest Preview

  **Branch:** `codex/v2-phase22a-promptfoo-import`

  **PR title:** `[codex] Add Promptfoo import preview`

  **Source:** Phase 22, tasks 1-5 and part of task 8.

  **Order:** `22.1`

  **Dependencies:** Phase 21 complete.

  **Parallel group:** Promptfoo/OpenTelemetry parallel candidate after Phase 21.

  **Scope:**

  - Add a Promptfoo YAML/JSON import parser for prompts, providers, tests/cases,
    assertions, variables, and options.
  - Map Promptfoo prompts to system prompts or cases with explicit warnings when
    the shape is ambiguous.
  - Map providers to model configs while preserving raw provider params.
  - Map assertions to deterministic evaluator or metric adapter configs when the
    mapping is supported.
  - Add `evalbench import promptfoo <file>` to emit a Model Eval manifest preview
    and optional persisted library records.

  **Likely files:**

  - `backend/model_eval_api/manifest.py`
  - `backend/model_eval_api/promptfoo.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `cli/model_eval_cli/main.py`
  - `tests/test_promptfoo_interop_phase22.py`
  - `docs/headless-workflow.md`

  **Acceptance criteria:**

  - Unsupported fields are returned as actionable warnings, not silently dropped.
  - Raw provider params are preserved in model config definitions.
  - Import preview does not execute providers or require provider keys.
  - Optional persistence uses existing library versioning rules.

  **Validation:**

  - `pytest tests/test_promptfoo_interop_phase22.py tests/test_manifest_contract.py`
  - `python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml`
  - `ruff check backend/model_eval_api cli/model_eval_cli tests/test_promptfoo_interop_phase22.py`

  **Out of scope:**

  - Promptfoo export.
  - UI import controls.

- [x] `task_id: v2-phase22b-promptfoo-export-ui`

  **Title:** Promptfoo Export, API, And UI

  **Branch:** `codex/v2-phase22b-promptfoo-export-ui`

  **PR title:** `[codex] Add Promptfoo export and UI`

  **Source:** Phase 22, tasks 6-8.

  **Order:** `22.2`

  **Dependencies:** `v2-phase22a-promptfoo-import`.

  **Parallel group:** Promptfoo/OpenTelemetry parallel candidate after Phase 21.

  **Scope:**

  - Add `evalbench export <experiment> --format promptfoo`.
  - Add API and frontend import/export actions with warning display.
  - Add export mapping for compatible model configs, prompts, cases, assertions,
    variables, and options.
  - Document unsupported and lossy mappings.

  **Likely files:**

  - `backend/model_eval_api/headless.py`
  - `backend/model_eval_api/main.py`
  - `backend/model_eval_api/promptfoo.py`
  - `cli/model_eval_cli/main.py`
  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/App.test.tsx`
  - `tests/test_promptfoo_interop_phase22.py`
  - `tests/test_exports_phase11.py`
  - `docs/headless-workflow.md`

  **Acceptance criteria:**

  - Round-trip-compatible configs export without warnings.
  - Unsupported fields and lossy mappings are surfaced in CLI, API, and UI.
  - Promptfoo export is stable in tests and excludes private/raw model outputs
    unless already part of the approved experiment export contract.

  **Validation:**

  - `pytest tests/test_promptfoo_interop_phase22.py tests/test_exports_phase11.py`
  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of import/export warning display.

  **Out of scope:**

  - OpenTelemetry export.

- [x] `task_id: v2-phase23a-otel-span-builder-redaction`

  **Title:** OpenTelemetry Span Builder And Redaction Policy

  **Branch:** `codex/v2-phase23a-otel-span-builder-redaction`

  **PR title:** `[codex] Add OpenTelemetry span builder`

  **Source:** Phase 23, tasks 1-2 and part of task 6.

  **Order:** `23.1`

  **Dependencies:** Phase 20 complete. Can run in parallel with Phase 22 after
  Phase 21 if the runner uses independent worktrees.

  **Parallel group:** Promptfoo/OpenTelemetry parallel candidate.

  **Scope:**

  - Add a metadata-only span builder for experiment, run, run attempt,
    deterministic evaluator, judge evaluator, human review, artifact
    preprocessing, and export events.
  - Add redaction policy tests that fail if raw prompts, artifacts, manifests,
    screenshots, warmer messages, request payloads, response payloads,
    credentials, or model outputs appear in trace attributes.
  - Keep trace export opt-in and local-file oriented.

  **Likely files:**

  - `backend/model_eval_api/otel_export.py`
  - `backend/model_eval_api/headless.py`
  - `backend/model_eval_api/persistence/repositories.py`
  - `tests/test_exports_phase11.py`
  - `tests/test_privacy_repro_phase13.py`
  - `docs/privacy-repro-safety.md`

  **Acceptance criteria:**

  - Span IDs and parent-child relationships are stable for a given export.
  - Trace attributes include IDs, timings, status, token/cost metadata, provider
    metadata, and audit links only.
  - Redaction tests include deliberately sensitive fixture values and fail if
    those values leak, including screenshot filenames and screenshot-derived
    metadata that could reveal private artifact content.

  **Validation:**

  - `pytest tests/test_exports_phase11.py tests/test_privacy_repro_phase13.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api tests/test_exports_phase11.py tests/test_privacy_repro_phase13.py`

  **Out of scope:**

  - CLI/API/UI export actions.

- [x] `task_id: v2-phase23b-otel-export-surfaces`

  **Title:** OpenTelemetry Export CLI, API, UI, And Docs

  **Branch:** `codex/v2-phase23b-otel-export-surfaces`

  **PR title:** `[codex] Add OpenTelemetry export surfaces`

  **Source:** Phase 23, tasks 3-6.

  **Order:** `23.2`

  **Dependencies:** `v2-phase23a-otel-span-builder-redaction`.

  **Parallel group:** Promptfoo/OpenTelemetry parallel candidate.

  **Scope:**

  - Add `evalbench export <experiment> --format otel-json`.
  - Add API endpoint and frontend export action for OpenTelemetry JSON.
  - Record an audit log event for trace export generation.
  - Document what metadata is included, what is excluded, and how to inspect the
    local trace file.

  **Likely files:**

  - `backend/model_eval_api/headless.py`
  - `backend/model_eval_api/main.py`
  - `cli/model_eval_cli/main.py`
  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/App.test.tsx`
  - `tests/test_exports_phase11.py`
  - `tests/test_privacy_repro_phase13.py`
  - `docs/headless-workflow.md`
  - `docs/privacy-repro-safety.md`

  **Acceptance criteria:**

  - CLI, API, and UI all use the same span builder.
  - Audit logs record export generation without embedding raw trace payloads.
  - UI labels the export as metadata-only and local-file based.
  - Export ordering is stable and covered by tests.

  **Validation:**

  - `pytest tests/test_exports_phase11.py tests/test_privacy_repro_phase13.py`
  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of export action.

  **Out of scope:**

  - Promptfoo export behavior.

- [x] `task_id: v2-phase24a-frontier-analytics`

  **Title:** Cost-Quality Frontier Analytics And Export Rows

  **Branch:** `codex/v2-phase24a-frontier-analytics`

  **PR title:** `[codex] Add V2 cost-quality frontier analytics`

  **Source:** Phase 24, tasks 1-5 and 7-8 backend/export portions.

  **Order:** `24.1`

  **Dependencies:** Phases 19, 21, and 22 complete.

  **Parallel group:** sequential Phase 24.

  **Scope:**

  - Add frontier calculation that marks dominated and non-dominated
    configurations by quality metric, cost, and latency.
  - Include Phase 18 uncertainty intervals in frontier rows.
  - Add filters by case, suite, split, model, prompt, warmer, evaluator source,
    and reviewer.
  - Combine warmer lift with Phase 19 distortion/divergence values in backend
    analytics.
  - Add judge calibration overlays when Phase 15 calibration exists.
  - Add Markdown, CSV, JSON, and Promptfoo-compatible export fields for V2
    frontier rows.

  **Likely files:**

  - `backend/model_eval_api/results_analytics.py`
  - `backend/model_eval_api/headless.py`
  - `backend/model_eval_api/main.py`
  - `tests/test_results_analytics_phase10.py`
  - `tests/test_exports_phase11.py`
  - `tests/test_promptfoo_interop_phase22.py`
  - `docs/results-analytics.md`

  **Acceptance criteria:**

  - Dominated-row calculation handles missing quality scores, missing cost, and
    missing latency deterministically.
  - Frontier rows include uncertainty labels and interval bounds where available.
  - Backend filters are deterministic and shared by API/export paths.
  - Export shape is stable across Markdown, CSV, JSON, and Promptfoo-compatible
    outputs.

  **Validation:**

  - `pytest tests/test_results_analytics_phase10.py tests/test_exports_phase11.py tests/test_promptfoo_interop_phase22.py`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/results_analytics.py backend/model_eval_api/headless.py backend/model_eval_api/main.py tests/test_results_analytics_phase10.py tests/test_exports_phase11.py`

  **Out of scope:**

  - Results page chart/table controls.

- [x] `task_id: v2-phase24b-frontier-results-ux`

  **Title:** V2 Results Frontier UX

  **Branch:** `codex/v2-phase24b-frontier-results-ux`

  **PR title:** `[codex] Add V2 frontier results UX`

  **Source:** Phase 24, tasks 3, 6, and frontend portions of task 8.

  **Order:** `24.2`

  **Dependencies:** `v2-phase24a-frontier-analytics`.

  **Parallel group:** sequential Phase 24.

  **Scope:**

  - Add frontend API types for V2 frontier rows, filters, uncertainty fields,
    calibration overlays, and warmer lift versus distortion.
  - Add Results chart/table controls for frontier, uncertainty, calibration, and
    context-sensitivity views.
  - Keep the UI dense and workbench-oriented. Do not add a landing page or
    marketing-style hero.
  - Add responsive UI tests and browser smoke coverage.

  **Likely files:**

  - `frontend/src/api.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/styles.css`
  - `frontend/src/App.test.tsx`
  - `docs/results-analytics.md`

  **Acceptance criteria:**

  - Filters do not overlap or resize unstable UI controls.
  - Frontier visualization identifies dominated and non-dominated rows.
  - Uncertainty and calibration indicators are visible without implying false
    precision.
  - Responsive layout works on desktop and mobile browser smoke viewports.

  **Validation:**

  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of Results on desktop and mobile widths.

  **Out of scope:**

  - New backend metrics.

- [x] `task_id: v2-phase25a-v2-demo-builder`

  **Title:** Local-Only V2 Demo Builder

  **Branch:** `codex/v2-phase25a-v2-demo-builder`

  **PR title:** `[codex] Add local-only V2 demo builder`

  **Source:** Phase 25, tasks 1-4, 7-8.

  **Order:** `25.1`

  **Dependencies:** Phase 24 complete.

  **Parallel group:** sequential Phase 25.

  **Scope:**

  - Add a V2 demo manifest or suite definition that extends the copper memo
    scenario with benchmark suite, splits, judge config, taxonomy, reviewer
    assignments, replicated runs, preprocessing, metric adapters, and exports.
  - Add synthetic data that produces advanced analytics outputs, including
    divergence rows from Phase 19 and cost-quality frontier rows from Phase 24.
  - Add at least one safe committed text fixture and one safe committed image or
    PDF fixture for local tests.
  - Add synthetic judge outputs and synthetic multi-reviewer human decisions for
    repeatable local-only calibration.
  - Add `evalbench demo v2 --export-dir <path>` that builds the complete V2 demo.
  - Generate export fixtures into a temporary directory only.

  **Likely files:**

  - `backend/model_eval_api/v2_demo.py`
  - `backend/model_eval_api/copper_demo.py`
  - `cli/model_eval_cli/main.py`
  - `examples/v2_copper_benchmark_suite.yaml`
  - `tests/test_v2_demo_phase25.py`
  - `tests/fixtures/` or another existing safe fixture path

  **Acceptance criteria:**

  - Demo reruns are idempotent.
  - No live provider calls are made.
  - Expected suite, run, review, judge, metric, preprocessing, advanced
    analytics, frontier, and export counts are asserted in tests.
  - Generated exports are not committed.

  **Validation:**

  - `pytest tests/test_v2_demo_phase25.py`
  - `python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo`
  - `python -m compileall backend cli`
  - `ruff check backend/model_eval_api/v2_demo.py backend/model_eval_api/copper_demo.py cli/model_eval_cli/main.py tests/test_v2_demo_phase25.py`

  **Out of scope:**

  - Documentation walkthrough and frontend smoke coverage.

- [x] `task_id: v2-phase25b-v2-demo-docs-and-ui-smoke`

  **Title:** V2 Demo Documentation And Frontend Smoke Coverage

  **Branch:** `codex/v2-phase25b-v2-demo-docs-and-ui-smoke`

  **PR title:** `[codex] Document and smoke test the V2 demo`

  **Source:** Phase 25, tasks 5-6 and documentation portions of task 8.

  **Order:** `25.2`

  **Dependencies:** `v2-phase25a-v2-demo-builder`.

  **Parallel group:** sequential Phase 25.

  **Scope:**

  - Add `docs/v2-demo.md` showing the V2 workflow from suite setup through
    preprocessing, execution, review, calibration, analytics, and export.
  - Add frontend smoke coverage for opening the V2 demo in Library, Run Monitor,
    Comparison Workspace, and Results.
  - Keep smoke fixtures local-only and deterministic.

  **Likely files:**

  - `docs/v2-demo.md`
  - `docs/headless-workflow.md`
  - `frontend/src/App.tsx`
  - `frontend/src/App.test.tsx`
  - `tests/test_v2_demo_phase25.py`

  **Acceptance criteria:**

  - A new developer can run the V2 demo without provider keys.
  - Docs name the local-only commands and expected outputs.
  - Frontend smoke coverage proves V2 demo data renders across major workbench
    views.
  - Results smoke coverage verifies advanced analytics surfaces, including
    divergence rows and cost-quality frontier rows.

  **Validation:**

  - `pytest tests/test_v2_demo_phase25.py`
  - `npm test` from `frontend/`
  - `npm run build` from `frontend/`
  - Browser smoke test of Library, Run Monitor, Comparison Workspace, and Results
    with demo data.

  **Out of scope:**

  - New demo generation behavior beyond what Phase 25A provides.

- [x] `task_id: v2-phase25c-v2-completion-audit`

  **Title:** V2 Completion Audit And Defer Notes

  **Branch:** `codex/v2-phase25c-v2-completion-audit`

  **PR title:** `[codex] Complete V2 readiness audit`

  **Source:** V2 Completion Criteria and V3 Backlog Kept Out Of Scope.

  **Order:** `25.3`

  **Dependencies:** `v2-phase25b-v2-demo-docs-and-ui-smoke`.

  **Parallel group:** sequential final V2 task.

  **Scope:**

  - Update `docs/v2-implementation-task-list.md` to mark completed V2 tasks.
  - Update `docs/implementation-task-list.md` so every V2 backlog item has a
    completed phase or explicit defer note.
  - Add or update a concise V2 completion summary that links the implemented
    phase docs and states what remains out of scope.
  - Confirm V3 backlog items remain unchecked unless the user explicitly promoted
    them.

  **Likely files:**

  - `docs/v2-implementation-task-list.md`
  - `docs/implementation-task-list.md`
  - `docs/v2-demo.md`
  - `FEATURE_INVENTORY.md`

  **Acceptance criteria:**

  - Every V2 completion criterion is either checked with evidence or has a clear
    defer note.
  - Promptfoo, OpenTelemetry, artifact preprocessing, metric adapters,
    divergence metrics, and cost-quality frontier are all traceable to merged
    tasks.
  - V3 backlog remains explicitly out of scope.

  **Validation:**

  - `git diff --check`
  - `python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml`
  - Any docs-specific checks available in the repo.

  **Out of scope:**

  - New product behavior.
  - Starting V3.
