# Model Eval Implementation Task List

This checklist expands the initial product plan into an implementation sequence for the first usable Model Eval workbench. The MVP target is a complete copper memo context-sensitivity demo: one final task, two models, two system prompts, four warmers, blind human review, simple scoring, context-sensitivity analytics, and export.

## Phase 0: Repo Foundation

Acceptance criteria:

- A new developer can install dependencies, run the API, run the CLI, run the frontend, and execute the baseline checks from the README.
- The repo has clear environment conventions and no required secrets committed.
- The project keeps a narrow MVP boundary: context sensitivity first, broad observability later.

Tasks:

- [x] Create the private GitHub repository and push the initial scaffold.
- [x] Add product, architecture, data-model, MVP roadmap, and design docs.
- [x] Add a FastAPI backend skeleton with a health endpoint.
- [x] Add a CLI package with manifest preview support.
- [x] Add a React/Vite frontend shell.
- [x] Add the copper memo context-sensitivity example manifest.
- [x] Add `.env.example` with placeholders for provider keys, database URL, Redis URL, artifact storage root, and local-only mode.
- [x] Add a `Makefile` or `justfile` for common commands: install, lint, test, build, api, worker, frontend, and preview-example.
- [x] Add README setup notes for SQLite defaults plus host-installed Postgres and Redis.
- [x] Add a short architecture decision record explaining why warmers are first-class entities rather than prompt text.
- [x] Add CI with Python compile/lint/tests and frontend build.
- [x] Add seed data command for the copper memo demo objects.

## Phase 1: Manifest Contract And Run Generation

Acceptance criteria:

- The CLI and API can validate a manifest, report actionable validation errors, and expand a full-factorial design into deterministic logical run definitions without calling providers.
- The copper memo example expands to 16 logical runs and 32 run attempts when `replicates: 2`.
- Manifest parsing preserves both normalized provider fields and raw provider parameters.

Tasks:

- [x] Define Pydantic models for manifest fields: cases, models, system prompts, warmers, design, evaluation, and controls.
- [x] Add manifest validation for required IDs, duplicate IDs, unknown references, invalid design types, invalid replicate counts, and malformed provider parameters.
- [x] Support inline prompt text and library references for cases, system prompts, warmers, model configs, and evaluators.
- [x] Implement full-factorial run expansion from cases x models x system prompts x warmers.
- [x] Add deterministic run IDs derived from experiment ID, case ID, model config ID, system prompt ID, warmer ID, and replicate index.
- [x] Add run-order randomization controlled by a manifest flag and a stored random seed.
- [x] Add estimated token and cost placeholders to the manifest preview response.
- [x] Extend CLI commands:
  - [x] `evalbench validate <manifest>`.
  - [x] `evalbench preview <manifest>`.
  - [x] `evalbench expand <manifest> --format json`.
- [x] Add API routes for manifest validation and run preview.
- [x] Add tests for valid manifests, invalid manifests, duplicate IDs, empty dimensions, and the copper memo run count.

## Phase 2: Persistence And Versioned Libraries

Acceptance criteria:

- The backend can persist projects, library objects, experiments, logical runs, attempts, scores, and review sets.
- Versioned snapshots are stored so later edits do not mutate historical experiments.
- The database schema cleanly separates `Run` from `RunAttempt`.

Tasks:

- [x] Add SQLAlchemy engine/session setup and database configuration.
- [x] Add Alembic migrations.
- [x] Create core tables:
  - [x] Workspace.
  - [x] Project.
  - [x] Case.
  - [x] Artifact.
  - [x] SystemPrompt.
  - [x] ConversationWarmer.
  - [x] ModelConfig.
  - [x] Evaluator.
  - [x] Experiment.
  - [x] Run.
  - [x] RunAttempt.
  - [x] Score.
  - [x] ReviewSet.
  - [x] ReviewItem.
- [x] Add version fields and immutable snapshot JSON fields for cases, prompts, warmers, artifacts, model configs, evaluators, and experiments.
- [x] Store warmer metadata: name, domain, user level, intent, messages, tags, version, and archived status.
- [x] Store normalized model fields: provider, model, temperature, max output tokens, reasoning level, capability flags, and raw provider params JSON.
- [x] Store run attempt request payload, response payload, provider response ID, status, error message, timing, token usage, and cost.
- [x] Store score records with type, evaluator type, criterion, value, explanation, confidence, evaluator version, and creation time.
- [x] Add repository/service functions for creating library objects, snapshotting objects into experiments, expanding runs, and recording attempts.
- [x] Add tests for migrations, unique constraints, snapshot immutability, and run/attempt relationships.

## Phase 3: Artifact Handling Baseline

Acceptance criteria:

