import {
  ArtifactRecord,
  BenchmarkSuiteRecord,
  CaseRecord,
  EvaluatorRecord,
  ExperimentManifest,
  LLMJudgeConfigRecord,
  LibraryKind,
  MetricAdapterConfigRecord,
  ModelConfigRecord,
  SystemPromptRecord,
  WarmerRecord,
  parseJsonObject,
} from "./experimentBuilder";

export interface ApiExperimentResponse {
  id: number;
  project_slug?: string;
  slug: string;
  name: string;
  status: string;
  preview?: {
    logical_runs: number;
    run_attempts: number;
    estimated_token_count: number;
    estimated_cost_usd: number;
  };
}

export interface ApiDerivedArtifact {
  id: number | string;
  slug: string;
  name: string;
  artifact_type?: string | null;
  input_mode: string;
  filename?: string | null;
  checksum_sha256?: string | null;
  size_bytes?: number | null;
  mime_type?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  metadata: Record<string, unknown>;
  local_storage?: {
    available: boolean;
    reference: string | null;
  };
}

export interface ApiArtifactPreprocessingRun {
  id: number;
  source_artifact_id: number;
  source_artifact?: Record<string, unknown>;
  parser_name: string;
  parser_version: string;
  status: string;
  error_kind?: string | null;
  error_message?: string | null;
  error_metadata?: Record<string, unknown>;
  derived_artifact_ids: number[];
  derived_artifacts: ApiDerivedArtifact[];
}

export interface ArtifactPreprocessingOptions extends Record<string, unknown> {
  parserVersion?: string;
  pageNumber?: number;
  region?: Record<string, unknown>;
  table?: Record<string, unknown>;
  chunks?: Array<Record<string, unknown>>;
  citation?: Record<string, unknown>;
  sections?: Array<Record<string, unknown>>;
}

export interface ApiMonitorExperiment {
  id: number;
  project_slug?: string;
  slug: string;
  name: string;
  status: string;
  created_at?: string | null;
}

export interface ApiMonitorRun {
  id: number;
  run_id: string;
  experiment_id: number;
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  status: string;
}

export interface ApiRunAttempt {
  id: number;
  run_id: number;
  attempt_id: string;
  replicate_index: number;
  attempt_number: number;
  parent_attempt_id: string | null;
  status: string;
  error_kind: string | null;
  error_message: string | null;
  terminal_failure_reason: string | null;
  provider_response_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
  request_payload: Record<string, unknown>;
  response_payload: Record<string, unknown>;
  cache_key: string | null;
  cache_hit: boolean;
}

export interface ApiReviewAnswer {
  label: string;
  run_attempt_id?: number;
  text: string;
}

export interface ApiReviewMetadataAnswer {
  label: string;
  run_attempt_id?: number;
  model_config_slug?: string;
  system_prompt_slug?: string;
  warmer_slug?: string;
  case_slug?: string;
  cost_usd?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
}

export interface ApiReviewItem {
  assignment_id?: number;
  assignment_status?: string;
  id: number;
  review_set_id?: number;
  item_key: string;
  prompt: Record<string, unknown>;
  answers: ApiReviewAnswer[];
  reviewer_decision: Record<string, unknown>;
  reveal_metadata?: {
    answers?: ApiReviewMetadataAnswer[];
  };
}

export interface ApiReviewSet {
  id: number;
  slug: string;
  name: string;
  review_type: string;
  metadata: {
    failure_tags?: string[];
    failure_taxonomy?: {
      slug: string;
      name: string;
      version: number;
      tags: string[];
    };
  };
  assignment_progress?: {
    assigned: number;
    submitted: number;
    pending: number;
  };
  items: ApiReviewItem[];
}

export interface ApiReviewer {
  id: number;
  slug: string;
  name: string;
  email?: string | null;
}

export interface ApiReviewerQueue {
  review_set: {
    id: number;
    slug: string;
    name: string;
    review_type: string;
  };
  reviewer: ApiReviewer;
  failure_taxonomy: {
    slug?: string;
    name?: string;
    version?: number;
    tags?: string[];
  };
  progress: {
    assigned: number;
    submitted: number;
    pending: number;
  };
  items: ApiReviewItem[];
}

export interface ApiReviewAssignment {
  id: number;
  review_set_id: number;
  review_item_id: number;
  status: string;
  reviewer: ApiReviewer;
}

