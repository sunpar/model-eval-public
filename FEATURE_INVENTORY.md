# Model Eval Feature Inventory

This document is the repo-wide review/refactor map for the current Model Eval implementation.
It was reconciled against `docs/implementation-task-list.md`, `docs/v2-implementation-task-list.md`, and the implemented code tree through V2 Phase 25.

Use each feature below as an isolated review or cleanup branch. The phase checklist remains the delivery history; this inventory is organized by ownership boundary so a reviewer can inspect one complete feature without needing to understand the entire repo at once.

V2 implementation slices are tracked in F36-F48. V3 backlog notes in `docs/implementation-task-list.md` and `docs/mvp-roadmap.md` remain future direction only unless explicitly promoted later.

## F01 - Repository Foundation And Developer Workflow

Description: Local development, dependency, verification, and service bootstrap conventions for the backend, CLI, frontend, Postgres, and Redis.

Files involved:

- `README.md`
- `.env.example`
- `.gitignore`
- `Makefile`
- `pyproject.toml`
- `tests/test_repository_hygiene.py`
- `backend/model_eval_api/__init__.py`
- `backend/model_eval_api/persistence/__init__.py`
- `cli/model_eval_cli/__init__.py`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`

Architecture chosen: Python packaging exposes `evalbench` from the backend/CLI source roots, Makefile targets wrap common checks and local servers, and SQLite is the local default with host-installed Postgres and Redis available for service checks.

## F02 - Product Scope, Architecture, And Handoff Docs

Description: Product and technical framing for the context-sensitivity wedge, warmer-first architecture, data model, MVP roadmap, and external research handoff.

Files involved:

- `docs/product-brief.md`
- `docs/architecture.md`
- `docs/data-model.md`
- `docs/mvp-roadmap.md`
- `docs/implementation-task-list.md`
- `docs/superpowers/specs/2026-05-20-model-eval-design.md`
- `docs/handoff/model-eval-research-prompt.md`
- `docs/adr/0001-warmers-as-first-class-entities.md`

Architecture chosen: The docs define ConversationWarmer as a structured/versioned experimental variable, keep Experiment immutable and reproducible, and treat Playground and Benchmark Suite as product modes around the MVP Experiment path.

## F03 - Manifest Schema And Validation

Description: YAML/JSON manifest parsing with typed cases, artifacts, model configs, system prompts, warmers, evaluators, design, evaluation, and controls.

Files involved:

- `backend/model_eval_api/manifest.py`
- `tests/test_manifest_contract.py`
- `examples/copper_memo_context_sensitivity.yaml`

Architecture chosen: Pydantic models normalize inline objects and library references, then a separate validation pass reports duplicate IDs, empty dimensions, unsupported designs, malformed provider params, bad controls, and unknown design references.

## F04 - Full-Factorial Expansion And Stable Run IDs

Description: Deterministic expansion of manifest dimensions into logical runs and replicate attempts, including optional randomized run order.

Files involved:

- `backend/model_eval_api/manifest.py`
- `backend/model_eval_api/run_generation.py`
- `tests/test_manifest_contract.py`

Architecture chosen: The manifest expander builds full-factorial combinations across case, model, system prompt, and warmer dimensions. Stable SHA-derived run and attempt IDs are computed from experiment and dimension IDs, and randomization uses a stored seed.

## F05 - Manifest CLI Commands

Description: Headless commands for validating, previewing, and expanding manifests before any persistence or provider calls.

Files involved:

- `cli/model_eval_cli/main.py`
- `backend/model_eval_api/manifest.py`
- `tests/test_manifest_contract.py`
- `examples/copper_memo_context_sensitivity.yaml`

Architecture chosen: Typer commands call the same manifest parser and expander used by the API. CLI failures print actionable validation errors, and preview/expand output stays deterministic JSON.

## F06 - API Bootstrap And Manifest Endpoints

Description: FastAPI app bootstrap, health check, CORS setup, and manifest validate/preview endpoints.

Files involved:

- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `backend/model_eval_api/manifest.py`
- `tests/test_manifest_contract.py`

Architecture chosen: FastAPI receives raw manifest payloads, validates them through the shared manifest module, and returns typed Pydantic responses so the CLI and API keep one manifest contract.

## F07 - Database Configuration And Migration Chain

Description: SQLAlchemy engine/session setup and Alembic migrations for the MVP schema through privacy/reproducibility metadata.

Files involved:

- `backend/model_eval_api/persistence/database.py`
- `backend/model_eval_api/persistence/models.py`
- `alembic.ini`
- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/bdb42fbb358a_create_persistence_tables.py`
- `alembic/versions/f2d8b9c4a1e5_add_executor_attempt_metadata.py`
- `alembic/versions/a3f5c2d8b901_add_warmer_version_note.py`
- `alembic/versions/c8a9f2d7b6e1_add_experiment_pricing_snapshot.py`
- `alembic/versions/e7d4a2c9f013_add_privacy_repro_safety_metadata.py`
- `tests/test_persistence_phase2.py`
- `tests/test_privacy_repro_phase13.py`

