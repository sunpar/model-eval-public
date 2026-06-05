# Architecture

## System Components

Backend:

- FastAPI API for libraries, experiment manifests, run orchestration, reviews, and exports.
- Pydantic schemas at the boundary.
- SQLAlchemy models for durable experiment records.
- SQLite by default for local demos/tests, with host-installed Postgres supported for local service smoke checks.
- Redis plus an RQ worker queue for provider calls and scoring jobs when asynchronous execution is enabled.
- Local filesystem artifact storage first, with S3-compatible storage later.

Frontend:

- React, TypeScript, Vite, and lucide-react.
- Single-page workbench screens for Library, Experiment Builder, Run Monitor, Comparison Workspace, and Results.
- Native form controls and text areas for library records, manifests, Promptfoo import/export, judges, metric adapters, suites, and artifact controls.
- Side-by-side output comparison with metadata hidden until reveal.
- Review workspace for blind pairwise decisions, pass/fail labels, notes, rubric notes, failure tags, reviewers, assignments, and taxonomies.

CLI:

- Manifest validation.
- Run execution.
- Experiment comparison.
- Blind review queue export.
- Deterministic scoring, LLM judge execution, metric adapter execution, and benchmark suite runs.
- Promptfoo import/export and OpenTelemetry-compatible metadata export.
- Copper memo and V2 local-only demo generation.
- Markdown, CSV, JSON, Promptfoo, and `otel-json` export.

## Data Flow

1. User creates or imports cases, artifacts, system prompts, warmers, model configs, and evaluators.
2. User builds an experiment by choosing a study design.
3. The system expands the design into logical runs.
4. Each logical run creates one or more run attempts.
5. Provider adapters execute attempts and store raw request and response payloads.
6. Deterministic evaluators run first.
7. Human review and LLM judge workflows add additional scores.
8. Results are aggregated into win rates, failure rates, cost-quality views, and context-sensitivity analytics.

## Provider Adapter Boundary

Provider options are not equivalent, so the app uses both normalized and raw config fields.

Normalized fields:

- provider
- model
- temperature
- max_output_tokens
- reasoning_level: none, low, medium, high
- supports_images
- supports_files
- supports_tools
- supports_json_schema

Raw provider fields:

- provider_params_json

Analysis surfaces both normalized and raw provider settings so comparisons do not hide non-equivalent knobs.

Provider execution is gated before outbound calls. Projects can set provider allow/deny lists, manifests can force `local_only`, and the executor records a per-run `data_egress_label` plus deterministic context-budget report before each attempt. MVP over-budget behavior is fail-fast rather than truncation.

Attempt records store the provider, model name, request payload, response payload, provider response ID, provider timestamp, provider metadata such as `system_fingerprint`, and the pricing snapshot used for that attempt.

## Audit Boundary

Audit logs record typed events for experiment creation, queueing, retries, cancellations, review decisions, exports, provider calls, provider blocks, and cache hits. Audit details are intentionally metadata-only and must not include raw prompts, artifacts, manifests, provider request payloads, response payloads, screenshots, warmer text, or model outputs.

## Artifact Pipeline

Artifacts need explicit preprocessing records because model inputs are often shaped by extraction decisions.

For PDFs, store original PDF, extracted text, page-level text, page images, tables, figures, citations, parser name, parser version, and extraction timestamp.

For images, store original image, normalized image, dimensions, relevant metadata, OCR text if used, annotations, and regions of interest.

Each run records exactly what the model saw: direct files, direct images, PDF text, page screenshots, OCR text, selected figures, extracted tables, retrieval chunks, paper cards, or mixed derived bundles.