export interface ApiReviewDecision {
  reviewer_id?: string;
  winner: "A" | "B" | "tie" | "cannot_judge";
  pass_fail: Record<string, boolean>;
  failure_tags: Record<string, string[]>;
  rubric_notes: Record<string, string>;
  notes?: string;
  confidence?: number;
}

export interface ApiTokenTotals {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface ApiRateInterval {
  sample_count: number;
  rate: number | null;
  lower: number | null;
  upper: number | null;
  label: string;
}

export interface ApiNumericInterval {
  sample_count: number;
  mean: number | null;
  variance: number | null;
  lower: number | null;
  upper: number | null;
  label: string;
}

export interface ApiResultsSummary {
  attempt_count: number;
  failed_attempt_count: number;
  failure_rate: number | null;
  failure_rate_interval?: ApiRateInterval;
  winner_count: number;
  loser_count: number;
  tie_count: number;
  cannot_judge_count: number;
  win_rate: number | null;
  win_rate_interval?: ApiRateInterval;
  pass_count: number;
  fail_count: number;
  pass_rate: number | null;
  pass_rate_interval?: ApiRateInterval;
  average_cost_usd: number | null;
  cost_usd_interval?: ApiNumericInterval;
  average_latency_ms: number | null;
  latency_ms_interval?: ApiNumericInterval;
  token_totals: ApiTokenTotals;
  total_tokens_interval?: ApiNumericInterval;
}

export interface ApiFailureTagFrequency {
  tag: string;
  count: number;
  rate: number | null;
}

export interface ApiWarmerLiftRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  metric: string | null;
  baseline_warmer_slug: string;
  baseline_missing: boolean;
  baseline_rate: number | null;
  warmer_rate: number | null;
  lift: number | null;
}

export interface ApiAnalyticsFilters {
  case_slug: string | null;
  suite_slug: string | null;
  suite_split: string | null;
  model_config_slug: string | null;
  system_prompt_slug: string | null;
  warmer_slug: string | null;
  evaluator_source: string | null;
  reviewer_id: string | null;
}

export interface ApiContextSensitivityRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_count: number;
  scored_warmer_count: number;
  metric: string | null;
  best_warmer_slug: string | null;
  worst_warmer_slug: string | null;
  score_spread: number | null;
  label: string;
}

export interface ApiDivergencePlaceholderRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  score_spread: number | null;
  failure_tag_spread: boolean;
  signals: string[];
  label: string;
  semantic_diff_available: boolean;
}

export type ApiAnalyticsSourceKind =
  | "deterministic_heuristic"
  | "judge_backed"
  | "human_backed"
  | "unknown";

export interface ApiDivergenceMetricRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  criterion: string;
  metric_source: string;
  source_kind: ApiAnalyticsSourceKind;
  comparison_scope: string;
  baseline_attempt_id: string | null;
  comparison_attempt_id: string;
  value: number | null;
  label: string;
  warning: string | null;
  warning_label: string;
  sample_count: number;
  confidence: number | null;
  explanation: string | null;
  details: Record<string, unknown>;
}

export interface ApiDivergenceSummaryRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  criterion: string;
  metric_source: string;
  source_kind: ApiAnalyticsSourceKind;
  value: number | null;
  label: string;
  warning: string | null;
  warning_label: string;
  sample_count: number;
  confidence: number | null;
}

export interface ApiCarryoverAuditRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  comparison_attempt_id: string;
  source_evidence: string;
  source_kind: ApiAnalyticsSourceKind;
  status: string;
  explanation: string;
  warning: string | null;
  warning_label: string;
  sample_count: number;
  details: Record<string, unknown>;
  confidence: number | null;
}

export interface ApiCarryoverSummaryRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  source_evidence: string;
  source_kind: ApiAnalyticsSourceKind;
  status: string;
  warning: string | null;
  warning_label: string;
  sample_count: number;
  confidence: number | null;
}

export interface ApiQualityTableRow {
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  attempt_count: number;
  win_rate: number | null;
  pass_rate: number | null;
  failure_rate: number | null;
  average_cost_usd: number | null;
  average_latency_ms: number | null;
  token_totals: ApiTokenTotals;
  quality_metric: string | null;
  quality_rate: number | null;
  cost_usd_per_quality_point?: number | null;
}