Architecture chosen: The ORM is the canonical schema and Alembic captures incremental table/column changes. Tests use isolated SQLite sessions while local development can use host-installed Postgres.

## F08 - Versioned Library Object Persistence

Description: CRUD-style persistence helpers for workspaces, projects, cases, artifacts, system prompts, conversation warmers, model configs, and evaluators.

Files involved:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `tests/test_persistence_phase2.py`
- `tests/test_library_builder_phase6.py`

Architecture chosen: Versioned library tables use project-scoped slug/version unique constraints. Repository helpers create records and snapshot their public fields immediately so later edits do not mutate historical experiment inputs.

## F09 - Experiment Snapshotting And RunAttempt Separation

Description: Persisted experiments, immutable library snapshots, logical runs, and concrete run attempts.

Files involved:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `tests/test_persistence_phase2.py`
- `tests/test_executor_phase5.py`

Architecture chosen: `Experiment` stores manifest, design, controls, pricing, and selected library snapshots. `Run` stores one logical configuration and model input snapshot, while `RunAttempt` records provider call history, retries, dry-runs, and reruns without overwriting the logical run.

## F10 - Artifact Handling Baseline

Description: Local artifact registration, metadata capture, checksums, text ingestion, image dimension detection, and explicit artifact input modes.

Files involved:

- `backend/model_eval_api/artifact_types.py`
- `backend/model_eval_api/artifacts.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `tests/test_artifacts_phase3.py`

Architecture chosen: Artifacts are metadata-first library objects with storage rooted outside the repo by default. Runs record the exact artifact input mode in `model_input_snapshot`, and mixed input modes fail early in the MVP.

## F11 - Provider Adapter Core Boundary

Description: Shared provider request/response models, adapter protocol, dry-run execution path, token extraction, cost estimation, and response normalization boundary.

Files involved:

- `backend/model_eval_api/providers/base.py`
- `backend/model_eval_api/providers/models.py`
- `backend/model_eval_api/providers/__init__.py`
- `backend/model_eval_api/providers/errors.py`
- `tests/test_provider_adapters_phase4.py`

Architecture chosen: Provider adapters receive normalized run snapshots and return normalized responses while preserving raw provider params. The base adapter enforces local-only/dry-run safety before any live client is invoked.

## F12 - OpenAI Provider Adapter

Description: OpenAI request construction and response normalization from Model Eval run snapshots.

Files involved:

- `backend/model_eval_api/providers/openai.py`
- `backend/model_eval_api/providers/base.py`
- `tests/test_provider_adapters_phase4.py`

Architecture chosen: The adapter maps final messages into Responses-style `input`, maps normalized/raw reasoning settings into OpenAI reasoning payloads, preserves provider-specific params, and extracts response IDs, output text, usage, costs, and system fingerprint metadata.

## F13 - Anthropic Provider Adapter

Description: Anthropic request construction and response normalization from Model Eval run snapshots.

Files involved:

- `backend/model_eval_api/providers/anthropic.py`
- `backend/model_eval_api/providers/base.py`
- `tests/test_provider_adapters_phase4.py`

Architecture chosen: The adapter splits system/developer messages from chat messages, maps normalized reasoning levels to Anthropic thinking budgets when raw params do not override them, and normalizes content, usage, response IDs, and stop/model metadata.

## F14 - Provider Policy, Pricing, And Error Classification

Description: Provider allow/deny settings, local-only enforcement, static pricing snapshots, and retry/error classification.

Files involved:

- `backend/model_eval_api/providers/settings.py`
- `backend/model_eval_api/providers/pricing.py`
- `backend/model_eval_api/providers/errors.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/persistence/repositories.py`
- `tests/test_provider_adapters_phase4.py`
- `tests/test_privacy_repro_phase13.py`

Architecture chosen: Provider policy is checked before execution from environment, project, and manifest controls. Pricing is stored as a static MVP snapshot per experiment, and provider exceptions are classified into retryable, blocked, invalid request, auth, or unknown categories.

## F15 - Executor Lifecycle, Cache, Retry, And Safety Controls

Description: Synchronous executor for queued experiments and runs, including retries, cache hits, cost caps, cancellation, context budgets, egress labeling, and deterministic evaluator triggering.

Files involved:

- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/execution_states.py`
- `backend/model_eval_api/providers/*`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/deterministic_evaluators.py`
- `tests/test_executor_phase5.py`
- `tests/test_privacy_repro_phase13.py`

Architecture chosen: The executor advances Experiment, Run, and RunAttempt states separately. Provider cache entries are keyed by input snapshot plus provider config, retries create new attempts with parent metadata, and safety checks mark attempts failed before provider execution when policy blocks them.

## F16 - Redis/RQ Queue Adapter

Description: Queue wrapper for experiment expansion, run execution, deterministic evaluator jobs, and export-generation jobs.

Files involved:

- `backend/model_eval_api/queue.py`
- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/deterministic_evaluators.py`
- `Makefile`
- `pyproject.toml`
- `tests/test_executor_phase5.py`

