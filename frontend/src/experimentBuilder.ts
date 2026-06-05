export type LibraryKind =
  | "cases"
  | "artifacts"
  | "systemPrompts"
  | "warmers"
  | "modelConfigs"
  | "evaluators"
  | "llmJudgeConfigs"
  | "metricAdapterConfigs"
  | "benchmarkSuites";

export type ReasoningLevel = "none" | "low" | "medium" | "high";
export type DatasetSplit = "dev" | "validation" | "holdout" | "archived";

export type MessageRole = "system" | "user" | "assistant";

export interface ConversationMessage {
  role: MessageRole;
  content: string;
}

export interface CaseRecord {
  id: string;
  name: string;
  prompt: string;
  datasetSplit: DatasetSplit;
  version: number;
}

export interface ArtifactRecord {
  id: string;
  name: string;
  artifactType: string;
  uri: string;
  inputMode: string;
  metadataJson: string;
  version: number;
}

export interface SystemPromptRecord {
  id: string;
  name: string;
  prompt: string;
  version: number;
}

export interface WarmerRecord {
  id: string;
  name: string;
  domain: string;
  userLevel: string;
  intent: string;
  messages: ConversationMessage[];
  tags: string[];
  version: number;
  versionNote: string;
}

export interface ModelConfigRecord {
  id: string;
  name: string;
  provider: string;
  model: string;
  reasoningLevel: ReasoningLevel;
  temperature: number;
  maxOutputTokens: number;
  supportsImages: boolean;
  supportsFiles: boolean;
  supportsTools: boolean;
  supportsJsonSchema: boolean;
  rawProviderParamsJson: string;
  version: number;
}

export interface EvaluatorRecord {
  id: string;
  name: string;
  evaluatorType: string;
  definitionJson: string;
  version: number;
}

export interface LLMJudgeConfigRecord {
  id: string;
  name: string;
  judgePrompt: string;
  rubricDimensionsJson: string;
  outputSchemaJson: string;
  judgeModelConfigSlug: string;
  rawProviderParamsJson: string;
  calibrationStatus: string;
  version: number;
}

export interface MetricAdapterConfigRecord {
  id: string;
  name: string;
  adapterKind: string;
  adapterVersion: string;
  requiredInputsText: string;
  outputSchemaJson: string;
  capabilityMetadataJson: string;
  localOnly: boolean;
  version: number;
}

export interface BenchmarkSuiteRecord {
  id: string;
  name: string;
  description: string;
  caseIdsText: string;
  modelConfigIdsText: string;
  systemPromptIdsText: string;
  warmerIdsText: string;
  evaluatorIdsText: string;
  controlsJson: string;
  version: number;
}

export interface LibraryState {
  cases: CaseRecord[];
  artifacts: ArtifactRecord[];
  systemPrompts: SystemPromptRecord[];
  warmers: WarmerRecord[];
  modelConfigs: ModelConfigRecord[];
  evaluators: EvaluatorRecord[];
  llmJudgeConfigs: LLMJudgeConfigRecord[];
  metricAdapterConfigs: MetricAdapterConfigRecord[];
  benchmarkSuites: BenchmarkSuiteRecord[];
}

export interface ExperimentControls {
  replicates: number;
  maxParallelRequests: number;
  maxTotalCostUsd: number;
  retryFailed?: boolean;
  cacheProviderCalls?: boolean;
  localOnly?: boolean;
}

export interface SelectedArtifactInput {
  id: string;
  version?: number;
  inputMode: string;
}

export interface ExperimentDraft {
  name: string;
  benchmarkSuiteId?: string;
  suiteSplit?: DatasetSplit | "";
  selectedCaseIds: string[];
  selectedArtifactInputs?: SelectedArtifactInput[];
  selectedModelConfigIds: string[];
  selectedSystemPromptIds: string[];
  selectedWarmerIds: string[];
  selectedEvaluatorIds: string[];
  controls: ExperimentControls;
}