export interface ApiFrontierCalibrationOverlay {
  evaluator_id: string;
  comparison_count: number;
  agreement_rate: number | null;
  low_confidence_count: number;
}

export interface ApiCostQualityFrontierRow {
  frontier_key: string;
  case_slug: string;
  suite_slug: string;
  suite_split: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  attempt_count: number;
  failed_attempt_count: number;
  quality_metric: string | null;
  quality_rate: number | null;
  quality_interval: ApiRateInterval;
  quality_uncertainty_label: string;
  average_cost_usd: number | null;
  cost_usd_interval: ApiNumericInterval;
  cost_uncertainty_label: string;
  average_latency_ms: number | null;
  latency_ms_interval: ApiNumericInterval;
  latency_uncertainty_label: string;
  token_totals: ApiTokenTotals;
  total_tokens_interval: ApiNumericInterval;
  warmer_lift: ApiWarmerLiftRow | null;
  divergence_summary: ApiDivergenceSummaryRow[];
  carryover_summary: ApiCarryoverSummaryRow[];
  judge_calibration_overlays: ApiFrontierCalibrationOverlay[];
  dominated_by: string | null;
  is_frontier: boolean;
  dominance_status: string;
  promptfoo_provider_id: string | null;
  promptfoo_prompt_id: string | null;
  promptfoo_test_description: string | null;
  promptfoo_assertion_types: string[];
}

export interface ApiFailureRateRow {
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  attempt_count: number;
  failed_attempt_count: number;
  failure_rate: number | null;
}

export interface ApiNondeterminismRow {
  sample_count: number;
  retry_attempt_count: number;
  failure_rate_interval: ApiRateInterval;
  pass_rate_interval: ApiRateInterval;
  win_rate_interval: ApiRateInterval;
  cost_usd_interval: ApiNumericInterval;
  latency_ms_interval: ApiNumericInterval;
  total_tokens_interval: ApiNumericInterval;
  [key: string]:
    | string
    | number
    | ApiRateInterval
    | ApiNumericInterval
    | null;
}

export interface ApiDimensionFailureRow {
  attempt_count: number;
  failed_attempt_count: number;
  failure_rate: number | null;
  [key: string]: string | number | null;
}

export interface ApiJudgeCalibrationRow {
  evaluator_id: string;
  comparison_count: number;
  pairwise_comparison_count: number;
  pairwise_agreement_count: number;
  pass_fail_comparison_count: number;
  pass_fail_agreement_count: number;
  rubric_comparison_count: number;
  rubric_agreement_count: number;
  agreement_count: number;
  disagreement_count: number;
  agreement_rate: number | null;
  low_confidence_count: number;
}

export interface ApiJudgeVerbosityBiasRow {
  evaluator_id: string;
  comparison_count: number;
  length_comparable_count: number;
  longer_answer_win_count: number;
  longer_answer_win_rate: number | null;
  winner_average_tokens: number | null;
  loser_average_tokens: number | null;
}

export interface ApiReviewerCoverageRow {
  review_set_id: number;
  assigned_count: number;
  submitted_count: number;
  pending_count: number;
  reviewer_count: number;
  coverage_rate: number | null;
}

export interface ApiReviewerDisagreementRow {
  review_item_id: number;
  review_set_id: number;
  reviewer_count: number;
  pairwise_disagreement: boolean;
  pass_fail_disagreement_count: number;
  failure_tag_disagreement_count: number;
}

export interface ApiFailureTaxonomyRollupRow {
  tag: string;
  taxonomy_version: number | null;
  count: number;
}

export interface ApiMetricAdapterScoreRow {
  attempt_id: string;
  case_slug: string;
  model_config_slug: string;
  system_prompt_slug: string;
  warmer_slug: string;
  adapter_config_slug: string;
  adapter_config_version: number | null;
  criterion: string;
  metric_source: string;
  source_kind: ApiAnalyticsSourceKind;
  score: number | null;
  label: string | null;
  explanation: string | null;
  confidence: number | null;
}