Architecture chosen: RQ is a thin asynchronous boundary over the synchronous executor services. Jobs open their own database session and return small metadata payloads, keeping the core execution logic testable without Redis.

## F17 - Library And Experiment Builder API

Description: Project-scoped API endpoints for listing/creating library records, previewing manifests against library references, saving/updating drafts, and queueing experiments.

Files involved:

- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/queue.py`
- `tests/test_library_builder_phase6.py`

Architecture chosen: The API uses project slugs as the tenancy boundary, defaults manifest controls to local-only, snapshots draft experiments through repository services, and queues execution through the queue adapter after marking experiments queued.

## F18 - Run Monitor API

Description: API endpoints for experiment lists, runs, attempts, failures, analytics, audit logs, retry, cancel experiment, and cancel run actions.

Files involved:

- `backend/model_eval_api/main.py`
- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/persistence/models.py`
- `tests/test_executor_phase5.py`
- `tests/test_results_analytics_phase10.py`
- `tests/test_privacy_repro_phase13.py`

Architecture chosen: Monitor endpoints expose read models derived from persisted experiments, runs, and attempts. Mutating actions delegate to executor helpers so retry and cancellation preserve attempt history and audit behavior.

## F19 - Frontend App Shell, Modes, And Visual System

Description: React workbench shell with sidebar navigation, product-mode separation, shared controls, responsive styling, and top-level state.

Files involved:

- `frontend/src/App.tsx`
- `frontend/src/main.tsx`
- `frontend/src/styles.css`
- `frontend/src/App.test.tsx`
- `frontend/index.html`

Architecture chosen: A single Vite/React app manages route state for Library, Experiment Builder, Run Monitor, Comparison Workspace, and Results. Shared UI helpers live in `App.tsx`, with styles centralized in `styles.css`.

## F20 - Frontend Library Editors

Description: UI editors for cases, artifacts, system prompts, conversation warmers, model configs, and evaluators.

Files involved:

- `frontend/src/App.tsx`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/experimentBuilder.test.ts`

Architecture chosen: Editor components operate on typed local records from `experimentBuilder.ts`, keep warmer messages structured as role/content arrays, and persist records through a typed API adapter.

## F21 - Frontend Experiment Builder And Manifest Editing

Description: Experiment selection, manifest generation, inline validation, preview metrics, save draft, update draft, and queue actions.

Files involved:

- `frontend/src/App.tsx`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/experimentBuilder.test.ts`

Architecture chosen: The builder transforms local selected library IDs into a full-factorial manifest, validates dimensions/client-side JSON, estimates run/cost counts locally, and delegates persistence/queueing to the API. The manifest editor path lets JSON edits replace the generated draft when valid.