- The MVP can register artifacts and record what each provider call saw, even before advanced PDF/image preprocessing exists.
- Artifact handling is explicit enough to avoid mixing direct file input, extracted text, screenshots, and retrieval chunks.

Tasks:

- [x] Add local artifact storage rooted outside the repo by default.
- [x] Add artifact metadata records for type, filename, checksum, size, MIME type, storage URI, and creation time.
- [x] Add artifact input mode enum: direct file, image direct, PDF text, PDF page screenshots, selected figures, retrieval chunks, and none.
- [x] Add run-level `model_input_snapshot` that records final messages and artifact input mode.
- [x] Add a minimal text artifact ingestion path for copied paper text or source excerpts.
- [x] Add image metadata capture for dimensions and MIME type.
- [x] Defer full PDF extraction, OCR, figure extraction, and paper cards to V2 unless needed for a demo.
- [x] Add tests for artifact registration, checksum stability, missing files, and run input snapshots.

## Phase 4: Provider Adapter Interfaces

Acceptance criteria:

- Provider-specific details are isolated behind adapters.
- OpenAI and Anthropic adapters can build request payloads from a normalized run snapshot while preserving raw provider settings.
- Provider calls can run in dry-run mode without using API keys.

Tasks:

- [x] Define a provider adapter interface with methods for capability lookup, request construction, execution, token extraction, cost estimation, and response normalization.
- [x] Add shared request/response dataclasses or Pydantic models for provider calls.
- [x] Add OpenAI adapter:
  - [x] Map system/developer/user messages correctly.
  - [x] Map normalized reasoning level to OpenAI reasoning settings.
  - [x] Preserve raw provider params.
  - [x] Capture provider response ID and usage fields when available.
- [x] Add Anthropic adapter:
  - [x] Map system prompt and chat messages correctly.
  - [x] Map normalized reasoning level to thinking budget or configured raw params.
  - [x] Preserve raw provider params.
  - [x] Capture provider response ID and usage fields when available.
- [x] Add provider allow/deny configuration for privacy-sensitive projects.
- [x] Add local-only mode that blocks outbound provider calls and allows dry-run attempts.
- [x] Add cost lookup configuration with pricing snapshots stored per experiment.
- [x] Add retry classification for provider errors: retryable, blocked by config, invalid request, provider auth, and unknown.
- [x] Add unit tests with mocked provider responses and no real network calls.

## Phase 5: Executor And Attempt Storage

Acceptance criteria:

- The backend can execute expanded runs asynchronously, persist every attempt, retry failed attempts according to policy, and keep run status separate from attempt history.
- Failed provider calls and nondeterministic reruns never overwrite prior attempts.

Tasks:

- [x] Choose and wire the MVP worker backend: Redis plus RQ, Celery, or Arq.
- [x] Add experiment execution states: draft, queued, running, complete, failed, canceled.
- [x] Add run states: pending, running, complete, failed, canceled, skipped.
- [x] Add attempt states: queued, running, succeeded, failed, canceled.
- [x] Add queue jobs for experiment expansion, run execution, deterministic evaluators, and export generation.
- [x] Enforce manifest controls: max parallel requests, max total cost, retry failed, cache provider calls, and local-only mode.
- [x] Add provider call cache keyed by model input snapshot plus provider config.
- [x] Add cancellation checks before starting queued attempts.
- [x] Add retry policy with attempt count, backoff, and terminal failure reason.
- [x] Add run monitor API endpoints for listing experiments, runs, attempts, failures, and retry/cancel actions.
- [x] Add tests for successful attempts, retryable failures, nonretryable failures, cancellation, cost cap enforcement, and cache hits.

## Phase 6: Library And Experiment Builder UI

Acceptance criteria:

- A user can create or inspect prompts, warmers, cases, artifacts, and model configs, then build an experiment and preview run count/cost before saving it.
- The UI keeps Playground, Experiment, and Benchmark Suite concepts distinct, even if only Experiment is fully functional in MVP.

Tasks:

- [x] Add frontend routing and app layout for Library, Experiment Builder, Run Monitor, Comparison Workspace, and Results.
- [x] Build Library screens for:
  - [x] Cases.
  - [x] Artifacts.
  - [x] System prompts.
  - [x] Conversation warmers.
  - [x] Model configs.
  - [x] Evaluators.
- [x] Add Monaco editor or a simple text editor fallback for prompt, warmer, and manifest editing.
- [x] Add warmer editor fields for domain, user level, intent, messages, tags, and version note.
- [x] Add model config editor fields for provider, model, normalized reasoning level, temperature, max output tokens, capability flags, and raw provider params JSON.
- [x] Build Experiment Builder:
  - [x] Select cases, models, prompts, warmers, evaluators, and controls.
  - [x] Select design type, with full factorial implemented first.
  - [x] Preview logical runs, run attempts, rough token estimate, and rough cost estimate.
  - [x] Save as draft experiment.
  - [x] Queue experiment run.
