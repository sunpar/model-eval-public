# Data Model

The key modeling choice is to separate Case, Experiment, Run, and RunAttempt.

## Core Hierarchy

Workspace

Project

- Cases
- Artifacts
- System Prompts
- Conversation Warmers
- Model Configs
- Evaluators
- Provider allow/deny lists
- Experiments
- Runs
- Run Attempts
- Scores
- Review Sets
- Audit Logs

## Case

A case is the thing being tested:

- Analyze this screenshot.
- Summarize this paper.
- Write the Chilean copper investment memo.
- Review this code diff.
- Extract claims from this article.

Cases can reference artifacts and ground-truth cards.

## Conversation Warmer

A warmer is a structured, versioned conversation history.

```json
{
  "name": "Copper - Expert User",
  "domain": "commodities",
  "user_level": "expert",
  "intent": "prime model for deep market-structure analysis",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "tags": ["copper", "commodities", "expert", "investment-memo"],
  "version": 3
}
```

## Experiment

An experiment is the study design:

- Compare 3 models, 2 warmers, and 2 system prompts on 20 cases.
- Run one-factor-at-a-time prompt sensitivity tests.
- Run replicated samples to measure nondeterminism.
- Rerun a benchmark suite after a model release.

Implemented design support is centered on full-factorial expansion with explicit dimensions,
suite references, split filters, reliability replicates, and retry metadata. The roadmap keeps
other design families as future extensions:

- Implemented: full factorial.
- Implemented: replicated attempts through `design.replicates`.
- Implemented: benchmark suite reruns through suite references and split filters.
- Future: one-factor-at-a-time.
- Future: paired comparison.
- Future: fractional factorial.

## Run

A run is one logical configuration on one case.

Example:

- case: Chile memo.
- model: Claude.
- reasoning: high.
- system prompt: expert analyst v4.
- warmer: expert user copper v2.

Privacy and reproducibility fields:

- data_egress_label.
- context_report with estimated tokens and included/dropped message metadata.
- truncation_policy, with `fail_on_over_budget` as the MVP policy.

## RunAttempt

A run attempt is one actual provider call. Retries, nondeterministic reruns, failed calls, and replicated samples should never overwrite the logical run.

Each attempt stores provider, model, request payload, response payload, provider response ID, provider timestamp, pricing snapshot, provider metadata, system fingerprint when available, token usage, cost, latency, cache state, and failure metadata.

## Score

Scores are multi-type, not one master number.

Fields:

- type: pass_fail, numeric, categorical, pairwise, ranking, note, placeholder, divergence, metric_adapter, rubric_score.
- evaluator_type: human, code, llm_judge, metric_adapter.
- criterion.
- value.
- explanation.
- confidence.
- evaluator_version.
- created_at.

Subjective comparison prioritizes pairwise preference, binary pass/fail, failure tags, and rubric dimensions before aggregated numeric scores.

## Artifact Preprocessing

Artifact preprocessing records link a source artifact to local derived artifacts
without committing source files or generated previews to git. PDF text
preprocessing stores a completed run with parser name/version, the source
checksum, the derived text artifact ID, and stable per-page metadata:
`page_number`, `char_count`, and `checksum_sha256`.

PDF visual preprocessing stores one derived PNG artifact per rendered page with
`page_number`, image dimensions, checksum, and derived artifact ID. Direct image
preprocessing stores a normalized PNG artifact with original and normalized
dimensions plus checksums. Optional OCR metadata is recorded as either captured
text statistics and a derived OCR text artifact ID, or deterministic
`ocr_unavailable` metadata when no local OCR command is configured.
This path uses local Pillow and PyMuPDF dependencies because the Python standard
library cannot render PDF pages or decode and re-encode common image formats.

Selected figure and table preprocessing records preserve source artifact ID,
page number, region bounds, parser name/version, source checksum, and derived
artifact ID. Selected figures are stored as local PNG artifacts. Extracted
tables are stored as local JSON artifacts with structured metadata for columns,
row count, and table checksum. Invalid pages or regions are recorded as failed
preprocessing runs before any derived preview is written.

Retrieval chunk preprocessing stores one local JSON artifact per chunk with
chunk text, source offsets, parser name/version, source checksum, chunk checksum,
and derived artifact ID. Paper card preprocessing stores a deterministic local
JSON summary artifact with citation metadata, section offsets and checksums,
source checksum, parser name/version, summary checksum, and derived artifact ID.
Both paths require local source text and record failed preprocessing runs when
the source text is missing or empty.

Run model input snapshots record the concrete artifact input mode for direct
files, direct images, extracted PDF text, page screenshots, OCR text, selected
figures, tables, retrieval chunks, paper cards, or a mixed derived bundle.
Derived artifact inputs include source checksum, parser name/version, and
derived artifact ID. Mixed derived bundles include a stable checksum computed
from input mode, selected derived artifact IDs, source checksums, and parser
versions. Direct-plus-derived mixed inputs are rejected before provider
execution.

Empty, encrypted, or unreadable PDFs are recorded as failed preprocessing runs
with metadata-only diagnostics. Error metadata must not include local paths,
storage URIs, raw PDF contents, provider secrets, or private source payloads.

The preprocessing API can start a parser for a library artifact, list
preprocessing runs for that source, list derived artifact references, and update
an artifact's selected input mode for experiment manifests. Source artifact
selections that request a derived mode resolve to the latest completed derived
artifact for that mode; if preprocessing has not produced one, experiment
creation fails before provider execution instead of recording an unbound derived
mode. API and UI previews return local-storage availability as metadata only:
derived artifact responses include IDs, slugs, parser metadata, checksums,
dimensions, counts, and offsets, but omit local storage URIs and raw derived text
by default.

## Audit Log

Audit logs are queryable event records for experiment creation and updates, execution queueing, provider-call starts/successes/failures/cache hits, provider blocks, retry requests, cancellations, review decisions, and exports.

Audit details must stay metadata-only. Raw prompts, artifacts, manifest content, request payloads, response payloads, screenshots, warmer text, model outputs, and provider credentials belong in controlled snapshots or local environment configuration, not in audit details.