## F22 - Frontend API Client

Description: Browser-side API types and request helpers for library records, experiment drafts, monitor data, review sets, review decisions, and analytics.

Files involved:

- `frontend/src/api.ts`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/App.tsx`
- `frontend/src/App.test.tsx`

Architecture chosen: The client keeps backend DTOs typed at the boundary, uses one configurable API base URL, wraps non-2xx responses in `ApiError`, and converts frontend record shape into backend snake_case payloads.

## F23 - Run Monitor UI

Description: UI table and detail drawer for run progress, filtering, cost/token/latency summaries, retries, cancellation, and provider/cost safeguards.

Files involved:

- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/App.test.tsx`

Architecture chosen: The UI fetches monitor experiments, runs, and attempts, flattens them into monitor rows, applies client-side filters, derives progress summaries, and presents attempt metadata in a drawer without mutating backend state directly.

## F24 - Human Review Persistence And Pairwise Generation

Description: Backend blind pairwise review-set creation, answer randomization, metadata hiding/reveal, reviewer decisions, and typed score persistence.

Files involved:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `tests/test_review_phase8.py`

Architecture chosen: Review sets are project/experiment-owned queues. Pairwise items store answer snapshots and hidden reveal metadata separately, while review decisions create typed Score rows for pairwise preference, pass/fail, failure tags, rubric notes, and freeform notes.

## F25 - Comparison Workspace UI

Description: Frontend blind review workflow with answer labels, winner/tie/cannot-judge decisions, pass/fail controls, failure tags, notes, and metadata reveal.

Files involved:

- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/App.test.tsx`

Architecture chosen: The workspace creates or loads one blind review set for the selected experiment, keeps reviewer input local until submission, and only fetches reveal metadata when the user requests it.

## F26 - Deterministic Evaluator Engine

Description: Required-section, token-budget, JSON-schema, citation-required placeholder, and no-empty-output evaluators with score persistence.

Files involved:

- `backend/model_eval_api/deterministic_evaluators.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/persistence/repositories.py`
- `tests/test_deterministic_evaluators_phase9.py`
- `tests/test_exports_phase11.py`

Architecture chosen: Evaluators are small protocol implementations selected by snapshot definition. Automatic executor scoring skips dry-run attempts, and the headless score command can run one deterministic evaluator over succeeded non-dry-run attempts.

## F27 - Results Analytics Service

Description: Backend aggregation for win rate, pass rate, failure tag frequency, warmer lift, context sensitivity, divergence placeholders, cost-quality, latency-quality, and failure rates.

Files involved:

- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/headless.py`
- `docs/results-analytics.md`
- `tests/test_results_analytics_phase10.py`
- `tests/test_exports_phase11.py`

Architecture chosen: Analytics derive from terminal attempts and typed human-review scores, favor rates with counts over calibrated numeric scores, and compute warmer lift only against comparable no-warmer baselines.

## F28 - Results UI

Description: Results screen with summary metrics, score/cost/failure tables, warmer lift bars, context sensitivity labels, cost-quality frontier, and export actions.

Files involved:

- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/App.test.tsx`

Architecture chosen: The screen consumes the analytics endpoint as a typed DTO, renders directional tables and simple visual summaries, and avoids implying more precision than the MVP analytics can support.

## F29 - Headless Workflow Commands

Description: CLI workflow for local-first run, compare, blind review export, deterministic scoring, and experiment export.

Files involved:

- `cli/model_eval_cli/main.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/persistence/database.py`
- `docs/headless-workflow.md`
- `tests/test_exports_phase11.py`

Architecture chosen: CLI commands create the schema if needed, open one session per command, and delegate to headless services that share executor, evaluator, review, export, and analytics modules with the API.

## F30 - Export Serialization

Description: Markdown, CSV, JSON, and blind-review queue exports with stable headers, hidden metadata protection, and reproducibility payloads.

Files involved:

- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/persistence/models.py`
- `docs/headless-workflow.md`
- `tests/test_exports_phase11.py`

Architecture chosen: JSON exports include manifest/library/run/attempt snapshots plus analytics, CSV uses one stable cross-section header, Markdown produces a readable experiment report, and blind review exports omit answer metadata until reveal.

## F31 - Copper Memo Seed Data And Manifest