- [x] Add form validation and inline errors for invalid manifests or missing dimensions.
- [x] Add frontend tests or component smoke tests for editor validation and run preview.

## Phase 7: Run Monitor UI

Acceptance criteria:

- A user can see experiment progress, inspect failed attempts, retry failures, cancel queued work, and confirm cost/token/latency data as it arrives.

Tasks:

- [x] Build Run Monitor table with pending, running, failed, complete, canceled, and skipped states.
- [x] Add filters for case, model, prompt, warmer, status, and failure reason.
- [x] Add attempt detail drawer with request metadata, response metadata, timing, token usage, cost, and error details.
- [x] Add retry failed action.
- [x] Add cancel experiment action.
- [x] Add progress summary: total runs, completed runs, failed runs, total attempts, total cost, average latency, and remaining queued work.
- [x] Add safeguards for cost cap exceeded and provider allow/deny blocks.
- [x] Add tests for status rendering, retry action, cancellation action, and error detail rendering.

## Phase 8: Human Review And Scoring

Acceptance criteria:

- A user can review outputs blindly, choose pairwise winners, mark pass/fail, add notes, and apply failure tags.
- Scores are stored as typed records and can be aggregated later.

Tasks:

- [x] Add ReviewSet creation from a completed experiment.
- [x] Generate blind pairwise review items that hide model, system prompt, warmer, and cost metadata until reveal.
- [x] Randomize answer order for pairwise review items.
- [x] Store review item answer order and reviewer decisions.
- [x] Build Comparison Workspace:
  - [x] Blind pairwise view.
  - [x] Side-by-side output view.
  - [x] Winner, tie, and cannot judge decisions.
  - [x] Pass/fail decision.
  - [x] Failure tags.
  - [x] Freeform notes.
  - [x] Metadata reveal.
- [x] Add default failure tags for the copper memo demo: too generic, missed transmission mechanism, no quantified impact, invented numbers, weak trade expression, ignored inventory dynamics, no second-order effects, weak risks, overconfident conclusion, and spot/futures confusion.
- [x] Add score records for pairwise preference, pass/fail, failure tags, rubric notes, and freeform notes.
- [x] Add tests for blind randomization, metadata reveal, score persistence, and pairwise aggregation inputs.

## Phase 9: Deterministic Evaluators

Acceptance criteria:

- Deterministic checks can run before subjective review and produce typed scores.
- The copper memo demo has basic required-section and token-budget checks.

Tasks:

- [x] Add evaluator interface for deterministic code checks.
- [x] Add required-section evaluator for investment memos.
- [x] Add token budget evaluator.
- [x] Add JSON schema evaluator for structured-output cases.
- [x] Add citation-required evaluator placeholder.
- [x] Add no-empty-output evaluator.
- [x] Store evaluator version, score type, criterion, value, explanation, and confidence.
- [x] Run deterministic evaluators automatically after successful attempts.
- [x] Add tests for pass/fail evaluators, evaluator versioning, and score persistence.

## Phase 10: Results And Context-Sensitivity Analytics

Acceptance criteria:

- Results make the app's differentiated value visible: warmer lift, context sensitivity, distortion risk, and cost-quality tradeoffs.
- The UI avoids false precision and prefers win rates, pass/fail rates, failure rates, and clear qualitative labels.

Tasks:

- [x] Add aggregation service for win rate, pass rate, failure tag frequency, average cost, average latency, and token totals.
- [x] Add warmer lift calculation: score with warmer minus score with no warmer for comparable runs.
- [x] Add context sensitivity summary by fixed case, model, and system prompt across warmers.
- [x] Add divergence placeholders based on available scores and tags before semantic-diff models exist.
- [x] Add cost-quality table.
- [x] Add latency-quality table.
- [x] Add failure rate by model, prompt, warmer, and case.
- [x] Build Results screen:
  - [x] Score table.
  - [x] Cost table.
  - [x] Failure tag table.
  - [x] Warmer lift chart.
  - [x] Context sensitivity table.
  - [x] Cost-quality frontier.
  - [x] Export actions.
- [x] Add caution copy in docs and UI for uncalibrated numeric scores.
- [x] Add tests for aggregation correctness, no-winner/tie cases, missing no-warmer baseline, and cost aggregation.

## Phase 11: Exports And Headless Workflow

Acceptance criteria:

- The CLI is useful without the UI.
- Experiments can be exported for review, sharing, and outside analysis.

Tasks:

- [x] Add `evalbench run <manifest>` with dry-run and local-only modes.
- [x] Add `evalbench compare <experiment> --baseline <experiment>`.
- [x] Add `evalbench review <experiment> --blind` to generate a review queue export.
- [x] Add `evalbench score <experiment> --evaluator <id>`.
- [x] Add `evalbench export <experiment> --format markdown|csv|json`.
- [x] Add Markdown export with experiment summary, configs, scores, costs, failure tags, and key examples.
- [x] Add CSV export for runs, attempts, scores, reviews, and aggregate results.
- [x] Add JSON export with full reproducibility metadata.
- [x] Add export tests for stable headers, stable JSON shape, and no hidden metadata in blind review exports.

## Phase 12: Copper Memo Demo Readiness

Acceptance criteria:

- The demo proves the product exists for a specific reason: measuring how conversation warmers change final-task behavior.
- The demo can be rerun from a manifest and reviewed in the UI.

Tasks:

- [x] Create library records for the copper memo case.
- [x] Create four warmers: none, expert user, low-knowledge user, and adversarial user.
- [x] Create two system prompts: expert investment analyst and general finance assistant.
- [x] Create two model configs for the selected OpenAI and Anthropic models.
- [x] Create deterministic evaluators for required sections and token budget.
- [x] Run or dry-run the full experiment with 16 logical runs and 32 attempts.
- [x] Create a blind pairwise review set.
- [x] Complete sample human reviews.
- [x] Generate results showing warmer lift, context sensitivity, failure tags, cost, and latency.
- [x] Export the demo report in Markdown, CSV, and JSON.
- [x] Add screenshots or short docs showing the demo workflow.

## Phase 13: Privacy, Reproducibility, And Safety

Acceptance criteria:

- Private prompts, artifacts, finance memos, papers, screenshots, and code can be evaluated with clear data-egress controls.
- Experiments are reproducible enough to compare across provider drift.

Tasks:

- [x] Add provider allow/deny list per project.
- [x] Add per-run data egress labels.
- [x] Add local-only mode in API, CLI, and executor.
- [x] Add encrypted API key storage plan or document local env var use for MVP.
- [x] Store provider, model name, provider response ID, timestamp, request payload, response payload, and pricing snapshot.
- [x] Store system fingerprint or equivalent provider metadata when available.
- [x] Add audit log table for experiment creation, execution, retry, cancellation, review, export, and provider-call events.
- [x] Add context budget estimation and included/dropped message reporting before provider calls.
- [x] Add truncation policy field even if MVP only supports fail-on-over-budget.
- [x] Add tests for local-only enforcement, provider blocks, audit log writes, and over-budget behavior.

## V2 Backlog

- [x] LLM judge builder. Completed in V2 Phase 14.
- [x] Judge calibration against human labels. Completed in V2 Phase 15.
- [x] Position-swapped pairwise judging. Completed in V2 Phase 15.
- [x] Verbosity-bias controls. Completed in V2 Phase 15.
- [x] Regression benchmark suites. Completed in V2 Phase 17.
- [x] Dataset splits: dev, validation, holdout, and archived benchmark sets. Completed in V2 Phase 17.
- [x] Replicated runs for nondeterminism analysis. Completed in V2 Phase 18.
- [x] Confidence intervals and uncertainty display. Completed in V2 Phase 18.
- [x] Promptfoo import/export. Completed in V2 Phase 22.
- [x] OpenTelemetry trace export. Completed in V2 Phase 23.
- [x] RAG metric adapters. Completed in V2 Phase 21.
- [x] DeepEval-style metric adapters. Completed in V2 Phase 21.
- [x] Full artifact preprocessing pipeline for PDFs, images, OCR, figures, tables, and paper cards. Completed in V2 Phase 20.
- [x] Review queues for multiple reviewers. Completed in V2 Phase 16.
- [x] Failure taxonomy builder. Completed in V2 Phase 16.
- [x] Semantic, claim, conclusion, confidence, and structure divergence metrics. Completed in V2 Phase 19.
- [x] Better cost-quality frontier visualizations. Completed in V2 Phase 24.

V2 completion evidence lives in `docs/v2-implementation-task-list.md`, `docs/v2-demo.md`, and `FEATURE_INVENTORY.md`. The V3 backlog below remains unchecked and out of scope unless explicitly promoted.

## V3 Backlog

- [ ] Production trace ingestion.
- [ ] Active sampling for review.
- [ ] Synthetic case generation from failure taxonomies.
- [ ] Team review workflow.
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
- [ ] Browser smoke test for the frontend after material UI changes. Not run for this docs-only branch.
- [ ] Host-service smoke check for Postgres, Redis, API, and worker. Not run for this docs-only branch.