export interface ExperimentManifest {
  id: string;
  name: string;
  suite?: {
    id: string;
    split?: DatasetSplit;
  };
  cases: Array<VersionedManifestReference>;
  artifacts?: Array<VersionedManifestReference & { input_mode?: string }>;
  models: Array<VersionedManifestReference>;
  system_prompts: Array<VersionedManifestReference>;
  warmers: Array<VersionedManifestReference>;
  design: {
    type: "full_factorial";
    replicates: number;
    randomize_run_order: boolean;
    split?: DatasetSplit;
  };
  evaluation: {
    blind_review: boolean;
    human_pairwise: boolean;
    evaluators: Array<
      VersionedManifestReference & { type?: string; definition?: Record<string, unknown> }
    >;
  };
  controls: {
    max_parallel_requests: number;
    max_total_cost_usd: number;
    retry_failed: boolean;
    cache_provider_calls: boolean;
    local_only: boolean;
  };
}

interface VersionedManifestReference {
  id: string;
  version?: number;
}

export interface ExperimentPreview {
  logicalRuns: number;
  runAttempts: number;
  estimatedTokens: number;
  estimatedCostUsd: number;
}

export interface ExperimentValidationResult {
  valid: boolean;
  errors: string[];
  preview: ExperimentPreview;
}

const TOKENS_PER_ATTEMPT = 1800;
const COST_PER_THOUSAND_TOKENS_USD = 0.006;

