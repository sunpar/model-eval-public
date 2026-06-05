# Model Eval V2 Implementation Task List

This checklist expands the V2 backlog into an implementation sequence that follows the same phase-based style as the MVP/V1 task list. The V2 target is to turn the V1 context-sensitivity demo into a repeatable evaluation workbench with calibrated judge workflows, richer human review, benchmark suites, artifact preprocessing, uncertainty-aware analytics, and interoperability.

V2 should preserve the V1 wedge: conversation warmers remain first-class structured/versioned entities. V2 should not drift into V3 production trace ingestion, team administration, prompt deployment, evaluator CI gates, local model hosting, or scheduled provider-drift monitoring unless those items are explicitly promoted.

## V2 Delivery Rules

- Start every phase from latest `origin/main`.
- Keep one branch and one PR per phase.
- Keep provider execution dry-run/local-only in tests.
- Keep `Run` separate from `RunAttempt`.
- Keep human labels as the source of truth for subjective calibration.
- Preserve raw provider params alongside normalized provider fields.
- Store immutable snapshots for judge prompts, metric adapter configs, benchmark suite membership, preprocessing outputs, and export payloads where reproducibility requires it.
- Do not commit provider API keys, private prompts, private artifacts, model outputs, or raw traces.
- Add or update tests with every phase.
- Run the ongoing verification checklist before each PR.

## Phase 14: LLM Judge Builder Foundation

Acceptance criteria:

- Users can define reusable LLM judge configurations without running live provider calls by default.
- Judge configs are versioned library objects with prompt, rubric, output schema, model config, calibration status, and raw provider params.
- Experiments can snapshot judge configs as evaluators without mutating historical runs when a judge changes.