export interface ApiResultsAnalytics {
  experiment_id: number;
  filters: ApiAnalyticsFilters;
  summary: ApiResultsSummary;
  failure_tag_frequency: ApiFailureTagFrequency[];
  warmer_lift: ApiWarmerLiftRow[];
  context_sensitivity: ApiContextSensitivityRow[];
  divergence_placeholders: ApiDivergencePlaceholderRow[];
  divergence_metrics: ApiDivergenceMetricRow[];
  divergence_summary: ApiDivergenceSummaryRow[];
  carryover_audit: ApiCarryoverAuditRow[];
  carryover_summary: ApiCarryoverSummaryRow[];
  cost_quality_frontier: ApiCostQualityFrontierRow[];
  cost_quality_table: ApiQualityTableRow[];
  latency_quality_table: ApiQualityTableRow[];
  failure_rate_table: ApiFailureRateRow[];
  failure_rate_by_dimension: Record<string, ApiDimensionFailureRow[]>;
  nondeterminism_by_dimension: Record<string, ApiNondeterminismRow[]>;
  judge_calibration: ApiJudgeCalibrationRow[];
  judge_verbosity_bias: ApiJudgeVerbosityBiasRow[];
  reviewer_coverage: ApiReviewerCoverageRow[];
  reviewer_disagreement: ApiReviewerDisagreementRow[];
  failure_taxonomy_rollup: ApiFailureTaxonomyRollupRow[];
  metric_adapter_scores: ApiMetricAdapterScoreRow[];
}

export interface ApiMetricAdapterRunSummary {
  status?: string;
  dry_run?: boolean;
  planned_scores?: number;
  scores_recorded?: number;
  skipped?: Array<{
    attempt_id?: string;
    adapter_config_id?: string;
    reason: string;
    missing_inputs?: string[];
  }>;
}

export interface ApiPromptfooWarning {
  code: string;
  path?: string | null;
  message: string;
}

export type ApiExperimentExportFormat = "markdown" | "csv" | "json" | "promptfoo" | "otel-json";

export interface ApiExperimentExportResponse {
  format: ApiExperimentExportFormat | string;
  content: string;
  warnings: ApiPromptfooWarning[];
}

export interface ApiPromptfooImportPreviewResponse {
  manifest?: Record<string, unknown>;
  preview?: {
    logical_runs?: number;
    run_attempts?: number;
    estimated_token_count?: number;
    estimated_cost_usd?: number;
  };
  warnings: ApiPromptfooWarning[];
  library_records?: Record<string, unknown>;
  persisted?: {
    project_slug?: string;
    created?: Record<string, number>;
  };
}