export const initialLibrary: LibraryState = {
  cases: [
    {
      id: "chile_copper_memo",
      name: "Chile Copper Memo",
      prompt:
        "Write a proper investment memo on the Chilean copper disruption scenario. Be thorough, specific, and explicit about uncertainty.",
      datasetSplit: "dev",
      version: 1,
    },
  ],
  artifacts: [
    {
      id: "copper_supply_notes",
      name: "Copper Supply Notes",
      artifactType: "memo",
      uri: "local://examples/copper-supply-notes.md",
      inputMode: "direct_file",
      metadataJson: '{\n  "domain": "commodities",\n  "source": "demo"\n}',
      version: 1,
    },
  ],
  systemPrompts: [
    {
      id: "expert_investment_analyst_v3",
      name: "Expert Investment Analyst v3",
      prompt:
        "You are a senior investment analyst. Write in memo form, separate evidence from assumptions, and flag unsupported numbers.",
      version: 3,
    },
    {
      id: "general_finance_assistant_v2",
      name: "General Finance Assistant v2",
      prompt: "You are a careful finance assistant. Prefer clear structure and concise caveats.",
      version: 2,
    },
  ],
  warmers: [
    {
      id: "none",
      name: "No Prior Context",
      domain: "commodities",
      userLevel: "neutral",
      intent: "baseline with no prior conversation",
      messages: [],
      tags: ["baseline", "copper"],
      version: 1,
      versionNote: "Baseline warmer for controlled comparisons.",
    },
    {
      id: "copper_expert_user_v2",
      name: "Copper Expert User",
      domain: "commodities",
      userLevel: "expert",
      intent: "prime market-structure analysis without changing the final task",
      messages: [
        {
          role: "user",
          content: "Focus on concentrate supply, smelter bottlenecks, and second-order effects.",
        },
        {
          role: "assistant",
          content: "I will keep supply-chain mechanics and uncertainty explicit.",
        },
      ],
      tags: ["copper", "expert", "investment-memo"],
      version: 2,
      versionNote: "Expert context for lift and distortion checks.",
    },
    {
      id: "copper_low_knowledge_user_v1",
      name: "Copper Low-Knowledge User",
      domain: "commodities",
      userLevel: "beginner",
      intent: "simulate a user who needs more explanation and framing",
      messages: [
        {
          role: "user",
          content: "I am new to copper markets and may mix up mines, smelters, and refiners.",
        },
      ],
      tags: ["copper", "beginner"],
      version: 1,
      versionNote: "Beginner context for clarity checks.",
    },
    {
      id: "copper_adversarial_user_v1",
      name: "Copper Adversarial User",
      domain: "commodities",
      userLevel: "adversarial",
      intent: "test whether prior framing causes unsupported bearish conclusions",
      messages: [
        {
          role: "user",
          content: "Assume the disruption is catastrophic and the trade is obvious.",
        },
      ],
      tags: ["copper", "adversarial", "distortion"],
      version: 1,
      versionNote: "Adversarial context for distortion checks.",
    },
  ],
  modelConfigs: [
    {
      id: "openai_gpt_high",
      name: "OpenAI GPT High",
      provider: "openai",
      model: "gpt-5.5",
      reasoningLevel: "high",
      temperature: 0.2,
      maxOutputTokens: 4000,
      supportsImages: true,
      supportsFiles: true,
      supportsTools: true,
      supportsJsonSchema: true,
      rawProviderParamsJson: '{\n  "reasoning_effort": "high"\n}',
      version: 1,
    },
    {
      id: "claude_high",
      name: "Claude High",
      provider: "anthropic",
      model: "claude-opus",
      reasoningLevel: "high",
      temperature: 0.2,
      maxOutputTokens: 4000,
      supportsImages: true,
      supportsFiles: true,
      supportsTools: false,
      supportsJsonSchema: false,
      rawProviderParamsJson: '{\n  "thinking_budget": "high"\n}',
      version: 1,
    },
  ],
  evaluators: [
    {
      id: "investment_memo_required_sections_v1",
      name: "Required Sections",
      evaluatorType: "deterministic",
      definitionJson:
        '{\n  "required_sections": ["thesis", "variant view", "risks", "watch items"]\n}',
      version: 1,
    },
    {
      id: "hallucinated_numbers_check_v1",
      name: "Hallucinated Numbers Check",
      evaluatorType: "deterministic",
      definitionJson: '{\n  "check": "unsupported_numeric_claims"\n}',
      version: 1,
    },
    {
      id: "investment_memo_token_budget_v1",
      name: "Investment memo token budget",
      evaluatorType: "deterministic",
      definitionJson:
        '{\n  "kind": "token_budget",\n  "criterion": "investment_memo_token_budget",\n  "max_output_tokens": 1200\n}',
      version: 1,
    },
    {
      id: "investment_memo_llm_judge_v2",
      name: "Investment memo LLM judge",
      evaluatorType: "llm_judge",
      definitionJson: '{\n  "criterion": "investment_memo_llm_judge"\n}',
      version: 2,
    },
  ],
  llmJudgeConfigs: [
    {
      id: "memo_quality_judge_v1",
      name: "Memo Quality Judge",
      judgePrompt:
        "Score the answer against the rubric. Return JSON only and do not rely on hidden metadata.",
      rubricDimensionsJson:
        '[\n  { "name": "specificity", "scale": "1-5" },\n  { "name": "evidence", "scale": "1-5" }\n]',
      outputSchemaJson:
        '{\n  "type": "object",\n  "properties": {\n    "score": { "type": "number" },\n    "explanation": { "type": "string" }\n  },\n  "required": ["score", "explanation"]\n}',
      judgeModelConfigSlug: "openai_gpt_high",
      rawProviderParamsJson: '{\n  "temperature": 0\n}',
      calibrationStatus: "draft",
      version: 1,
    },
  ],
  metricAdapterConfigs: [
    {
      id: "retrieval_precision_local",
      name: "Retrieval Precision Local",
      adapterKind: "retrieval_precision",
      adapterVersion: "local-1",
      requiredInputsText: "answer_text\nretrieved_chunks",
      outputSchemaJson:
        '{\n  "type": "object",\n  "properties": {\n    "score": { "type": "number" },\n    "explanation": { "type": "string" }\n  }\n}',
      capabilityMetadataJson: "{}",
      localOnly: true,
      version: 1,
    },
  ],
  benchmarkSuites: [
    {
      id: "copper_suite",
      name: "Copper Benchmark Suite",
      description: "Copper memo benchmark suite with locked V2 membership.",
      caseIdsText: "chile_copper_memo",
      modelConfigIdsText: "openai_gpt_high\nclaude_high",
      systemPromptIdsText: "expert_investment_analyst_v3\ngeneral_finance_assistant_v2",
      warmerIdsText:
        "none\ncopper_expert_user_v2\ncopper_low_knowledge_user_v1\ncopper_adversarial_user_v1",
      evaluatorIdsText: "investment_memo_required_sections_v1",
      controlsJson:
        '{\n  "replicates": 2,\n  "max_parallel_requests": 4,\n  "max_total_cost_usd": 50,\n  "local_only": true\n}',
      version: 1,
    },
    {
      id: "v2_copper_benchmark_suite",
      name: "V2 Copper Benchmark Suite",
      description: "Local-only synthetic V2 demo suite extending the copper memo scenario.",
      caseIdsText: "chile_copper_memo",
      modelConfigIdsText: "openai_gpt_high\nclaude_high",
      systemPromptIdsText: "expert_investment_analyst_v3\ngeneral_finance_assistant_v2",
      warmerIdsText:
        "none\ncopper_expert_user_v2\ncopper_low_knowledge_user_v1\ncopper_adversarial_user_v1",
      evaluatorIdsText:
        "investment_memo_required_sections_v1\ninvestment_memo_token_budget_v1\ninvestment_memo_llm_judge_v2\nhallucinated_numbers_check_v1",
      controlsJson:
        '{\n  "local_only": true,\n  "random_seed": 25,\n  "randomize_run_order": false,\n  "replicates": 2\n}',
      version: 1,
    },
  ],
};

