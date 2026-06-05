# Privacy, Reproducibility, And Safety

Phase 13 keeps provider execution local-first while making any provider egress explicit and auditable.

## Data Egress Controls

- Projects store `provider_allow_list` and `provider_deny_list`. Empty allow lists mean no project-specific allow restriction; deny lists always block matching providers.
- Manifest/API controls keep `local_only` as the default. CLI runs default to `--dry-run --local-only`.
- Each run stores `data_egress_label`, `context_report`, and `truncation_policy`.
- Context reports use a deterministic token estimator before provider calls. If `context_budget_tokens` or `max_context_tokens` is exceeded, the attempt fails before the adapter executes.
- The supported truncation policy is `fail_on_over_budget`; automatic truncation is deferred.

## API Key Storage

The current implementation does not store provider API keys in the database. Provider credentials stay in local environment variables such as `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`, with `MODEL_EVAL_LOCAL_ONLY=true` as the default safety switch.

Encrypted in-app key storage is deferred until the product needs persistent multi-user credentials. That future storage should use envelope encryption, per-workspace key scope, audit events for key creation/rotation/deletion, and secret redaction at every API/log boundary.

## Reproducibility Metadata

Experiments store immutable manifest, library-object, design, controls, and pricing snapshots. Each run attempt stores:

- provider and model name
- provider response ID and response timestamp
- request and response payload snapshots
- provider metadata such as `system_fingerprint` when available
- attempt-level pricing snapshot
- timing, token, cost, cache, retry, and failure metadata

`Run` remains the logical configuration. `RunAttempt` remains the concrete provider call, dry-run attempt, retry, or rerun record.

## Audit Logs

Audit logs are typed by event kind and entity. They cover experiment creation/updates, queueing, retries, cancellations, review decisions, exports, provider-call starts/successes/failures/cache hits, and provider/config blocks.

Audit details intentionally avoid raw prompt text, private artifacts, manifest payloads, model outputs, request payloads, and response payloads. Those private records stay in the immutable experiment/run/attempt snapshots where product access controls can apply.

OpenTelemetry trace export is opt-in and local-file oriented. The span builder emits stable IDs and parent links for experiments, runs, run attempts, deterministic evaluators, judge evaluators, human review records, artifact preprocessing runs, and export events, but it only allow-lists metadata such as IDs, statuses, timing fields, token/cost totals, provider names, parser names, and audit links. Trace attributes must not include raw prompts, artifacts, manifests, screenshots, warmer messages, request payloads, response payloads, credentials, model outputs, terminal failure details, score values, artifact filenames, local paths, checksums, OCR text, or screenshot-derived private metadata.

The CLI, API, and UI expose this trace as `otel-json`; each generated trace records an `export_generated` audit event containing only the requested format, not the trace payload.
