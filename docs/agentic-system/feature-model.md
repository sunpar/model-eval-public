# Feature Model

Model Eval is a local-first workbench for measuring how prior conversation context changes model behavior on the same final task. The implemented system spans a FastAPI backend, SQLAlchemy/Alembic persistence, provider execution and safety gates, Typer CLI workflows, a React/Vite frontend, analytics/evaluator services, Promptfoo/OpenTelemetry interoperability, and synthetic copper demos.

The main product shape is warmer-first context-sensitivity analysis with immutable experiment snapshots, logical runs separated from concrete run attempts, blind review, judge and metric evaluation, and reproducible exports.

## Implemented Areas

- Development foundation, local services, CI, Python packaging, frontend build/test setup, and reproducible dependency locking.
- Warmer-first product model, manifest expansion, run generation, and immutable experiment snapshots.
- Project-scoped libraries for cases, artifacts, system prompts, warmers, model configs, evaluators, judges, metric adapters, benchmark suites, reviewers, and taxonomies.
- Provider adapters for OpenAI and Anthropic with local-only, provider policy, context-budget, cost-cap, cache, retry, cancellation, and audit controls.
- Artifact storage and preprocessing for direct files/images, PDF text, page screenshots, OCR text, selected figures, tables, retrieval chunks, paper cards, and mixed derived bundles.
- Blind review, multi-reviewer queues, failure taxonomies, deterministic evaluators, LLM judges, metric adapters, divergence/carryover analytics, uncertainty intervals, and cost-quality frontier rows.
- Headless CLI flows, stable Markdown/CSV/JSON exports, Promptfoo import/export, metadata-only OpenTelemetry JSON export, and local-only V1/V2 demos.

## Known Boundaries

- V2 is complete through Phase 25; V3 backlog items are deliberately out of scope unless promoted.
- Direct manifest expansion remains full-factorial centered. Benchmark-suite reruns and reliability replicates extend that base rather than adding every design family.
- Provider credentials stay in local environment variables. Encrypted in-app key storage is deferred.
- Artifact storage is local filesystem first. Cloud/S3 storage remains a later architecture option.
- Synthetic demo signals prove workflow coverage; they are not real provider quality evidence.
- Browser and Docker Compose service smoke checks are separate environment-level validations and are not replaced by unit tests.

The detailed JSON feature model is in `docs/agentic-system/feature-model.json`. The review slice map is in `docs/agentic-system/review/slice-plan.md`.