Likely touched areas:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/deterministic_evaluators.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/experimentBuilder.test.ts`
- `tests/test_llm_judges_phase14.py`
- Alembic migrations

Tasks:

- [x] Add `LLMJudgeConfig` persistence, including slug, name, judge prompt, rubric dimensions, output schema, judge model config reference, raw provider params, version, archived status, and snapshot JSON.
- [x] Add repository helpers for creating, listing, versioning, archiving, and snapshotting judge configs.
- [x] Add manifest support for judge evaluator references and inline judge definitions.
- [x] Add API endpoints for judge config library CRUD through the existing project-scoped library pattern.
- [x] Add frontend library screen support for judge configs with rubric, prompt, model, output schema, and raw params editing.
- [x] Add validation that a judge config cannot reference a missing model config or malformed output schema.
- [x] Add tests for judge config creation, duplicate version constraints, snapshot immutability, manifest references, and frontend editor validation.
- [x] Update docs to explain that V2 LLM judges are stored as evaluators but calibrated against human labels before trusted use.

## Phase 15: LLM Judge Execution, Calibration, And Bias Controls

Acceptance criteria:

- Judge runs can evaluate stored attempts and persist typed scores with judge prompt/version metadata.
- Pairwise judging supports answer-order randomization and position-swapped judging.
- Calibration reports compare judge decisions against human decisions and flag disagreement, verbosity bias, and low-confidence criteria.

Likely touched areas:

- `backend/model_eval_api/providers/*`
- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/queue.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/headless.py`
- `cli/model_eval_cli/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `tests/test_llm_judge_execution_phase15.py`
- Alembic migrations

Tasks:

- [x] Add judge execution records that link a judge config snapshot to source run attempts and produced score records.
- [x] Add a judge request builder that creates blind pairwise and rubric-evaluation prompts from stored attempt outputs without leaking hidden metadata into the judge prompt.
- [x] Add dry-run judge execution that records request shape and synthetic local outputs without provider keys.
- [x] Add live judge execution behind existing local-only, provider allow/deny, cost cap, and context-budget gates.
- [x] Add position-swapped judging for pairwise comparisons and store both original and swapped decisions.
- [x] Add verbosity-bias controls by storing answer token counts and reporting whether longer answers win disproportionately.
- [x] Add calibration aggregation comparing judge winner/pass/fail/rubric decisions to human review scores.
- [x] Add CLI command `evalbench judge <experiment> --judge <id> --dry-run --local-only`.
- [x] Add UI controls for running a judge on an experiment and viewing calibration status.
- [x] Add tests for dry-run judge execution, local-only enforcement, position swap persistence, human/judge agreement rates, verbosity-bias reporting, and duplicate-run prevention.

## Phase 16: Review Queues And Failure Taxonomy Builder

Acceptance criteria:

- Review sets support multiple reviewers, assignments, progress tracking, and reviewer-specific decisions.
- Failure tags can be managed through a versioned taxonomy instead of hard-coded demo tags.
- Results can show reviewer coverage, reviewer disagreement, and failure taxonomy rollups.

Likely touched areas:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `backend/model_eval_api/results_analytics.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `tests/test_review_phase8.py`
- `tests/test_results_analytics_phase10.py`
- Alembic migrations

Tasks:

- [x] Add `Reviewer`, `ReviewAssignment`, and `FailureTaxonomy` persistence with project scope and immutable taxonomy snapshots.
- [x] Add repository helpers to create review queues for one or more reviewers and assign review items deterministically.
- [x] Add API endpoints to list reviewers, create assignments, fetch a reviewer queue, and submit decisions under a reviewer identity.
- [x] Replace default hard-coded failure tags with a project taxonomy, while preserving copper memo defaults as seed taxonomy data.
- [x] Add UI for reviewer queue selection, assignment progress, and taxonomy-backed failure tag selection.
- [x] Add disagreement analytics across reviewers for pairwise winner, pass/fail, and failure tags.
- [x] Add export fields for reviewer IDs, assignment status, taxonomy version, and disagreement metrics.
- [x] Add tests for reviewer assignment, blind metadata hiding per reviewer, taxonomy versioning, repeated/revised decisions, disagreement aggregation, and export stability.

## Phase 17: Benchmark Suites And Dataset Splits

Acceptance criteria:

- Users can define reusable benchmark suites that group cases, prompts, warmers, model configs, evaluators, and controls.
- Cases can belong to dev, validation, holdout, and archived splits.
- A suite rerun creates a reproducible experiment from locked suite membership and current selected versions.

Likely touched areas:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/manifest.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/headless.py`
- `cli/model_eval_cli/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/experimentBuilder.ts`
- `tests/test_benchmark_suites_phase17.py`
- Alembic migrations

Tasks:

- [x] Add `BenchmarkSuite`, `BenchmarkSuiteItem`, and dataset split fields for cases.
- [x] Add suite snapshots that lock case membership, split membership, selected prompts, warmers, models, evaluators, controls, and suite version.
- [x] Add API endpoints for creating, editing, archiving, and previewing benchmark suites.
- [x] Add manifest support for benchmark suite references and split filters.
- [x] Add CLI command `evalbench suite run <suite> --split <split> --dry-run --local-only`.
- [x] Add UI for suite management, split assignment, and suite-run preview.
- [x] Add run generation support for suite reruns while preserving full-factorial behavior.
- [x] Add tests for split validation, archived-case exclusion, deterministic suite snapshots, rerun experiment creation, CLI suite runs, and UI preview.

## Phase 18: Replicated Runs, Nondeterminism, And Confidence Intervals

Acceptance criteria:

- Experiments and benchmark suites can intentionally run replicated attempts for nondeterminism analysis.
- Results show variance, simple confidence intervals, and uncertainty labels without false precision.
- Exports include replicate groups and uncertainty metadata.

Likely touched areas:

- `backend/model_eval_api/manifest.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/headless.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `docs/results-analytics.md`
- `tests/test_manifest_contract.py`
- `tests/test_executor_phase5.py`
- `tests/test_results_analytics_phase10.py`
- `tests/test_exports_phase11.py`

Tasks:

- [x] Extend manifest controls to distinguish replicate attempts used for reliability from retry attempts used for failure recovery.
- [x] Store replicate group metadata on runs and attempts.
- [x] Add nondeterminism summaries by case, model, prompt, warmer, and suite split.
- [x] Add confidence interval calculations for pass rate, win rate, failure rate, cost, latency, and token totals using transparent count-based methods.
- [x] Add UI labels that show sample size and uncertainty warnings when counts are low.
- [x] Add export fields for replicate group, sample count, variance, interval lower bound, interval upper bound, and uncertainty label.
- [x] Add tests for replicate grouping, retry exclusion from nondeterminism stats, zero-sample and one-sample interval behavior, and export stability.

## Phase 19: Advanced Context-Sensitivity And Divergence Metrics

Acceptance criteria:

- Results can distinguish quality lift from warmer-induced distortion using richer divergence metrics.
- Semantic, claim, conclusion, confidence, section-structure, token-length, and failure-mode divergence are recorded as typed scores or analytics rows.
- Metrics remain explainable and degrade gracefully when artifacts, citations, or structured outputs are unavailable.

Likely touched areas:

- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/deterministic_evaluators.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/headless.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `docs/results-analytics.md`
- `tests/test_results_analytics_phase10.py`
- `tests/test_deterministic_evaluators_phase9.py`

Tasks:

- [x] Add divergence score types for semantic, claim, conclusion, confidence, section structure, token length, and failure-mode spread.
- [x] Add deterministic section-structure divergence based on extracted headings and required-section order.
- [x] Add token-length and confidence-language divergence using local text analysis.
- [x] Add claim/conclusion divergence interfaces with local deterministic fallbacks and optional judge-backed scoring through Phase 15 judge execution.
- [x] Add conversation carryover audit rows showing warmer details reused, ignored, or overfit when evidence is available from structured judge outputs.
- [x] Add Results UI tables for divergence by fixed case, model, and system prompt across warmers.
- [x] Add warnings when divergence values are based on shallow deterministic heuristics rather than calibrated judge or human labels.
- [x] Add tests for comparable grouping, missing no-warmer baselines, mixed metric sources, deterministic structure divergence, and UI rendering.

Evidence: completed by `v2-phase19a-divergence-foundation`, `v2-phase19b-claim-conclusion-carryover`, and `v2-phase19c-divergence-results-ui`. Trace through `backend/model_eval_api/results_analytics.py`, `backend/model_eval_api/deterministic_evaluators.py`, `backend/model_eval_api/headless.py`, `docs/results-analytics.md`, `tests/test_results_analytics_phase10.py`, `tests/test_deterministic_evaluators_phase9.py`, and `frontend/src/App.test.tsx`.

## Phase 20: Artifact Preprocessing Pipeline

Acceptance criteria:

- The system can preprocess PDFs and images into explicit, versioned derived artifact records.
- Runs record whether the model saw direct files, extracted PDF text, page screenshots, selected figures, tables, OCR text, retrieval chunks, or a mixed derived bundle.
- Private source files and generated previews remain local by default and are never copied into the repo.

Likely touched areas:

- `backend/model_eval_api/artifact_types.py`
- `backend/model_eval_api/artifacts.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/executor.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `tests/test_artifacts_phase3.py`
- Alembic migrations

Tasks:

- [x] Add preprocessing records for source artifact ID, parser name, parser version, derived artifact IDs, extraction timestamp, checksum, and local storage URI.
- [x] Add PDF text extraction with page-level text metadata.
- [x] Add PDF page screenshot generation with per-page image metadata.
- [x] Add image normalization metadata and OCR text capture.
- [x] Add selected figure and table extraction records with source page/region metadata.
- [x] Add retrieval chunk records with chunk text, source offsets, and source checksum.
- [x] Add paper card summaries as derived artifacts with citation and section metadata.
- [x] Add API endpoints and UI controls for starting preprocessing, inspecting derived artifacts, and selecting an input mode for runs.
- [x] Add tests for checksum stability, missing local files, derived artifact snapshotting, mixed-mode rejection where unsupported, and model input snapshots for each input mode.

Evidence: completed by `v2-phase20a-preprocessing-records`, `v2-phase20b-pdf-text-extraction`, `v2-phase20c-visual-ocr-extractors`, `v2-phase20d-figure-table-records`, `v2-phase20e-retrieval-paper-cards`, `v2-phase20f-run-input-snapshots`, and `v2-phase20g-preprocessing-api-ui`. Trace through `backend/model_eval_api/artifact_types.py`, `backend/model_eval_api/artifacts.py`, `backend/model_eval_api/persistence/models.py`, `backend/model_eval_api/main.py`, `backend/model_eval_api/executor.py`, `docs/data-model.md`, `tests/test_artifacts_phase3.py`, and `frontend/src/App.test.tsx`.

## Phase 21: Metric Adapter Layer For RAG And DeepEval-Style Checks

Acceptance criteria:

- Metric adapters can be registered, configured, versioned, run, and stored as typed evaluator scores.
- V2 includes local RAG metric adapters and DeepEval-style adapter contracts without requiring external services in tests.
- Adapter outputs can feed Results and exports alongside human, deterministic, and judge scores.

Likely touched areas:

- `backend/model_eval_api/deterministic_evaluators.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/main.py`
- `cli/model_eval_cli/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `tests/test_metric_adapters_phase21.py`
- Alembic migrations

Tasks:

- [x] Add `MetricAdapterConfig` persistence with adapter kind, version, required input fields, output schema, and local-only capability metadata.
- [x] Add adapter registry with local implementations for retrieval precision, citation coverage, groundedness checklist, and answer relevance.
- [x] Add DeepEval-style adapter wrapper that can map external metric results into Model Eval score records without importing external SDKs in tests.
- [x] Add evaluator execution path for metric adapters after successful attempts and after artifact preprocessing when required.
- [x] Add API and CLI commands to run one adapter or all compatible adapters for an experiment.
- [x] Add frontend controls for configuring adapter evaluators and viewing adapter scores.
- [x] Add tests for adapter registration, required-input validation, local metric scoring, external-result mapping, dry-run behavior, and export inclusion.

Evidence: completed by `v2-phase21a-metric-adapter-registry` and `v2-phase21b-metric-adapter-execution`. Trace through `backend/model_eval_api/metric_adapters.py`, `backend/model_eval_api/metric_adapter_execution.py`, `backend/model_eval_api/main.py`, `backend/model_eval_api/headless.py`, `cli/model_eval_cli/main.py`, `docs/headless-workflow.md`, `tests/test_metric_adapters_phase21.py`, and `frontend/src/App.test.tsx`.

## Phase 22: Promptfoo Import And Export

Acceptance criteria:

- Users can import a Promptfoo-style eval definition into Model Eval library objects and experiment manifests.
- Users can export compatible Model Eval experiments back into a Promptfoo-style config where the mapping is lossless enough to be explicit.
- Unsupported fields are reported with actionable warnings, not silently dropped.

Likely touched areas:

- `backend/model_eval_api/manifest.py`
- `backend/model_eval_api/headless.py`
- `cli/model_eval_cli/main.py`
- `backend/model_eval_api/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `tests/test_promptfoo_interop_phase22.py`
- `docs/headless-workflow.md`

Tasks:

- [x] Add Promptfoo import parser for prompts, providers, tests/cases, assertions, variables, and options.
- [x] Map Promptfoo prompts to system prompts or cases with explicit warnings when the source shape is ambiguous.
- [x] Map Promptfoo providers to Model Eval model configs while preserving raw provider params.
- [x] Map Promptfoo assertions to deterministic evaluator or metric adapter configs where possible.
- [x] Add CLI command `evalbench import promptfoo <file>` that emits a Model Eval manifest preview and optional persisted library records.
- [x] Add CLI command `evalbench export <experiment> --format promptfoo`.
- [x] Add API and UI import/export actions with warning display.
- [x] Add tests for round-trip-compatible configs, unsupported field warnings, provider param preservation, evaluator mapping, and export stability.

Evidence: completed by `v2-phase22a-promptfoo-import` and `v2-phase22b-promptfoo-export-ui`. Trace through `backend/model_eval_api/promptfoo.py`, `backend/model_eval_api/headless.py`, `backend/model_eval_api/main.py`, `cli/model_eval_cli/main.py`, `docs/headless-workflow.md`, `tests/test_promptfoo_interop_phase22.py`, and `frontend/src/App.test.tsx`.

## Phase 23: OpenTelemetry Trace Export

Acceptance criteria:

- Experiment, run, attempt, evaluator, review, and export events can be exported as OpenTelemetry-compatible traces.
- Trace payloads preserve IDs, timings, status, token/cost metadata, provider metadata, and audit event links without leaking raw prompts, artifacts, manifests, screenshots, warmer text, model outputs, or provider credentials.
- Export is opt-in and local-file based for V2.

Likely touched areas:

- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/main.py`
- `cli/model_eval_cli/main.py`
- `docs/privacy-repro-safety.md`
- `docs/headless-workflow.md`
- `tests/test_exports_phase11.py`
- `tests/test_privacy_repro_phase13.py`

Tasks:

- [x] Add trace span builder for experiment, run, run attempt, deterministic evaluator, judge evaluator, human review, artifact preprocessing, and export events.
- [x] Add redaction policy tests that fail if raw prompts, artifacts, manifests, warmer messages, request payloads, response payloads, or credentials appear in trace attributes.
- [x] Add CLI command `evalbench export <experiment> --format otel-json`.
- [x] Add API endpoint and UI export action for OpenTelemetry JSON.
- [x] Add audit log event for trace export generation.
- [x] Add docs explaining what metadata is included, what is excluded, and how to inspect the local trace file.
- [x] Add tests for span hierarchy, stable trace IDs, metadata inclusion, redaction, and export ordering.

Evidence: completed by `v2-phase23a-otel-span-builder-redaction` and `v2-phase23b-otel-export-surfaces`. Trace through `backend/model_eval_api/otel_export.py`, `backend/model_eval_api/headless.py`, `backend/model_eval_api/main.py`, `cli/model_eval_cli/main.py`, `docs/privacy-repro-safety.md`, `docs/headless-workflow.md`, `tests/test_exports_phase11.py`, `tests/test_privacy_repro_phase13.py`, and `frontend/src/App.test.tsx`.

## Phase 24: Better Cost-Quality Frontier And V2 Results UX

Acceptance criteria:

- Results can compare cost, latency, quality, uncertainty, judge calibration, and warmer sensitivity without hiding sample-size limitations.
- The cost-quality frontier makes dominated configurations, uncertainty intervals, and warmer lift/distortion visible.
- UI and exports use the same aggregation source of truth.

Likely touched areas:

- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `docs/results-analytics.md`
- `tests/test_results_analytics_phase10.py`
- `frontend/src/App.test.tsx`

Tasks:

- [x] Add frontier calculation that marks dominated and non-dominated configurations by quality metric, cost, and latency.
- [x] Add interval-aware frontier rows using Phase 18 uncertainty fields.
- [x] Add filters by case, suite, split, model, prompt, warmer, evaluator source, and reviewer.
- [x] Add warmer lift versus distortion view combining Phase 19 divergence and existing warmer lift.
- [x] Add judge calibration overlays when Phase 15 calibration exists.
- [x] Add UI chart/table controls for frontier, uncertainty, calibration, and context-sensitivity views.
- [x] Add Markdown, CSV, JSON, and Promptfoo-compatible export fields for V2 frontier rows.
- [x] Add tests for dominated-row calculation, missing quality scores, uncertainty display, filter behavior, export shape, and responsive UI rendering.

Evidence: completed by `v2-phase24a-frontier-analytics` and `v2-phase24b-frontier-results-ux`. Trace through `backend/model_eval_api/results_analytics.py`, `backend/model_eval_api/headless.py`, `backend/model_eval_api/main.py`, `docs/results-analytics.md`, `tests/test_results_analytics_phase10.py`, `tests/test_exports_phase11.py`, and `frontend/src/App.test.tsx`.

## Phase 25: V2 Demo Readiness And Documentation

Acceptance criteria:

- The repo has a local-only V2 demo that proves V2 exists beyond the V1 copper memo path.
- The demo exercises judge calibration, multi-reviewer queues, benchmark suite reruns, replicated runs, artifact preprocessing, advanced analytics, and exports.
- A new developer can run the V2 demo without live provider keys.

Likely touched areas:

- `backend/model_eval_api/copper_demo.py`
- `backend/model_eval_api/v2_demo.py`
- `cli/model_eval_cli/main.py`
- `examples/v2_copper_benchmark_suite.yaml`
- `docs/v2-demo.md`
- `frontend/src/App.tsx`
- `frontend/src/App.test.tsx`
- `tests/test_v2_demo_phase25.py`

Tasks:

- [x] Add a V2 demo manifest or suite definition that extends the copper memo scenario with a benchmark suite, splits, judge config, taxonomy, reviewer assignments, replicated runs, and exports.
- [x] Add at least one local text artifact and one local image/PDF fixture small enough for tests and safe to commit.
- [x] Add synthetic judge outputs and synthetic multi-reviewer human decisions for local-only repeatability.
- [x] Add CLI command `evalbench demo v2 --export-dir <path>` that builds the complete V2 demo.
- [x] Add docs showing the V2 workflow from suite setup through preprocessing, execution, review, calibration, analytics, and export.
- [x] Add frontend smoke coverage for opening the V2 demo in Library, Run Monitor, Comparison Workspace, and Results.
- [x] Add export fixtures generated during tests into a temporary directory only, not committed demo output.
- [x] Add tests for idempotent V2 demo reruns, no live provider calls, expected suite/run/review counts, and stable export shapes.

Evidence: completed by `v2-phase25a-v2-demo-builder` and `v2-phase25b-v2-demo-docs-and-ui-smoke`. Trace through `backend/model_eval_api/v2_demo.py`, `cli/model_eval_cli/main.py`, `examples/v2_copper_benchmark_suite.yaml`, `docs/v2-demo.md`, `tests/test_v2_demo_phase25.py`, `frontend/src/experimentBuilder.ts`, and `frontend/src/App.test.tsx`.

## V2 Completion Criteria

- [x] Every V2 backlog item from `docs/implementation-task-list.md` has a completed phase or explicit defer note.
- [x] The V2 demo can be created from scratch with local-only commands.
- [x] LLM judge decisions can be calibrated against human review labels.
- [x] Multiple reviewers and failure taxonomies are first-class, queryable records.
- [x] Benchmark suites can be rerun by split with reproducible snapshots.
- [x] Replicate groups and uncertainty intervals are visible in API, UI, and exports.
- [x] Artifact preprocessing records exactly what was extracted and what each run saw.
- [x] RAG and DeepEval-style metric adapters can run without external services in tests.
- [x] Promptfoo import/export reports unsupported fields explicitly.
- [x] OpenTelemetry export is metadata-only and redaction-tested.
- [x] Cost-quality frontier views show dominance, uncertainty, and context-sensitivity together.

Completion evidence: V2 phases 14-25 are implemented and traceable through the phase evidence above, `docs/implementation-task-list.md`, `FEATURE_INVENTORY.md`, and the local-only workflow in `docs/v2-demo.md`.

## V3 Backlog Kept Out Of Scope

The V3 items below are intentionally unchecked. They remain out of scope for V2 completion unless the user explicitly promotes them.

- [ ] Production trace ingestion.
- [ ] Active sampling for review.
- [ ] Synthetic case generation from failure taxonomies.
- [ ] Team review workflow beyond local reviewer identities and assignments.
- [ ] Evaluator CI gates.
- [ ] Prompt deployment and rollback.
- [ ] Model release comparison dashboard.
- [ ] Custom provider SDK.
- [ ] Local model support through Ollama and vLLM.
- [ ] Scheduled benchmark reruns for provider drift monitoring.

## Ongoing Verification Checklist

Latest release-readiness verification recorded on 2026-05-26 for
`codex/docs-release-readiness`:

- [x] `git diff --check`
- [x] `uv run python -m compileall backend cli`
- [x] `PYTHONPATH=backend:cli uv run python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml`
- [x] `uv run --extra dev ruff check .`
- [x] `PYTHONPATH=backend:cli uv run --extra dev pytest -q`
- [x] `(cd frontend && npm ci)`
- [x] `(cd frontend && npm run build)`
- [x] `(cd frontend && npm run test)`
- [x] `PYTHONPATH=backend MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-docs-alembic.sqlite3 uv run alembic upgrade head`
- [x] `PYTHONPATH=backend:cli MODEL_EVAL_DATABASE_URL=sqlite+pysqlite:////tmp/model-eval-v2-demo-docs-readiness.sqlite3 uv run --extra dev python -m model_eval_cli.main demo v2 --export-dir /tmp/model-eval-v2-demo-docs-readiness`
- [ ] Browser smoke test after material UI changes. Not run for this docs-only branch.
- [ ] Host-service smoke check when Postgres, Redis, API, worker, or setup docs change. Not run for this docs-only branch.
