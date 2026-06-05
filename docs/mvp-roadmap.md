# Roadmap And Completion Status

This file is the high-level product roadmap. Detailed implementation evidence lives in
`docs/implementation-task-list.md`, `docs/v2-implementation-task-list.md`, `docs/v2-demo.md`,
and `FEATURE_INVENTORY.md`.

## V1

- [x] System prompt library.
- [x] Conversation warmer library.
- [x] Case library.
- [x] Artifact library.
- [x] Model config library.
- [x] Experiment builder.
- [x] Full-factorial run generation.
- [x] Python provider adapters for OpenAI and Anthropic.
- [x] Async run executor and retry/cancel controls.
- [x] Run and run-attempt snapshot storage.
- [x] Side-by-side comparison.
- [x] Blind pairwise review.
- [x] Manual pass/fail, notes, rubric notes, and failure tags.
- [x] Cost, token, and latency tracking.
- [x] Markdown, CSV, and JSON export.
- [x] CLI manifest runner.

## V1 Screens

Library:

- [x] Prompts.
- [x] Warmers.
- [x] Cases.
- [x] Artifacts.
- [x] Model configs.
- [x] Evaluators, judge configs, metric adapters, benchmark suites, reviewers, and taxonomies.

Experiment Builder:

- [x] Select dimensions.
- [x] Preview run count.
- [x] Preview cost range.
- [x] Save manifest and create/update draft experiments.

Run Monitor:

- [x] Pending, queued, running, failed, skipped, canceled, and complete states.
- [x] Retry.
- [x] Cancel.

Comparison Workspace:

- [x] Blind pairwise.
- [x] Side-by-side output view.
- [x] Metadata reveal.
- [x] Notes.
- [x] Failure tags and rubric notes.
- [x] Multi-reviewer assignment workflow.

Results:

- [x] Win rates and pass rates.
- [x] Score table.
- [x] Cost, latency, and token tables.
- [x] Context sensitivity and divergence tables.
- [x] Replicate uncertainty intervals.
- [x] Cost-quality frontier.
- [x] Judge calibration overlays.
- [x] Metric adapter score rows.
- [x] Export actions.

## V2

- [x] LLM judge builder.
- [x] Judge calibration against human labels.
- [x] Position-swapped pairwise judging and verbosity-bias controls.
- [x] Regression benchmark suites.
- [x] Dataset splits.
- [x] Replicated runs.
- [x] Confidence intervals and uncertainty display.
- [x] Promptfoo import/export.
- [x] OpenTelemetry trace export.
- [x] Metric adapter layer for RAG and DeepEval-style checks.
- [x] Artifact preprocessing pipeline.
- [x] Review queues.
- [x] Failure taxonomy builder.
- [x] Advanced context sensitivity, divergence, and carryover analytics.
- [x] Cost-quality frontier.
- [x] Local-only V2 demo and readiness audit.

## V3

- [ ] Production trace ingestion.
- [ ] Active sampling for review.
- [ ] Synthetic case generation from failure taxonomy.
- [ ] Team review workflow beyond local reviewer identities and assignments.
- [ ] Evaluator CI gates.
- [ ] Prompt deployment and rollback.
- [ ] Model release comparison dashboard.
- [ ] Custom provider SDK.
- [ ] Local model support through Ollama or vLLM.
- [ ] Scheduled benchmark reruns for provider drift monitoring.