export const initialExperimentDraft: ExperimentDraft = {
  name: "Copper memo context sensitivity",
  selectedCaseIds: ["chile_copper_memo"],
  selectedArtifactInputs: [],
  selectedModelConfigIds: ["openai_gpt_high", "claude_high"],
  selectedSystemPromptIds: ["expert_investment_analyst_v3", "general_finance_assistant_v2"],
  selectedWarmerIds: [
    "none",
    "copper_expert_user_v2",
    "copper_low_knowledge_user_v1",
    "copper_adversarial_user_v1",
  ],
  selectedEvaluatorIds: ["investment_memo_required_sections_v1"],
  controls: {
    replicates: 2,
    maxParallelRequests: 4,
    maxTotalCostUsd: 50,
    retryFailed: true,
    cacheProviderCalls: true,
    localOnly: true,
  },
};

export function buildExperimentManifest(draft: ExperimentDraft): ExperimentManifest {
  const suiteSplit = draft.suiteSplit || undefined;
  const manifest: ExperimentManifest = {
    id: slugify(draft.name),
    name: draft.name.trim() || "Untitled experiment",
    cases: draft.selectedCaseIds.map(toManifestReference),
    models: draft.selectedModelConfigIds.map(toManifestReference),
    system_prompts: draft.selectedSystemPromptIds.map(toManifestReference),
    warmers: draft.selectedWarmerIds.map(toManifestReference),
    design: {
      type: "full_factorial",
      replicates: normalizedReplicates(draft.controls.replicates),
      randomize_run_order: true,
      ...(suiteSplit ? { split: suiteSplit } : {}),
    },
    evaluation: {
      blind_review: true,
      human_pairwise: true,
      evaluators: draft.selectedEvaluatorIds.map(toManifestReference),
    },
    controls: {
      max_parallel_requests: Math.max(1, Math.trunc(draft.controls.maxParallelRequests || 1)),
      max_total_cost_usd: Math.max(0, draft.controls.maxTotalCostUsd || 0),
      retry_failed: draft.controls.retryFailed ?? true,
      cache_provider_calls: draft.controls.cacheProviderCalls ?? true,
      local_only: draft.controls.localOnly ?? true,
    },
  };
  const selectedArtifactInputs = draft.selectedArtifactInputs ?? [];
  if (selectedArtifactInputs.length) {
    manifest.artifacts = selectedArtifactInputs.map((artifact) => ({
      id: artifact.id,
      ...(artifact.version ? { version: artifact.version } : {}),
      input_mode: artifact.inputMode,
    }));
  }
  if (draft.benchmarkSuiteId) {
    manifest.suite = {
      id: draft.benchmarkSuiteId,
      ...(suiteSplit ? { split: suiteSplit } : {}),
    };
  }
  return manifest;
}

export function validateExperimentDraft(draft: ExperimentDraft): ExperimentValidationResult {
  const errors: string[] = [];
  if (!draft.name.trim()) {
    errors.push("Name the experiment.");
  }
  if (draft.selectedCaseIds.length === 0) {
    errors.push("Select at least one case.");
  }
  if (draft.selectedModelConfigIds.length === 0) {
    errors.push("Select at least one model config.");
  }
  if (draft.selectedSystemPromptIds.length === 0) {
    errors.push("Select at least one system prompt.");
  }
  if (draft.selectedWarmerIds.length === 0) {
    errors.push("Select at least one conversation warmer.");
  }
  if (draft.controls.replicates < 1) {
    errors.push("Replicates must be at least 1.");
  }
  return {
    valid: errors.length === 0,
    errors,
    preview: estimateExperimentPreview(draft),
  };
}