const DEFAULT_PROJECT = "default";
const API_BASE_URL =
  (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env
    ?.VITE_MODEL_EVAL_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function createLibraryRecord(
  kind: LibraryKind,
  record:
    | CaseRecord
    | ArtifactRecord
    | SystemPromptRecord
    | WarmerRecord
    | ModelConfigRecord
    | EvaluatorRecord
    | LLMJudgeConfigRecord
    | MetricAdapterConfigRecord
    | BenchmarkSuiteRecord,
): Promise<void> {
  await apiRequest(libraryPath(kind), {
    method: "POST",
    body: JSON.stringify(libraryPayload(kind, record)),
  });
}

export async function startArtifactPreprocessing(
  artifactSlug: string,
  parserName: string,
  options: ArtifactPreprocessingOptions = {},
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiArtifactPreprocessingRun> {
  const { parserVersion, pageNumber, ...rawPayload } = options;
  const payload: Record<string, unknown> = { ...rawPayload, parser_name: parserName };
  if (parserVersion) payload.parser_version = parserVersion;
  if (pageNumber !== undefined) payload.page_number = pageNumber;
  if (options.region) payload.region = options.region;
  if (options.table) payload.table = options.table;
  if (options.chunks) payload.chunks = options.chunks;
  if (options.citation) payload.citation = options.citation;
  if (options.sections) payload.sections = options.sections;
  return apiRequest<ApiArtifactPreprocessingRun>(
    `/projects/${projectSlug}/library/artifacts/${encodeURIComponent(
      artifactSlug,
    )}/preprocessing-runs`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function updateArtifactInputMode(
  artifactSlug: string,
  inputMode: string,
  projectSlug = DEFAULT_PROJECT,
): Promise<{ input_mode: string }> {
  return apiRequest<{ input_mode: string }>(
    `/projects/${projectSlug}/library/artifacts/${encodeURIComponent(artifactSlug)}/input-mode`,
    {
      method: "PATCH",
      body: JSON.stringify({ input_mode: inputMode }),
    },
  );
}

export async function listArtifactPreprocessingRuns(
  artifactSlug: string,
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiArtifactPreprocessingRun[]> {
  return apiRequest<ApiArtifactPreprocessingRun[]>(
    `/projects/${projectSlug}/library/artifacts/${encodeURIComponent(
      artifactSlug,
    )}/preprocessing-runs`,
    { method: "GET" },
  );
}

export async function listArtifactDerivedArtifacts(
  artifactSlug: string,
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiDerivedArtifact[]> {
  return apiRequest<ApiDerivedArtifact[]>(
    `/projects/${projectSlug}/library/artifacts/${encodeURIComponent(
      artifactSlug,
    )}/derived-artifacts`,
    { method: "GET" },
  );
}

export async function createExperimentDraft(
  manifest: ExperimentManifest,
): Promise<ApiExperimentResponse> {
  return apiRequest<ApiExperimentResponse>(`/projects/${DEFAULT_PROJECT}/experiments/drafts`, {
    method: "POST",
    body: JSON.stringify(manifest),
  });
}

export async function updateExperimentDraft(
  experimentId: number,
  manifest: ExperimentManifest,
): Promise<ApiExperimentResponse> {
  return apiRequest<ApiExperimentResponse>(
    `/projects/${DEFAULT_PROJECT}/experiments/${experimentId}/draft`,
    {
      method: "PUT",
      body: JSON.stringify(manifest),
    },
  );
}

export async function queueExperiment(experimentId: number): Promise<ApiExperimentResponse> {
  return apiRequest<ApiExperimentResponse>(
    `/projects/${DEFAULT_PROJECT}/experiments/${experimentId}/queue`,
    { method: "POST" },
  );
}

export async function listMonitorExperiments(): Promise<ApiMonitorExperiment[]> {
  return apiRequest<ApiMonitorExperiment[]>("/monitor/experiments", { method: "GET" });
}

export async function listMonitorRuns(experimentId: number): Promise<ApiMonitorRun[]> {
  return apiRequest<ApiMonitorRun[]>(`/monitor/experiments/${experimentId}/runs`, {
    method: "GET",
  });
}

export async function listRunAttempts(runId: number): Promise<ApiRunAttempt[]> {
  return apiRequest<ApiRunAttempt[]>(`/monitor/runs/${runId}/attempts`, { method: "GET" });
}

export async function retryFailedRun(runId: number): Promise<ApiRunAttempt> {
  return apiRequest<ApiRunAttempt>(`/monitor/runs/${runId}/retry`, { method: "POST" });
}

export async function cancelMonitorExperiment(experimentId: number): Promise<ApiMonitorExperiment> {
  return apiRequest<ApiMonitorExperiment>(`/monitor/experiments/${experimentId}/cancel`, {
    method: "POST",
  });
}

export async function createReviewSetFromExperiment(
  experimentId: number,
  payload: {
    slug: string;
    name: string;
    random_seed?: number;
    reviewer_slugs?: string[];
    failure_taxonomy_slug?: string;
  },
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiReviewSet> {
  return apiRequest<ApiReviewSet>(
    `/projects/${projectSlug}/experiments/${experimentId}/review-sets`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function createReviewer(
  payload: { slug: string; name: string; email?: string },
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiReviewer> {
  return apiRequest<ApiReviewer>(`/projects/${projectSlug}/reviewers`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getReviewerQueue(
  reviewSetId: number,
  reviewerSlug: string,
): Promise<ApiReviewerQueue> {
  return apiRequest<ApiReviewerQueue>(
    `/review-sets/${reviewSetId}/reviewers/${encodeURIComponent(reviewerSlug)}/queue`,
    { method: "GET" },
  );
}

export async function createReviewAssignments(
  reviewSetId: number,
  reviewerSlugs: string[],
): Promise<{
  review_set_id: number;
  assignment_progress: { assigned: number; submitted: number; pending: number };
  assignments: ApiReviewAssignment[];
}> {
  return apiRequest(`/review-sets/${reviewSetId}/assignments`, {
    method: "POST",
    body: JSON.stringify({ reviewer_slugs: reviewerSlugs }),
  });
}

export async function getReviewSetForExperiment(
  experimentId: number,
  slug: string,
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiReviewSet | null> {
  const reviewSets = await apiRequest<ApiReviewSet[]>(
    `/projects/${projectSlug}/experiments/${experimentId}/review-sets?slug=${encodeURIComponent(
      slug,
    )}`,
    { method: "GET" },
  );
  return reviewSets[0] ?? null;
}

export async function getReviewSet(
  reviewSetId: number,
  options: { revealMetadata?: boolean } = {},
): Promise<ApiReviewSet> {
  const reveal = options.revealMetadata ? "?reveal_metadata=true" : "";
  return apiRequest<ApiReviewSet>(`/review-sets/${reviewSetId}${reveal}`, { method: "GET" });
}

export async function submitReviewDecision(
  reviewItemId: number,
  decision: ApiReviewDecision,
): Promise<ApiReviewItem> {
  return apiRequest<ApiReviewItem>(`/review-items/${reviewItemId}/decision`, {
    method: "POST",
    body: JSON.stringify(decision),
  });
}

export async function submitReviewAssignmentDecision(
  assignmentId: number,
  decision: ApiReviewDecision,
): Promise<ApiReviewAssignment> {
  return apiRequest<ApiReviewAssignment>(`/review-assignments/${assignmentId}/decision`, {
    method: "POST",
    body: JSON.stringify(decision),
  });
}

function analyticsFilterParams(filters: Partial<ApiAnalyticsFilters>): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return params;
}

export async function getExperimentAnalytics(
  experimentId: number,
  filters: Partial<ApiAnalyticsFilters> = {},
): Promise<ApiResultsAnalytics> {
  const params = analyticsFilterParams(filters);
  const query = params.toString();
  return apiRequest<ApiResultsAnalytics>(
    `/monitor/experiments/${experimentId}/analytics${query ? `?${query}` : ""}`,
    {
      method: "GET",
    },
  );
}

export async function exportExperiment(
  experimentId: number,
  format: ApiExperimentExportFormat,
  filters: Partial<ApiAnalyticsFilters> = {},
): Promise<ApiExperimentExportResponse> {
  const params = analyticsFilterParams(filters);
  params.set("format", format);
  return apiRequest<ApiExperimentExportResponse>(
    `/monitor/experiments/${experimentId}/exports?${params.toString()}`,
    { method: "GET" },
  );
}

export async function previewPromptfooImport(
  content: string,
  persist = false,
  projectSlug = DEFAULT_PROJECT,
): Promise<ApiPromptfooImportPreviewResponse> {
  return apiRequest<ApiPromptfooImportPreviewResponse>(
    `/projects/${projectSlug}/imports/promptfoo/preview`,
    {
      method: "POST",
      body: JSON.stringify({ content, persist }),
    },
  );
}

export async function runExperimentJudge(
  experimentId: number,
  evaluatorId: string,
): Promise<{
  status: string;
  scores_recorded: number;
}> {
  const encodedEvaluatorId = encodeURIComponent(evaluatorId);
  return apiRequest(`/monitor/experiments/${experimentId}/judges/${encodedEvaluatorId}/run`, {
    method: "POST",
    body: JSON.stringify({ dry_run: true, local_only: true, position_swap: true }),
  });
}

export async function runExperimentMetricAdapters(
  experimentId: number,
  payload: {
    adapterConfigSlug?: string;
    adapterConfigVersion?: number;
    dryRun?: boolean;
    localOnly?: boolean;
    force?: boolean;
  },
): Promise<ApiMetricAdapterRunSummary> {
  return apiRequest<ApiMetricAdapterRunSummary>(
    `/monitor/experiments/${experimentId}/metric-adapters/run`,
    {
      method: "POST",
      body: JSON.stringify({
        adapter_config_slug: payload.adapterConfigSlug,
        adapter_config_version: payload.adapterConfigVersion,
        dry_run: payload.dryRun ?? false,
        local_only: payload.localOnly ?? true,
        force: payload.force ?? false,
      }),
    },
  );
}

export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

async function apiRequest<T = unknown>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(await responseMessage(response), response.status);
  }
  return response.json() as Promise<T>;
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (Array.isArray(payload.detail)) {
      return payload.detail.join(" ");
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    return `Request failed with status ${response.status}.`;
  }
  return `Request failed with status ${response.status}.`;
}

function libraryPath(kind: LibraryKind): string {
  const pathByKind: Record<LibraryKind, string> = {
    cases: "cases",
    artifacts: "artifacts",
    systemPrompts: "system-prompts",
    warmers: "warmers",
    modelConfigs: "model-configs",
    evaluators: "evaluators",
    llmJudgeConfigs: "llm-judge-configs",
    metricAdapterConfigs: "metric-adapter-configs",
    benchmarkSuites: "benchmark-suites",
  };
  return `/projects/${DEFAULT_PROJECT}/library/${pathByKind[kind]}`;
}

function libraryPayload(
  kind: LibraryKind,
  record:
    | CaseRecord
    | ArtifactRecord
    | SystemPromptRecord
    | WarmerRecord
    | ModelConfigRecord
    | EvaluatorRecord
    | LLMJudgeConfigRecord
    | MetricAdapterConfigRecord
    | BenchmarkSuiteRecord,
): object {
  if (kind === "cases") {
    const item = record as CaseRecord;
    return {
      slug: item.id,
      name: item.name,
      prompt: item.prompt,
      dataset_split: item.datasetSplit,
      version: item.version,
    };
  }
  if (kind === "artifacts") {
    const item = record as ArtifactRecord;
    return {
      slug: item.id,
      name: item.name,
      artifact_type: item.artifactType,
      uri: item.uri,
      input_mode: item.inputMode,
      metadata: parseJsonObject(item.metadataJson),
      version: item.version,
    };
  }
  if (kind === "systemPrompts") {
    const item = record as SystemPromptRecord;
    return {
      slug: item.id,
      name: item.name,
      prompt: item.prompt,
      messages: [],
      version: item.version,
    };
  }
  if (kind === "warmers") {
    const item = record as WarmerRecord;
    return {
      slug: item.id,
      name: item.name,
      domain: item.domain,
      user_level: item.userLevel,
      intent: item.intent,
      messages: item.messages,
      tags: item.tags,
      version_note: item.versionNote,
      version: item.version,
    };
  }
  if (kind === "modelConfigs") {
    const item = record as ModelConfigRecord;
    return {
      slug: item.id,
      name: item.name,
      provider: item.provider,
      model: item.model,
      reasoning_level: item.reasoningLevel,
      temperature: item.temperature,
      max_output_tokens: item.maxOutputTokens,
      capability_flags: {
        supports_images: item.supportsImages,
        supports_files: item.supportsFiles,
        supports_tools: item.supportsTools,
        supports_json_schema: item.supportsJsonSchema,
      },
      raw_provider_params: parseJsonObject(item.rawProviderParamsJson),
      version: item.version,
    };
  }
  if (kind === "llmJudgeConfigs") {
    const item = record as LLMJudgeConfigRecord;
    return {
      slug: item.id,
      name: item.name,
      judge_prompt: item.judgePrompt,
      rubric_dimensions: JSON.parse(item.rubricDimensionsJson || "[]") as unknown,
      output_schema: parseJsonObject(item.outputSchemaJson),
      judge_model_config_slug: item.judgeModelConfigSlug,
      raw_provider_params: parseJsonObject(item.rawProviderParamsJson),
      calibration_status: item.calibrationStatus,
      version: item.version,
    };
  }
  if (kind === "metricAdapterConfigs") {
    const item = record as MetricAdapterConfigRecord;
    return {
      slug: item.id,
      name: item.name,
      adapter_kind: item.adapterKind,
      adapter_version: item.adapterVersion,
      required_inputs: parseIdList(item.requiredInputsText),
      output_schema: parseJsonObject(item.outputSchemaJson),
      capability_metadata: parseJsonObject(item.capabilityMetadataJson),
      local_only: item.localOnly,
      version: item.version,
    };
  }
  if (kind === "benchmarkSuites") {
    const item = record as BenchmarkSuiteRecord;
    return {
      slug: item.id,
      name: item.name,
      description: item.description,
      case_ids: parseIdList(item.caseIdsText),
      model_config_ids: parseIdList(item.modelConfigIdsText),
      system_prompt_ids: parseIdList(item.systemPromptIdsText),
      warmer_ids: parseIdList(item.warmerIdsText),
      evaluator_ids: parseIdList(item.evaluatorIdsText),
      controls: parseJsonObject(item.controlsJson),
      version: item.version,
    };
  }
  const item = record as EvaluatorRecord;
  return {
    slug: item.id,
    name: item.name,
    evaluator_type: item.evaluatorType,
    definition: parseJsonObject(item.definitionJson),
    version: item.version,
  };
}

function parseIdList(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}