Description: Local-only copper memo case, two system prompts, four warmers, two model configs, evaluators, and full-factorial manifest.

Files involved:

- `backend/model_eval_api/copper_seed.py`
- `cli/model_eval_cli/seed_data.py`
- `cli/model_eval_cli/main.py`
- `examples/copper_memo_context_sensitivity.yaml`
- `tests/test_cli_seed.py`
- `tests/test_manifest_contract.py`

Architecture chosen: Seed data is a structured payload rather than raw prompt text, preserving warmers as first-class objects and matching the example manifest's 16 logical runs and 32 attempts.

## F32 - Copper Memo Demo Builder

Description: End-to-end synthetic demo that creates or refreshes copper memo library records, experiment, dry-run attempts, sample reviews, analytics, and exports.

Files involved:

- `backend/model_eval_api/copper_demo.py`
- `backend/model_eval_api/copper_seed.py`
- `cli/model_eval_cli/main.py`
- `docs/copper-memo-demo.md`
- `docs/superpowers/plans/2026-05-20-copper-demo-readiness.md`
- `tests/test_copper_demo_phase12.py`

Architecture chosen: The demo is idempotent and local-only. It rebuilds the experiment from the manifest, populates synthetic provider outputs and deterministic scores, creates a blind review set, records sample human decisions, and writes optional exports outside the repo.

## F33 - Privacy, Reproducibility, And Safety

Description: Local-only defaults, provider allow/deny policy, per-run data egress labels, context budget reports, fail-on-over-budget behavior, audit logs, metadata redaction, and provider metadata persistence.

Files involved:

- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/providers/settings.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/headless.py`
- `docs/privacy-repro-safety.md`
- `tests/test_privacy_repro_phase13.py`

Architecture chosen: Safety checks happen before adapter execution, audit logs store metadata only, raw provider payloads and outputs live in controlled snapshots/attempts, provider secrets remain environment-only for MVP, and over-budget inputs fail instead of truncating.

## F34 - API Schema And Payload Mapping Layer

Description: Pydantic request models and response payload helpers for library, experiment, monitor, review, audit, and analytics endpoints.

Files involved:

- `backend/model_eval_api/schemas.py`
- `backend/model_eval_api/main.py`
- `frontend/src/api.ts`
- `tests/test_library_builder_phase6.py`
- `tests/test_review_phase8.py`
- `tests/test_results_analytics_phase10.py`

Architecture chosen: Backend request models validate incoming writes, while `main.py` response helpers flatten ORM objects into simple JSON payloads. The frontend mirrors those payloads with TypeScript interfaces rather than sharing generated types.

## F35 - Test Suite Organization

Description: Phase-oriented backend and frontend tests covering manifest contracts, persistence, artifacts, providers, executor behavior, library builder, review, evaluators, analytics, exports, copper demo readiness, and privacy/repro safety.

Files involved:

- `tests/test_manifest_contract.py`
- `tests/test_persistence_phase2.py`
- `tests/test_artifacts_phase3.py`
- `tests/test_provider_adapters_phase4.py`
- `tests/test_executor_phase5.py`
- `tests/test_library_builder_phase6.py`
- `tests/test_review_phase8.py`
- `tests/test_deterministic_evaluators_phase9.py`
- `tests/test_results_analytics_phase10.py`
- `tests/test_exports_phase11.py`
- `tests/test_copper_demo_phase12.py`
- `tests/test_privacy_repro_phase13.py`
- `tests/test_cli_seed.py`
- `frontend/src/App.test.tsx`
- `frontend/src/experimentBuilder.test.ts`

Architecture chosen: Tests are grouped by delivery phase and feature surface. Backend tests use isolated sessions and mocked/dry-run provider paths, while frontend tests use Vitest and Testing Library around user-visible workflows.

## F36 - V2 LLM Judge Config Library

Description: Versioned LLM judge configurations with prompt, rubric, output schema, judge model reference, raw provider params, calibration status, API/library editor support, and immutable evaluator snapshots.

Files involved:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `alembic/versions/d4c3b2a19014_add_llm_judge_configs.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/App.test.tsx`
- `tests/test_llm_judges_phase14.py`
- `docs/results-analytics.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: `LLMJudgeConfig` is a project-scoped versioned library object that resolves its judge model against existing model configs and stores sanitized provider params in snapshots. Manifest evaluators with `type: llm_judge` can reference a stored judge config or define one inline, and experiments freeze the judge config snapshot so later judge edits do not mutate historical evaluator definitions.

