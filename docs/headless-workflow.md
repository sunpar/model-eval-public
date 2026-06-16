# Headless Workflow

The `evalbench` CLI supports local-first experiment workflows without the UI.

Commands:

- `evalbench run <manifest> --dry-run --local-only` validates, persists, and executes the manifest without live provider calls.
- `evalbench compare <experiment> --baseline <experiment>` compares stored aggregate summaries.
- `evalbench review <experiment> --blind` emits a blind review queue with answer labels and text only.
- `evalbench score <experiment> --evaluator <id>` runs one deterministic evaluator against succeeded non-dry-run attempts.
- `evalbench judge <experiment> --judge <id> --dry-run --local-only` runs an LLM judge evaluator against stored attempts without live provider calls.
- `evalbench demo v2 --export-dir <path>` builds the complete local-only V2 copper demo, including benchmark suite setup, preprocessing records, synthetic execution, blind review, judge calibration rows, analytics, and Markdown/CSV/JSON exports.
- `evalbench import promptfoo <file> --preview-only` converts a Promptfoo YAML/JSON config into a Model Eval manifest preview without executing providers or requiring provider keys.
- `evalbench import promptfoo <file> --persist --project <slug>` also writes mapped cases, system prompts, providers, deterministic evaluators, metric adapter configs, and the default `none` warmer as new project-scoped library versions.
- `evalbench export <experiment> --format markdown|csv|json|promptfoo|otel-json` emits reproducible experiment exports. Analytics sections can be filtered with `--case`, `--suite`, `--suite-split`, `--model-config`, `--system-prompt`, `--warmer`, `--evaluator-source`, and `--reviewer`.

Exports use stable ordering. JSON includes manifest, library snapshots, run snapshots, request and response payloads, scores, review records, and aggregate analytics. CSV uses one stable header across run, attempt, score, review, and aggregate rows. Markdown is a readable summary with configs, scores, costs, failure tags, and key examples.

Promptfoo import preserves raw provider params on generated model configs and returns warnings for unsupported fields or ambiguous prompt shapes instead of silently dropping them. Supported assertion mappings create local deterministic evaluator configs for `not-empty` and `is-json`, and local metric adapter configs for `answer-relevance`; unsupported assertion types are reported in the preview warnings.

Promptfoo export maps compatible model configs, system prompts, cases, deterministic `not-empty` and `is-json` evaluators, prompt variables, and `max_parallel_requests` concurrency controls. Conversation warmers, reliability replicates, randomized run ordering, unsupported evaluator kinds, unsupported controls, and system prompts without raw prompt/messages are reported as warnings in CLI/API/UI surfaces instead of being silently represented as lossless mappings.

OpenTelemetry JSON export uses the metadata-only span builder and is intended for local file inspection. The trace contains stable experiment, run, attempt, evaluator, review, artifact preprocessing, and export-event spans with IDs, parent links, statuses, timings, token/cost totals, provider names, parser names, and audit links only. It excludes raw prompts, manifests, artifacts, warmer messages, request and response payloads, credentials, model outputs, terminal failure details, score values, filenames, checksums, local paths, OCR text, and screenshot-derived private metadata.

Blind review queue exports omit answer metadata such as run attempt IDs, model config slugs, system prompt slugs, warmer slugs, costs, and reveal metadata.

LLM judge dry-runs build blind pairwise prompts from answer text only, store original and position-swapped decisions, and persist pairwise, pass/fail, and rubric scores with the frozen judge config version. Non-dry-run judge execution must pass the same local-only, provider allow/deny, cost-cap, and context-budget gates as model execution.