export function validateManifestForSave(value: unknown): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return ["Manifest must be a JSON object."];
  }
  const manifest = value as Partial<ExperimentManifest>;
  const errors: string[] = [];
  if (!manifest.name || typeof manifest.name !== "string") {
    errors.push("Manifest must include a name.");
  }
  const hasSuiteReference =
    !!manifest.suite &&
    typeof (manifest.suite as { id?: unknown }).id === "string" &&
    Boolean((manifest.suite as { id: string }).id.trim());
  if (!hasSuiteReference) {
    appendDimensionError(errors, manifest.cases, "case");
    appendDimensionError(errors, manifest.models, "model");
    appendDimensionError(errors, manifest.system_prompts, "system prompt");
    appendDimensionError(errors, manifest.warmers, "conversation warmer");
  }
  if (manifest.design?.type !== "full_factorial") {
    errors.push("Manifest design type must be full_factorial.");
  }
  if (
    typeof manifest.design?.replicates !== "number" ||
    !Number.isInteger(manifest.design.replicates) ||
    manifest.design.replicates < 1
  ) {
    errors.push("Manifest replicates must be an integer greater than or equal to 1.");
  }
  return errors;
}

export function estimateManifestPreview(manifest: ExperimentManifest): ExperimentPreview {
  const logicalRuns =
    manifest.cases.length *
    manifest.models.length *
    manifest.system_prompts.length *
    manifest.warmers.length;
  const runAttempts = logicalRuns * normalizedReplicates(manifest.design.replicates);
  const estimatedTokens = runAttempts * TOKENS_PER_ATTEMPT;
  return {
    logicalRuns,
    runAttempts,
    estimatedTokens,
    estimatedCostUsd: Number(
      ((estimatedTokens / 1000) * COST_PER_THOUSAND_TOKENS_USD).toFixed(2),
    ),
  };
}

export function estimateExperimentPreview(draft: ExperimentDraft): ExperimentPreview {
  const logicalRuns =
    draft.selectedCaseIds.length *
    draft.selectedModelConfigIds.length *
    draft.selectedSystemPromptIds.length *
    draft.selectedWarmerIds.length;
  const runAttempts = logicalRuns * normalizedReplicates(draft.controls.replicates);
  const estimatedTokens = runAttempts * TOKENS_PER_ATTEMPT;
  return {
    logicalRuns,
    runAttempts,
    estimatedTokens,
    estimatedCostUsd: Number(
      ((estimatedTokens / 1000) * COST_PER_THOUSAND_TOKENS_USD).toFixed(2),
    ),
  };
}

export function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value || "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Expected a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

export function parseMessages(value: string): ConversationMessage[] {
  const parsed: unknown = JSON.parse(value || "[]");
  if (!Array.isArray(parsed)) {
    throw new Error("Expected a JSON array of messages.");
  }
  return parsed.map((message, index) => {
    if (!message || typeof message !== "object" || Array.isArray(message)) {
      throw new Error(`Message ${index + 1} must be an object.`);
    }
    const role = "role" in message ? message.role : undefined;
    const content = "content" in message ? message.content : undefined;
    if (role !== "system" && role !== "user" && role !== "assistant") {
      throw new Error(`Message ${index + 1} must use role system, user, or assistant.`);
    }
    if (typeof content !== "string" || !content.trim()) {
      throw new Error(`Message ${index + 1} content is required.`);
    }
    return { role, content };
  });
}

export function slugify(value: string): string {
  const slug = value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug || "untitled";
}

function normalizedReplicates(value: number): number {
  return Math.max(1, Math.trunc(value || 1));
}

function toManifestReference(value: string): VersionedManifestReference {
  const [id, version] = parseVersionedSelection(value);
  return version === undefined ? { id } : { id, version };
}

function parseVersionedSelection(value: string): [string, number | undefined] {
  const match = /^(.*)@([1-9]\d*)$/.exec(value);
  if (!match) return [value, undefined];
  return [match[1], Number(match[2])];
}

function appendDimensionError(errors: string[], values: unknown, label: string) {
  if (!Array.isArray(values) || values.length === 0) {
    errors.push(`Manifest must include at least one ${label}.`);
  }
}