## F37 - V2 LLM Judge Execution And Calibration

Description: Dry-run and gated live LLM judge execution over stored run attempts, metadata-blind pairwise request construction, position-swapped comparisons, judge pairwise/pass-fail/rubric score persistence, calibration analytics, verbosity-bias reporting, and API/CLI/UI run controls.

Files involved:

- `backend/model_eval_api/llm_judges.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/results_analytics.py`
- `cli/model_eval_cli/main.py`
- `alembic/versions/e5f6a7b80115_add_judge_executions.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_llm_judge_execution_phase15.py`
- `docs/headless-workflow.md`
- `docs/results-analytics.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: `JudgeExecution` stores the frozen judge config, source attempt IDs, request shape, local dry-run or gated live response, and produced score IDs. The judge runner groups completed attempts into blind pairwise comparisons, optionally evaluates both answer orders, stores token counts for verbosity-bias analysis, records pairwise/pass-fail/rubric judge scores, and reuses provider policy, experiment local-only, cost-cap, and per-comparison context-budget gates before allowing non-dry-run execution.

## F38 - V2 Review Queues And Failure Taxonomies

Description: Project-scoped reviewers, deterministic review assignments, reviewer queue APIs, versioned failure taxonomy snapshots, assignment-aware review submission, reviewer disagreement analytics, and taxonomy/export metadata.

Files involved:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/headless.py`
- `alembic/versions/f6a7b8c91216_add_review_assignments_taxonomies.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/App.test.tsx`
- `tests/test_review_queues_phase16.py`
- `tests/test_review_phase8.py`
- `tests/test_results_analytics_phase10.py`
- `tests/test_exports_phase11.py`
- `docs/v2-implementation-task-list.md`
- `docs/implementation-task-list.md`

Architecture chosen: `Reviewer`, `ReviewAssignment`, and `FailureTaxonomy` extend the existing blind review set model without changing the human-label score contract. Review sets snapshot the active taxonomy, assignments are idempotently created per reviewer and item, queue payloads hide metadata until reveal, assignment decisions stamp reviewer and taxonomy metadata into score values, and analytics/export paths report reviewer coverage, disagreement, assignment status, and taxonomy version.

## F39 - V2 Benchmark Suites And Dataset Splits

Description: Versioned benchmark suites with locked membership, split-aware case selection, reproducible suite previews/reruns, manifest suite references, CLI execution, and suite management UI.

Files involved:

- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/manifest.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/schemas.py`
- `cli/model_eval_cli/main.py`
- `alembic/versions/a7c9d1e2f317_add_benchmark_suites.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/experimentBuilder.test.ts`
- `tests/test_benchmark_suites_phase17.py`
- `tests/test_manifest_contract.py`
- `docs/v2-implementation-task-list.md`
- `docs/implementation-task-list.md`

Architecture chosen: `BenchmarkSuite` is a project-scoped versioned library object with `BenchmarkSuiteItem` rows that snapshot case, model, prompt, warmer, and evaluator membership at the selected versions. Suite previews filter archived cases and requested dataset splits before building a normal full-factorial manifest, so suite reruns reuse the existing experiment snapshot and execution paths while preserving suite references in the manifest.

## F40 - V2 Replicates And Uncertainty

Description: Reliability replicate metadata, retry-vs-replicate separation, nondeterminism summaries, confidence intervals, uncertainty labels, and export/UI visibility.

Files involved:

- `backend/model_eval_api/manifest.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/executor.py`
- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/headless.py`
- `alembic/versions/b8d4f1a6c219_add_replicate_metadata.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_manifest_contract.py`
- `tests/test_executor_phase5.py`
- `tests/test_results_analytics_phase10.py`
- `tests/test_exports_phase11.py`
- `docs/results-analytics.md`
- `docs/v2-implementation-task-list.md`
- `docs/implementation-task-list.md`

Architecture chosen: Replicated attempts remain part of the existing full-factorial run expansion, but each attempt now carries a `replicate_group_id` and `attempt_kind`. Retry attempts inherit the replicate group and are marked as `retry`, letting analytics count recovery separately while reliability intervals use only root replicate attempts.

