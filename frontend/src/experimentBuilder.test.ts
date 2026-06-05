import { describe, expect, it } from "vitest";

import {
  buildExperimentManifest,
  initialExperimentDraft,
  validateExperimentDraft,
  validateManifestForSave,
} from "./experimentBuilder";

describe("experiment builder draft validation", () => {
  it("reports missing selected dimensions before previewing a run matrix", () => {
    const result = validateExperimentDraft({
      name: "Copper context study",
      selectedCaseIds: [],
      selectedModelConfigIds: ["openai_high"],
      selectedSystemPromptIds: ["analyst_v3"],
      selectedWarmerIds: ["none"],
      selectedEvaluatorIds: [],
      controls: { replicates: 2, maxParallelRequests: 4, maxTotalCostUsd: 50 },
    });

    expect(result.valid).toBe(false);
    expect(result.errors).toContain("Select at least one case.");
  });

  it("builds a full-factorial manifest and estimates runs, attempts, tokens, and cost", () => {
    const result = validateExperimentDraft({
      name: "Copper context study",
      selectedCaseIds: ["chile_copper_memo"],
      selectedModelConfigIds: ["openai_high", "claude_high"],
      selectedSystemPromptIds: ["analyst_v3", "finance_v2"],
      selectedWarmerIds: ["none", "expert", "beginner", "adversarial"],
      selectedEvaluatorIds: ["sections_check"],
      controls: { replicates: 2, maxParallelRequests: 4, maxTotalCostUsd: 50 },
    });

    expect(result.valid).toBe(true);
    expect(result.preview.logicalRuns).toBe(16);
    expect(result.preview.runAttempts).toBe(32);
    expect(result.preview.estimatedTokens).toBeGreaterThan(0);
    expect(result.preview.estimatedCostUsd).toBeGreaterThan(0);
  });

  it("keeps warmers as structured manifest references instead of pasted prompt text", () => {
    const manifest = buildExperimentManifest({
      name: "Copper context study",
      selectedCaseIds: ["chile_copper_memo"],
      selectedModelConfigIds: ["openai_high"],
      selectedSystemPromptIds: ["analyst_v3"],
      selectedWarmerIds: ["expert"],
      selectedEvaluatorIds: ["sections_check"],
      controls: { replicates: 1, maxParallelRequests: 2, maxTotalCostUsd: 10 },
    });

    expect(manifest.warmers).toEqual([{ id: "expert" }]);
    expect(manifest.design.type).toBe("full_factorial");
    expect(manifest.evaluation.evaluators).toEqual([{ id: "sections_check" }]);
  });

  it("adds selected artifact input modes to generated manifests", () => {
    const manifest = buildExperimentManifest({
      ...initialExperimentDraft,
      selectedArtifactInputs: [{ id: "paper_text", inputMode: "pdf_text" }],
    });

    expect(manifest.artifacts).toEqual([{ id: "paper_text", input_mode: "pdf_text" }]);
  });

  it("emits explicit versions for versioned library selections", () => {
    const manifest = buildExperimentManifest({
      ...initialExperimentDraft,
      selectedModelConfigIds: ["openai_high@2"],
      selectedSystemPromptIds: ["analyst_v3@3"],
      selectedWarmerIds: ["expert@4"],
      selectedEvaluatorIds: ["sections_check@5"],
      selectedArtifactInputs: [{ id: "paper_text", version: 2, inputMode: "pdf_text" }],
    });

    expect(manifest.models).toEqual([{ id: "openai_high", version: 2 }]);
    expect(manifest.system_prompts).toEqual([{ id: "analyst_v3", version: 3 }]);
    expect(manifest.warmers).toEqual([{ id: "expert", version: 4 }]);
    expect(manifest.evaluation.evaluators).toEqual([{ id: "sections_check", version: 5 }]);
    expect(manifest.artifacts).toEqual([{ id: "paper_text", version: 2, input_mode: "pdf_text" }]);
  });

  it("keeps benchmark suite references and split filters in generated manifests", () => {
    const manifest = buildExperimentManifest({
      ...initialExperimentDraft,
      benchmarkSuiteId: "copper_suite",
      suiteSplit: "validation",
    });

    expect(manifest.suite).toEqual({ id: "copper_suite", split: "validation" });
    expect(manifest.design.split).toBe("validation");
  });

  it("validates edited manifest JSON dimensions before saving", () => {
    const errors = validateManifestForSave({
      id: "bad_exp",
      name: "Bad Experiment",
      cases: [],
      models: [{ id: "openai_high" }],
      system_prompts: [{ id: "analyst_v3" }],
      warmers: [{ id: "expert" }],
      design: { type: "full_factorial", replicates: 1, randomize_run_order: true },
      evaluation: { blind_review: true, human_pairwise: true, evaluators: [] },
      controls: {
        max_parallel_requests: 1,
        max_total_cost_usd: 10,
        retry_failed: true,
        cache_provider_calls: true,
        local_only: true,
      },
    });

    expect(errors).toContain("Manifest must include at least one case.");
  });

  it("allows edited manifest JSON to use a benchmark suite reference without dimensions", () => {
    const errors = validateManifestForSave({
      id: "suite_manifest",
      name: "Suite Manifest",
      suite: { id: "copper_suite", split: "dev" },
      design: { type: "full_factorial", replicates: 1, randomize_run_order: true, split: "dev" },
      evaluation: { blind_review: true, human_pairwise: true, evaluators: [] },
      controls: {
        max_parallel_requests: 1,
        max_total_cost_usd: 10,
        retry_failed: true,
        cache_provider_calls: true,
        local_only: true,
      },
    });

    expect(errors).toEqual([]);
  });
});
