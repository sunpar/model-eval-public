# Results Analytics

Core result analytics are directional summaries for comparing model, prompt, and warmer behavior.
They should not be treated as calibrated model-quality scores.

Use rates with their counts:

- Win rate excludes ties and cannot-judge decisions from the win/loss denominator.
- Pass rate uses blind human review pass/fail scores when present.
- Failure-tag rate is the share of terminal attempts that received each blind-review tag at least once.
- Warmer lift compares a non-`none` warmer against the matching `none` baseline for the same case, model, and system prompt.
- Base context-sensitivity labels are transparent directional summaries from score spread and failure-tag spread. V2 divergence rows add stored deterministic, judge-backed, or human-backed evidence with explicit source labels.

The UI should favor win rates, pass rates, failure rates, failure tags, and qualitative labels over false precision.

## V2 Replicates And Uncertainty

Replicated attempts are reliability samples. Retry attempts are recovery events and are excluded from nondeterminism summaries so transient failures do not inflate confidence.

Phase 18 analytics add transparent intervals for pass rate, win rate, failure rate, cost, latency, and token totals. Rate intervals use count-based Wilson bounds; numeric intervals use sample means and variance. Labels intentionally stay coarse:

- `no_samples`: no eligible reliability samples.
- `single_sample`: one sample, so lower and upper bounds equal the observed value.
- `low_sample`: fewer than 30 samples.
- `stable_sample`: 30 or more samples.

These intervals are directional diagnostics for local comparison, not calibrated guarantees.

## V2 Cost-Quality Frontier

Phase 24 adds `cost_quality_frontier` rows to the analytics API and JSON exports. Each row is grouped by case, suite split, model config, system prompt, and warmer so cost, latency, and quality are compared only across comparable case/split slices.

Quality uses pass rate when pass/fail labels exist and falls back to win rate when only pairwise labels exist. Rows with missing quality, cost, or latency are kept in the payload with deterministic `dominance_status` values of `missing_quality`, `missing_cost`, or `missing_latency`. Complete rows are marked `frontier` unless another complete row in the same case and split has quality greater than or equal to it, cost less than or equal to it, and latency less than or equal to it, with at least one strict improvement; those rows are marked `dominated` and include `dominated_by`.

Frontier rows carry Phase 18 interval payloads for quality, cost, latency, and token totals. They also attach matching warmer-lift summaries, Phase 19 divergence/carryover summaries, Phase 15 judge-calibration overlays when present, and Promptfoo-compatible provider, prompt, test, and assertion fields for downstream export consumers.

The analytics endpoint and analytics-backed export formats accept the same deterministic filter keys that the frontier builder uses: `case_slug`, `suite_slug`, `suite_split`, `model_config_slug`, `system_prompt_slug`, `warmer_slug`, `evaluator_source`, and `reviewer_id`. Dimension filters remove attempts before aggregation; evaluator and reviewer filters limit the scores that contribute to quality and score-backed analytics. Judge-calibration overlays keep the judge side available when a reviewer filter selects the matching human labels.

## V2 LLM Judges

V2 LLM judge configs are stored as versioned library records and snapshotted into experiments as evaluator definitions. A judge snapshot includes the prompt, rubric dimensions, output schema, model-config reference, raw provider params, and calibration status that were current when the experiment was saved.

Judge outputs are not a replacement for human labels. Human review remains the source of truth for subjective calibration. Phase 15 calibration rows compare judge winner/loser and pass/fail decisions with human labels, include rubric-score coverage against human rubric notes, and report agreement, disagreement, and low-confidence counts.

Verbosity-bias rows use stored answer token counts from judge comparisons to show whether longer answers win disproportionately. Treat those rows as a bias signal, not a quality metric.

## V2 Deterministic Divergence Foundation

Phase 19 adds local divergence rows for comparing each non-`none` warmer against the matching no-warmer baseline for the same case, model, and system prompt. The first deterministic metrics are semantic lexical/keyphrase overlap, markdown section-structure changes, token-length deltas, and confidence-language markers. These scores are stored in the existing score table with `type: divergence` and payloads that include `metric_source`, `comparison_scope`, baseline/comparison attempt IDs, `value`, `label`, and `warning`.

The semantic row is explicitly labeled `deterministic_semantic_overlap` and should be treated as an uncalibrated heuristic, not semantic judging. The analytics response also exposes `divergence_metrics`, including stored divergence scores and a human-failure-tag spread row when blind review tags are available across warmers. Missing baselines, one-sided outputs, and absent text produce `unavailable` labels rather than crashing.

Phase 19 claim and conclusion divergence rows reuse existing Phase 15 judge scores when available. Stored judge rubric dimensions such as claim quality or conclusion support produce `llm_judge_rubric` rows without rerunning a judge. When judge-backed evidence is missing, analytics emits `deterministic_fallback` rows with explicit warnings so consumers do not mistake local lexical heuristics for calibrated semantic judging.

The `carryover_audit` rows classify each non-`none` warmer attempt as `reused`, `ignored`, `overfit`, or `unknown`. Structured judge output is preferred when an existing judge score includes carryover evidence. Otherwise the row falls back to local warmer/output term overlap and labels the source as `local_warmer_overlap`.

Phase 19C adds grouped `divergence_summary` and `carryover_summary` rows for the Results UI and exports. Each row is grouped by case, model, system prompt, warmer, signal, and source, and includes `sample_count`, `source_kind`, `warning`, and `warning_label`. Source kinds are intentionally explicit:

- `deterministic_heuristic` means a local text heuristic or overlap check, not calibrated semantic judging.
- `judge_backed` means stored LLM judge output was reused and still needs human-label calibration.
- `human_backed` means available blind review labels or tags contributed the row.

Markdown, CSV, and JSON exports use the same analytics rows as the API. CSV rows use `aggregate_divergence`, `aggregate_carryover`, and `aggregate_frontier` sections so downstream readers can keep detailed attempt scores separate from grouped analytics summaries.