## F41 - V2 Metric Adapter Registry And Configs

Description: Project-scoped metric adapter configuration persistence, immutable adapter snapshots, local deterministic RAG-style metric registry, and DeepEval-style result mapping.

Files involved:

- `backend/model_eval_api/metric_adapters.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/schemas.py`
- `alembic/versions/b1c2d3e4f521_add_metric_adapter_configs.py`
- `tests/test_metric_adapters_phase21.py`
- `docs/v2-implementation-task-list.md`

Architecture chosen: `MetricAdapterConfig` is a project-scoped versioned library object that snapshots adapter kind, adapter version, required inputs, output schema, local-only status, and capability metadata at creation. The local registry exposes deterministic retrieval precision, citation coverage, groundedness checklist, and answer relevance adapters, while the DeepEval-style mapping accepts fixture dictionaries and produces normal Model Eval score payloads without importing external SDKs.

## F42 - V2 Divergence And Carryover Metrics

Description: Advanced context-sensitivity analytics for semantic, claim, conclusion, confidence, section-structure, token-length, failure-mode, and warmer carryover divergence.

Files involved:

- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/deterministic_evaluators.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/headless.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_results_analytics_phase10.py`
- `tests/test_deterministic_evaluators_phase9.py`
- `tests/test_exports_phase11.py`
- `docs/results-analytics.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: Divergence metrics are stored as typed evaluator scores or derived analytics rows keyed to comparable no-warmer baselines. Deterministic text and structure heuristics provide local fallbacks, judge-backed claim and conclusion scores are preferred when available, and Results/export surfaces label heuristic rows so users can distinguish calibrated evidence from shallow signals.

## F43 - V2 Artifact Preprocessing Pipeline

Description: Versioned preprocessing records for PDFs, images, OCR text, selected figures, extracted tables, retrieval chunks, paper cards, and run input snapshots.

Files involved:

- `backend/model_eval_api/artifact_types.py`
- `backend/model_eval_api/artifacts.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `backend/model_eval_api/persistence/snapshots.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/executor.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_artifacts_phase3.py`
- `docs/data-model.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: Artifact preprocessing creates metadata-first derived artifact records with parser names, parser versions, checksums, local storage URIs, source offsets or page regions, and immutable snapshots. Experiment inputs choose explicit source or derived modes, and run model input snapshots preserve exactly which direct files or derived bundles the model saw without copying private source files into git.

## F44 - V2 Metric Adapter Execution And Results

Description: Execution path, API/CLI controls, frontend controls, score persistence, analytics, and export inclusion for registered metric adapters.

Files involved:

- `backend/model_eval_api/metric_adapter_execution.py`
- `backend/model_eval_api/metric_adapters.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/headless.py`
- `cli/model_eval_cli/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_metric_adapters_phase21.py`
- `tests/test_exports_phase11.py`
- `docs/headless-workflow.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: Adapter execution resolves compatible local-only configs against successful attempts and available artifact-derived inputs, records typed score payloads through the same evaluator score path as deterministic and judge scores, and exposes dry-run/API/CLI/UI controls that can run one adapter or all compatible adapters for an experiment.

## F45 - V2 Promptfoo Interop

Description: Promptfoo-style import and export for prompts, providers, tests, assertions, variables, options, warnings, API/UI surfaces, and CLI workflows.

Files involved:

- `backend/model_eval_api/promptfoo.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/schemas.py`
- `cli/model_eval_cli/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_promptfoo_interop_phase22.py`
- `docs/headless-workflow.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: Promptfoo import builds Model Eval manifest previews and optional project library records while preserving raw provider params and surfacing unsupported fields as warnings. Export maps compatible experiments back to Promptfoo YAML and emits explicit warnings for warmers, replicates, randomized ordering, unsupported evaluator kinds, and lossy controls.

## F46 - V2 OpenTelemetry Metadata Export

Description: Opt-in local OpenTelemetry-compatible JSON trace export for experiment, run, attempt, evaluator, review, artifact preprocessing, and export events with redaction guarantees.

Files involved:

- `backend/model_eval_api/otel_export.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/main.py`
- `backend/model_eval_api/persistence/models.py`
- `backend/model_eval_api/persistence/repositories.py`
- `cli/model_eval_cli/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/App.test.tsx`
- `tests/test_exports_phase11.py`
- `tests/test_privacy_repro_phase13.py`
- `docs/privacy-repro-safety.md`
- `docs/headless-workflow.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: The trace builder emits stable parent-linked spans with IDs, statuses, timings, provider metadata, token/cost totals, parser metadata, and audit links while excluding raw prompts, manifests, artifacts, warmer messages, request/response payloads, credentials, model outputs, file paths, checksums, and OCR text. Trace export is local-file oriented and records only metadata in audit details.

## F47 - V2 Cost-Quality Frontier Results UX

Description: Cost-quality frontier analytics and Results UI for dominance, uncertainty intervals, warmer lift versus distortion, judge calibration overlays, filters, and export rows.

Files involved:

- `backend/model_eval_api/results_analytics.py`
- `backend/model_eval_api/headless.py`
- `backend/model_eval_api/main.py`
- `frontend/src/App.tsx`
- `frontend/src/api.ts`
- `frontend/src/styles.css`
- `frontend/src/App.test.tsx`
- `tests/test_results_analytics_phase10.py`
- `tests/test_exports_phase11.py`
- `docs/results-analytics.md`
- `docs/v2-implementation-task-list.md`

Architecture chosen: Frontier rows are computed from the same analytics source used by API, exports, and UI. Rows compare quality, cost, and latency with interval metadata, mark dominated and non-dominated configurations, attach divergence summaries and calibration overlays, and support filters by case, suite, split, model, prompt, warmer, evaluator source, and reviewer.

## F48 - V2 Local Demo And Readiness Audit

Description: Local-only V2 copper demo, safe committed fixtures, synthetic execution/review/judge data, stable exports, UI smoke coverage, and V2 completion audit notes.

Files involved:

- `backend/model_eval_api/v2_demo.py`
- `backend/model_eval_api/copper_demo.py`
- `cli/model_eval_cli/main.py`
- `examples/v2_copper_benchmark_suite.yaml`
- `tests/fixtures/v2_demo_copper_context.txt`
- `tests/fixtures/v2_demo_copper_chart.svg`
- `tests/test_v2_demo_phase25.py`
- `frontend/src/experimentBuilder.ts`
- `frontend/src/App.test.tsx`
- `docs/v2-demo.md`
- `docs/v2-implementation-task-list.md`
- `docs/implementation-task-list.md`

Architecture chosen: The V2 demo builds or reuses a deterministic project, benchmark suite, artifact preprocessing run, synthetic succeeded attempts, multi-reviewer blind review assignments, synthetic judge outputs, metric adapter scores, analytics, and Markdown/CSV/JSON exports without live provider calls. The readiness audit links every V2 backlog item to a completed phase and leaves V3 backlog items explicitly unchecked and out of scope.

## Comparison To The Existing Task List

`docs/implementation-task-list.md` is phase-based and acceptance-criteria driven. This inventory maps that same implemented MVP into reviewable feature slices:

- Phases 0-1 map mostly to F01-F06 and F31.
- Phases 2-3 map mostly to F07-F10.
- Phases 4-5 map mostly to F11-F18.
- Phase 6 maps mostly to F17 and F19-F22.
- Phases 7-10 map mostly to F18 and F23-F28.
- Phase 11 maps mostly to F29-F30.
- Phase 12 maps mostly to F31-F32.
- Phase 13 maps mostly to F14, F15, F18, F30, and F33.
- V2 Phase 14 maps to F36.
- V2 Phase 15 maps to F37.
- V2 Phase 16 maps to F38.
- V2 Phase 17 maps to F39.
- V2 Phase 18 maps to F40.
- V2 Phase 19 maps to F42.
- V2 Phase 20 maps to F43.
- V2 Phase 21 maps to F41 and F44.
- V2 Phase 22 maps to F45.
- V2 Phase 23 maps to F46.
- V2 Phase 24 maps to F47.
- V2 Phase 25 maps to F48.

V2 backlog items are implemented and mapped above. V3 backlog items remain outside this implemented inventory, including production trace ingestion, active sampling, synthetic case generation, team administration, evaluator CI gates, prompt deployment, model release comparison, custom provider SDKs, local model support, and scheduled drift monitoring.
