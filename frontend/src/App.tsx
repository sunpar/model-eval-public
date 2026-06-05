import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  Ban,
  Boxes,
  Check,
  CheckCircle2,
  Download,
  FileText,
  FlaskConical,
  GitCompareArrows,
  Library,
  ListChecks,
  MonitorPlay,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";

import {
  ApiDerivedArtifact,
  ApiAnalyticsFilters,
  ApiCarryoverSummaryRow,
  ApiContextSensitivityRow,
  ApiCostQualityFrontierRow,
  ApiArtifactPreprocessingRun,
  ApiDivergenceSummaryRow,
  ApiMonitorExperiment,
  ApiMonitorRun,
  ApiNondeterminismRow,
  ApiReviewItem,
  ApiReviewSet,
  ApiQualityTableRow,
  ApiResultsAnalytics,
  ApiMetricAdapterRunSummary,
  ApiMetricAdapterScoreRow,
  ApiPromptfooImportPreviewResponse,
  ApiPromptfooWarning,
  ApiWarmerLiftRow,
  ApiRunAttempt,
  cancelMonitorExperiment,
  createExperimentDraft,
  createLibraryRecord,
  createReviewAssignments,
  createReviewer,
  createReviewSetFromExperiment,
  getReviewSetForExperiment,
  getReviewerQueue,
  getReviewSet,
  getExperimentAnalytics,
  exportExperiment,
  isConflict,
  listArtifactDerivedArtifacts,
  listArtifactPreprocessingRuns,
  listMonitorExperiments,
  listMonitorRuns,
  listRunAttempts,
  previewPromptfooImport,
  queueExperiment,
  retryFailedRun,
  runExperimentJudge,
  runExperimentMetricAdapters,
  startArtifactPreprocessing,
  submitReviewAssignmentDecision,
  submitReviewDecision,
  updateArtifactInputMode,
  updateExperimentDraft,
} from "./api";
import {
  ArtifactRecord,
  BenchmarkSuiteRecord,
  CaseRecord,
  DatasetSplit,
  EvaluatorRecord,
  ExperimentDraft,
  ExperimentManifest,
  ExperimentPreview,
  LLMJudgeConfigRecord,
  LibraryKind,
  LibraryState,
  MetricAdapterConfigRecord,
  ModelConfigRecord,
  SelectedArtifactInput,
  SystemPromptRecord,
  WarmerRecord,
  buildExperimentManifest,
  estimateManifestPreview,
  initialExperimentDraft,
  initialLibrary,
  parseJsonObject,
  parseMessages,
  slugify,
  validateExperimentDraft,
  validateManifestForSave,
} from "./experimentBuilder";

type RouteId = "library" | "experiment" | "monitor" | "comparison" | "results";
type ModeId = "playground" | "experiment" | "benchmark";
type DraftStatus = "draft" | "queued" | "running" | "complete" | "failed" | "canceled" | "skipped";
type DraftSelectionKey =
  | "selectedCaseIds"
  | "selectedModelConfigIds"
  | "selectedSystemPromptIds"
  | "selectedWarmerIds"
  | "selectedEvaluatorIds";

interface DraftExperimentRecord {
  id: string;
  apiId?: number;
  experimentSlug?: string;
  projectSlug?: string;
  name: string;
  manifest: ExperimentManifest;
  preview: ReturnType<typeof validateExperimentDraft>["preview"];
  status: DraftStatus;
}

interface ManifestEditorState {
  errors: string[];
  preview: ExperimentPreview;
}

const routes: Array<{ id: RouteId; label: string; icon: typeof Library }> = [
  { id: "library", label: "Library", icon: Library },
  { id: "experiment", label: "Experiment Builder", icon: FlaskConical },
  { id: "monitor", label: "Run Monitor", icon: MonitorPlay },
  { id: "comparison", label: "Comparison Workspace", icon: GitCompareArrows },
  { id: "results", label: "Results", icon: BarChart3 },
];

const libraryTabs: Array<{ id: LibraryKind; label: string; icon: typeof FileText }> = [
  { id: "cases", label: "Cases", icon: FileText },
  { id: "artifacts", label: "Artifacts", icon: Boxes },
  { id: "systemPrompts", label: "System prompts", icon: Sparkles },
  { id: "warmers", label: "Conversation warmers", icon: GitCompareArrows },
  { id: "modelConfigs", label: "Model configs", icon: Settings2 },
  { id: "evaluators", label: "Evaluators", icon: ListChecks },
  { id: "llmJudgeConfigs", label: "LLM judges", icon: Sparkles },
  { id: "metricAdapterConfigs", label: "Metric adapters", icon: ListChecks },
  { id: "benchmarkSuites", label: "Benchmark suites", icon: Boxes },
];

const defaultCaseForm: CaseRecord = {
  id: "",
  name: "",
  prompt: "",
  datasetSplit: "dev",
  version: 1,
};

const defaultArtifactForm: ArtifactRecord = {
  id: "",
  name: "",
  artifactType: "memo",
  uri: "",
  inputMode: "direct_file",
  metadataJson: "{}",
  version: 1,
};

const defaultSystemPromptForm: SystemPromptRecord = {
  id: "",
  name: "",
  prompt: "",
  version: 1,
};

const defaultWarmerForm: WarmerRecord = {
  id: "",
  name: "",
  domain: "",
  userLevel: "",
  intent: "",
  messages: [],
  tags: [],
  version: 1,
  versionNote: "",
};

const defaultModelConfigForm: ModelConfigRecord = {
  id: "",
  name: "",
  provider: "openai",
  model: "",
  reasoningLevel: "none",
  temperature: 0.2,
  maxOutputTokens: 4000,
  supportsImages: false,
  supportsFiles: false,
  supportsTools: false,
  supportsJsonSchema: false,
  rawProviderParamsJson: "{}",
  version: 1,
};

const defaultEvaluatorForm: EvaluatorRecord = {
  id: "",
  name: "",
  evaluatorType: "deterministic",
  definitionJson: "{}",
  version: 1,
};

const defaultLLMJudgeConfigForm: LLMJudgeConfigRecord = {
  id: "",
  name: "",
  judgePrompt: "",
  rubricDimensionsJson: "[]",
  outputSchemaJson: '{\n  "type": "object"\n}',
  judgeModelConfigSlug: "",
  rawProviderParamsJson: "{}",
  calibrationStatus: "draft",
  version: 1,
};

const defaultMetricAdapterConfigForm: MetricAdapterConfigRecord = {
  id: "",
  name: "",
  adapterKind: "retrieval_precision",
  adapterVersion: "local-1",
  requiredInputsText: "answer_text\nretrieved_chunks",
  outputSchemaJson: '{\n  "type": "object"\n}',
  capabilityMetadataJson: "{}",
  localOnly: true,
  version: 1,
};

const defaultBenchmarkSuiteForm: BenchmarkSuiteRecord = {
  id: "",
  name: "",
  description: "",
  caseIdsText: "chile_copper_memo",
  modelConfigIdsText: "openai_gpt_high\nclaude_high",
  systemPromptIdsText: "expert_investment_analyst_v3\ngeneral_finance_assistant_v2",
  warmerIdsText:
    "none\ncopper_expert_user_v2\ncopper_low_knowledge_user_v1\ncopper_adversarial_user_v1",
  evaluatorIdsText: "investment_memo_required_sections_v1",
  controlsJson:
    '{\n  "replicates": 2,\n  "max_parallel_requests": 4,\n  "max_total_cost_usd": 50,\n  "local_only": true\n}',
  version: 1,
};

const preprocessingParsers = [
  { value: "pdf_text", label: "PDF text" },
  { value: "pdf_page_screenshots", label: "PDF page screenshots" },
  { value: "image_normalization", label: "Image normalization" },
  { value: "selected_figure", label: "Selected figure" },
  { value: "table_extraction", label: "Table extraction" },
  { value: "retrieval_chunks", label: "Retrieval chunks" },
  { value: "paper_card", label: "Paper cards" },
];

function defaultPreprocessingPayload(parserName: string): Record<string, unknown> {
  if (parserName === "selected_figure") {
    return { page_number: 1, region: { x: 0, y: 0, width: 1, height: 1 } };
  }
  if (parserName === "table_extraction") {
    return {
      page_number: 1,
      region: { x: 0, y: 0, width: 1, height: 1 },
      table: { columns: [], rows: [] },
    };
  }
  return {};
}

const artifactInputModes = [
  { value: "direct_file", label: "Direct file" },
  { value: "image_direct", label: "Direct image" },
  { value: "pdf_text", label: "PDF text" },
  { value: "pdf_page_screenshots", label: "PDF page screenshots" },
  { value: "ocr_text", label: "OCR text" },
  { value: "selected_figures", label: "Selected figures" },
  { value: "table_extraction", label: "Table extraction" },
  { value: "retrieval_chunks", label: "Retrieval chunks" },
  { value: "paper_cards", label: "Paper cards" },
];

function App() {
  const [mode, setMode] = useState<ModeId>("experiment");
  const [route, setRoute] = useState<RouteId>("library");
  const [library, setLibrary] = useState<LibraryState>(initialLibrary);
  const [activeLibrary, setActiveLibrary] = useState<LibraryKind>("cases");
  const [caseForm, setCaseForm] = useState<CaseRecord>(defaultCaseForm);
  const [artifactForm, setArtifactForm] = useState<ArtifactRecord>(defaultArtifactForm);
  const [artifactPreprocessingParser, setArtifactPreprocessingParser] = useState("pdf_text");
  const [artifactPreprocessingPayload, setArtifactPreprocessingPayload] = useState(() =>
    prettyJson(defaultPreprocessingPayload("pdf_text")),
  );
  const [artifactPreprocessingRuns, setArtifactPreprocessingRuns] = useState<
    Record<string, ApiArtifactPreprocessingRun>
  >({});
  const [artifactPreprocessingHistory, setArtifactPreprocessingHistory] = useState<
    Record<string, ApiArtifactPreprocessingRun[]>
  >({});
  const [artifactDerivedArtifacts, setArtifactDerivedArtifacts] = useState<
    Record<string, ApiDerivedArtifact[]>
  >({});
  const [artifactPreprocessingError, setArtifactPreprocessingError] = useState<string | null>(null);
  const [artifactPreprocessingBusy, setArtifactPreprocessingBusy] = useState(false);
  const [systemPromptForm, setSystemPromptForm] =
    useState<SystemPromptRecord>(defaultSystemPromptForm);
  const [warmerForm, setWarmerForm] = useState<WarmerRecord>(defaultWarmerForm);
  const [warmerMessagesText, setWarmerMessagesText] = useState("[]");
  const [warmerTagsText, setWarmerTagsText] = useState("");
  const [modelConfigForm, setModelConfigForm] =
    useState<ModelConfigRecord>(defaultModelConfigForm);
  const [evaluatorForm, setEvaluatorForm] = useState<EvaluatorRecord>(defaultEvaluatorForm);
  const [llmJudgeConfigForm, setLLMJudgeConfigForm] = useState<LLMJudgeConfigRecord>(
    defaultLLMJudgeConfigForm,
  );
  const [metricAdapterConfigForm, setMetricAdapterConfigForm] =
    useState<MetricAdapterConfigRecord>(defaultMetricAdapterConfigForm);
  const [benchmarkSuiteForm, setBenchmarkSuiteForm] =
    useState<BenchmarkSuiteRecord>(defaultBenchmarkSuiteForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [draft, setDraft] = useState<ExperimentDraft>(initialExperimentDraft);
  const validation = useMemo(() => validateExperimentDraft(draft), [draft]);
  const generatedManifest = useMemo(() => buildExperimentManifest(draft), [draft]);
  const [manifestText, setManifestText] = useState(() => prettyJson(generatedManifest));
  const [manifestDirty, setManifestDirty] = useState(false);
  const manifestEditorState = useMemo(() => validateManifestEditor(manifestText), [manifestText]);
  const activePreview = manifestDirty ? manifestEditorState.preview : validation.preview;
  const activeValidationErrors = manifestDirty ? manifestEditorState.errors : validation.errors;
  const [manifestError, setManifestError] = useState<string | null>(null);
  const [draftExperiments, setDraftExperiments] = useState<DraftExperimentRecord[]>([]);

  const activeItems = library[activeLibrary];
  const selectedDraft = draftExperiments[0];

  useEffect(() => {
    if (!manifestDirty) {
      setManifestText(prettyJson(generatedManifest));
    }
  }, [generatedManifest, manifestDirty]);

  function refreshManifestEditor() {
    setManifestText(prettyJson(buildExperimentManifest(draft)));
    setManifestDirty(false);
    setManifestError(null);
  }

  async function saveCurrentLibraryItem() {
    setFormError(null);
    try {
      if (activeLibrary === "cases") {
        const record = withRecordId(caseForm);
        if (!(await persistLibraryRecord("cases", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({ ...current, cases: [...current.cases, record] }));
        setCaseForm(defaultCaseForm);
        return;
      }
      if (activeLibrary === "artifacts") {
        parseJsonObject(artifactForm.metadataJson);
        const record = withRecordId(artifactForm);
        const existing = library.artifacts.find((item) => item.id === record.id);
        if (existing) {
          if (!artifactRecordMatchesExistingExceptInputMode(existing, record)) {
            setFormError(
              "Existing artifacts only support input mode updates. Use a new slug or version for artifact metadata edits.",
            );
            return;
          }
          await updateArtifactInputMode(record.id, record.inputMode);
          const updated = { ...existing, inputMode: record.inputMode };
          setLibrary((current) => ({
            ...current,
            artifacts: current.artifacts.map((item) => (item.id === record.id ? updated : item)),
          }));
          setArtifactForm(updated);
          return;
        }
        if (!(await persistLibraryRecord("artifacts", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({ ...current, artifacts: [...current.artifacts, record] }));
        setArtifactForm(defaultArtifactForm);
        return;
      }
      if (activeLibrary === "systemPrompts") {
        const record = withRecordId(systemPromptForm);
        if (!(await persistLibraryRecord("systemPrompts", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({
          ...current,
          systemPrompts: [...current.systemPrompts, record],
        }));
        setSystemPromptForm(defaultSystemPromptForm);
        return;
      }
      if (activeLibrary === "warmers") {
        const messages = parseMessages(warmerMessagesText);
        const record = withRecordId({
          ...warmerForm,
          messages,
          tags: parseTags(warmerTagsText),
        });
        if (!(await persistLibraryRecord("warmers", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({ ...current, warmers: [...current.warmers, record] }));
        setWarmerForm(defaultWarmerForm);
        setWarmerMessagesText("[]");
        setWarmerTagsText("");
        return;
      }
      if (activeLibrary === "modelConfigs") {
        parseJsonObject(modelConfigForm.rawProviderParamsJson);
        const record = withRecordId(modelConfigForm);
        if (!(await persistLibraryRecord("modelConfigs", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({
          ...current,
          modelConfigs: [...current.modelConfigs, record],
        }));
        setModelConfigForm(defaultModelConfigForm);
        return;
      }
      if (activeLibrary === "evaluators") {
        parseJsonObject(evaluatorForm.definitionJson);
        const record = withRecordId(evaluatorForm);
        if (!(await persistLibraryRecord("evaluators", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({ ...current, evaluators: [...current.evaluators, record] }));
        setEvaluatorForm(defaultEvaluatorForm);
        return;
      }
      if (activeLibrary === "metricAdapterConfigs") {
        const outputSchema = parseJsonObject(metricAdapterConfigForm.outputSchemaJson);
        const capabilityMetadata = parseJsonObject(metricAdapterConfigForm.capabilityMetadataJson);
        const requiredInputs = parseIdList(metricAdapterConfigForm.requiredInputsText);
        if (!requiredInputs.length) {
          setFormError("Metric adapter configs must include at least one required input.");
          return;
        }
        const record = withRecordId({
          ...metricAdapterConfigForm,
          requiredInputsText: requiredInputs.join("\n"),
          outputSchemaJson: prettyJson(outputSchema),
          capabilityMetadataJson: prettyJson(capabilityMetadata),
        });
        if (!(await persistLibraryRecord("metricAdapterConfigs", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({
          ...current,
          metricAdapterConfigs: [...current.metricAdapterConfigs, record],
        }));
        setMetricAdapterConfigForm(defaultMetricAdapterConfigForm);
        return;
      }
      if (activeLibrary === "benchmarkSuites") {
        parseJsonObject(benchmarkSuiteForm.controlsJson);
        const record = withRecordId(benchmarkSuiteForm);
        if (!(await persistLibraryRecord("benchmarkSuites", record))) {
          setFormError("Resource already exists.");
          return;
        }
        setLibrary((current) => ({
          ...current,
          benchmarkSuites: [...current.benchmarkSuites, record],
        }));
        setBenchmarkSuiteForm(defaultBenchmarkSuiteForm);
        return;
      }
      const outputSchema = parseJsonObject(llmJudgeConfigForm.outputSchemaJson);
      const rubricDimensions = parseJsonArray(llmJudgeConfigForm.rubricDimensionsJson);
      parseJsonObject(llmJudgeConfigForm.rawProviderParamsJson);
      if (!library.modelConfigs.some((item) => item.id === llmJudgeConfigForm.judgeModelConfigSlug)) {
        setFormError("Judge model config must reference an existing model config.");
        return;
      }
      if (outputSchema.type !== "object") {
        setFormError("Output schema must be a JSON object schema.");
        return;
      }
      const record = withRecordId({
        ...llmJudgeConfigForm,
        rubricDimensionsJson: prettyJson(rubricDimensions),
        outputSchemaJson: prettyJson(outputSchema),
      });
      if (!(await persistLibraryRecord("llmJudgeConfigs", record))) {
        setFormError("Resource already exists.");
        return;
      }
      setLibrary((current) => ({
        ...current,
        llmJudgeConfigs: [...current.llmJudgeConfigs, record],
      }));
      setLLMJudgeConfigForm(defaultLLMJudgeConfigForm);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "The editor value is invalid.");
    }
  }

  function inspectLibraryItem(id: string) {
    setFormError(null);
    setArtifactPreprocessingError(null);
    if (activeLibrary === "cases") {
      const record = library.cases.find((item) => item.id === id);
      if (record) setCaseForm(record);
    } else if (activeLibrary === "artifacts") {
      const record = library.artifacts.find((item) => item.id === id);
      if (record) {
        setArtifactForm(record);
        void loadArtifactPreprocessingState(record.id);
      }
    } else if (activeLibrary === "systemPrompts") {
      const record = library.systemPrompts.find((item) => item.id === id);
      if (record) setSystemPromptForm(record);
    } else if (activeLibrary === "warmers") {
      const record = library.warmers.find((item) => item.id === id);
      if (record) {
        setWarmerForm(record);
        setWarmerMessagesText(prettyJson(record.messages));
        setWarmerTagsText(record.tags.join(", "));
      }
    } else if (activeLibrary === "modelConfigs") {
      const record = library.modelConfigs.find((item) => item.id === id);
      if (record) setModelConfigForm(record);
    } else if (activeLibrary === "evaluators") {
      const record = library.evaluators.find((item) => item.id === id);
      if (record) setEvaluatorForm(record);
    } else if (activeLibrary === "metricAdapterConfigs") {
      const record = library.metricAdapterConfigs.find((item) => metricAdapterConfigKey(item) === id);
      if (record) setMetricAdapterConfigForm(record);
    } else if (activeLibrary === "benchmarkSuites") {
      const record = library.benchmarkSuites.find((item) => item.id === id);
      if (record) setBenchmarkSuiteForm(record);
    } else {
      const record = library.llmJudgeConfigs.find((item) => item.id === id);
      if (record) setLLMJudgeConfigForm(record);
    }
  }

  async function loadArtifactPreprocessingState(artifactId: string) {
    try {
      const [runsResponse, derivedResponse] = await Promise.all([
        listArtifactPreprocessingRuns(artifactId),
        listArtifactDerivedArtifacts(artifactId),
      ]);
      const runs = Array.isArray(runsResponse) ? runsResponse : [];
      const derivedArtifacts = Array.isArray(derivedResponse) ? derivedResponse : [];
      setArtifactPreprocessingHistory((current) => ({ ...current, [artifactId]: runs }));
      setArtifactDerivedArtifacts((current) => ({ ...current, [artifactId]: derivedArtifacts }));
      setArtifactPreprocessingRuns((current) => {
        const next = { ...current };
        const latestRun = runs[runs.length - 1];
        if (latestRun) {
          next[artifactId] = latestRun;
        } else {
          delete next[artifactId];
        }
        return next;
      });
      if (derivedArtifacts.length) {
        const derivedRecords = derivedArtifacts.map(derivedArtifactRecord);
        setLibrary((current) => ({
          ...current,
          artifacts: upsertArtifactRecords(current.artifacts, derivedRecords),
        }));
      }
    } catch (error) {
      setArtifactPreprocessingError(
        error instanceof Error ? error.message : "Could not load derived artifact records.",
      );
    }
  }

  async function changeArtifactInputMode(inputMode: string) {
    const artifactId = artifactForm.id.trim();
    setArtifactForm((current) => ({ ...current, inputMode }));
    if (!library.artifacts.some((item) => item.id === artifactId)) {
      return;
    }
    setArtifactPreprocessingError(null);
    try {
      const updated = await updateArtifactInputMode(artifactId, inputMode);
      const updatedInputMode = updated.input_mode || inputMode;
      setArtifactForm((current) =>
        current.id === artifactId ? { ...current, inputMode: updatedInputMode } : current,
      );
      setLibrary((current) => ({
        ...current,
        artifacts: current.artifacts.map((item) =>
          item.id === artifactId ? { ...item, inputMode: updatedInputMode } : item,
        ),
      }));
    } catch (error) {
      setArtifactPreprocessingError(
        error instanceof Error ? error.message : "Could not update artifact input mode.",
      );
    }
  }

  async function runArtifactPreprocessing() {
    const artifactId = artifactForm.id.trim();
    if (!artifactId) {
      setArtifactPreprocessingError("Select an artifact before preprocessing.");
      return;
    }
    setArtifactPreprocessingBusy(true);
    setArtifactPreprocessingError(null);
    try {
      const payload = parseJsonObject(artifactPreprocessingPayload);
      const run = await startArtifactPreprocessing(
        artifactId,
        artifactPreprocessingParser,
        payload,
      );
      setArtifactPreprocessingRuns((current) => ({ ...current, [artifactId]: run }));
      setArtifactPreprocessingHistory((current) => ({
        ...current,
        [artifactId]: [run, ...(current[artifactId] ?? []).filter((item) => item.id !== run.id)],
      }));
      setArtifactDerivedArtifacts((current) => ({
        ...current,
        [artifactId]: run.derived_artifacts.length
          ? run.derived_artifacts
          : run.status === "failed"
            ? (current[artifactId] ?? [])
            : [],
      }));
      if (run.derived_artifacts.length) {
        const derivedRecords = run.derived_artifacts.map(derivedArtifactRecord);
        setLibrary((current) => ({
          ...current,
          artifacts: upsertArtifactRecords(current.artifacts, derivedRecords),
        }));
      }
    } catch (error) {
      setArtifactPreprocessingError(
        error instanceof Error ? error.message : "Could not start preprocessing.",
      );
    } finally {
      setArtifactPreprocessingBusy(false);
    }
  }

  async function saveDraftExperiment(status: DraftStatus = "draft") {
    setManifestError(null);
    try {
      const parsed = JSON.parse(manifestText) as ExperimentManifest;
      const manifestErrors = validateManifestForSave(parsed);
      if (manifestErrors.length) {
        setManifestError(manifestErrors.join(" "));
        return;
      }
      const manifestId = parsed.id || slugify(parsed.name);
      const existing = draftExperiments.find(
        (item) =>
          item.apiId &&
          (item.id === manifestId ||
            item.experimentSlug === manifestId ||
            draftManifestKey(item.manifest) === manifestId),
      );
      const preview = estimateManifestPreview(parsed);
      await persistManifestLibraryRecords(parsed, library);
      const savedManifestChanged = existing ? manifestChanged(existing.manifest, parsed) : false;
      let apiId: number;
      let responseName = parsed.name;
      let responseSlug = manifestId;
      let responseStatus: DraftStatus = "draft";
      let responseProjectSlug = existing?.projectSlug;
      if (existing?.apiId) {
        apiId = existing.apiId;
        if (savedManifestChanged) {
          const updated = await updateExperimentDraft(apiId, parsed);
          responseName = updated.name || responseName;
          responseSlug = updated.slug || responseSlug;
          responseProjectSlug = updated.project_slug || responseProjectSlug;
        }
      } else {
        const created = await createExperimentDraft(parsed);
        apiId = created.id;
        responseName = created.name || responseName;
        responseSlug = created.slug || responseSlug;
        responseProjectSlug = created.project_slug || responseProjectSlug;
      }
      if (status === "queued") {
        const queued = await queueExperiment(apiId);
        responseName = queued.name || responseName;
        responseSlug = queued.slug || responseSlug;
        responseProjectSlug = queued.project_slug || responseProjectSlug;
        responseStatus = queued.status === "queued" ? "queued" : "draft";
      }
      const record: DraftExperimentRecord = {
        id: manifestId,
        apiId,
        experimentSlug: responseSlug,
        projectSlug: responseProjectSlug,
        name: responseName,
        manifest: parsed,
        preview,
        status: responseStatus,
      };
      upsertDraftExperiment(record);
      setManifestDirty(false);
    } catch (error) {
      setManifestError(error instanceof Error ? error.message : "Manifest JSON is invalid.");
    }
  }

  function upsertDraftExperiment(record: DraftExperimentRecord) {
    setDraftExperiments((current) => [record, ...current.filter((item) => item.id !== record.id)]);
  }

  function useMonitorExperiment(record: DraftExperimentRecord) {
    upsertDraftExperiment(record);
    setMode("experiment");
    setRoute("comparison");
  }

  function toggleDraftSelection(key: DraftSelectionKey, id: string, fallbackId?: string) {
    setDraft((current) => {
      const selected = current[key];
      if (!Array.isArray(selected)) {
        return current;
      }
      const isSelected = selected.some(
        (selectedId) => selectedId === id || selectedId === fallbackId,
      );
      return {
        ...current,
        [key]: isSelected
          ? selected.filter((selectedId) => selectedId !== id && selectedId !== fallbackId)
          : [...selected, id],
      };
    });
  }

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="Workspace navigation">
        <div className="brand">
          <span className="brand-mark">ME</span>
          <div>
            <strong>Model Eval</strong>
            <span>Context sensitivity lab</span>
          </div>
        </div>

        <div className="mode-switch" aria-label="Product mode">
          {(["playground", "experiment", "benchmark"] as ModeId[]).map((item) => (
            <button
              key={item}
              type="button"
              className={mode === item ? "selected" : ""}
              onClick={() => setMode(item)}
            >
              {modeLabel(item)}
            </button>
          ))}
        </div>

        <nav className="main-nav" aria-label="Experiment workspace">
          {routes.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={route === item.id ? "selected" : ""}
                onClick={() => {
                  setMode("experiment");
                  setRoute(item.id);
                }}
                title={item.label}
              >
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="workspace">
        <header className="workspace-header">
          <div>
            <span className="section-label">{modeLabel(mode)}</span>
            <h1>{mode === "experiment" ? routeTitle(route) : modeTitle(mode)}</h1>
          </div>
          <div className="header-actions">
            <Metric label="Runs" value={String(activePreview.logicalRuns)} />
            <Metric label="Attempts" value={String(activePreview.runAttempts)} />
            <Metric label="Cost" value={`$${activePreview.estimatedCostUsd.toFixed(2)}`} />
          </div>
        </header>

        {mode !== "experiment" ? (
          <ModePanel mode={mode} />
        ) : route === "library" ? (
          <LibraryScreen
            activeLibrary={activeLibrary}
            activeItems={activeItems}
            formError={formError}
            caseForm={caseForm}
            artifactForm={artifactForm}
            artifactPreprocessingParser={artifactPreprocessingParser}
            artifactPreprocessingPayload={artifactPreprocessingPayload}
            artifactPreprocessingRun={artifactPreprocessingRuns[artifactForm.id]}
            artifactPreprocessingHistory={artifactPreprocessingHistory[artifactForm.id] ?? []}
            artifactDerivedArtifacts={artifactDerivedArtifacts[artifactForm.id] ?? []}
            artifactPreprocessingError={artifactPreprocessingError}
            artifactPreprocessingBusy={artifactPreprocessingBusy}
            systemPromptForm={systemPromptForm}
            warmerForm={warmerForm}
            warmerMessagesText={warmerMessagesText}
            warmerTagsText={warmerTagsText}
            modelConfigForm={modelConfigForm}
            evaluatorForm={evaluatorForm}
            llmJudgeConfigForm={llmJudgeConfigForm}
            metricAdapterConfigForm={metricAdapterConfigForm}
            benchmarkSuiteForm={benchmarkSuiteForm}
            setActiveLibrary={setActiveLibrary}
            setCaseForm={setCaseForm}
            setArtifactForm={setArtifactForm}
            onArtifactInputModeChange={changeArtifactInputMode}
            setArtifactPreprocessingParser={(value) => {
              setArtifactPreprocessingParser(value);
              setArtifactPreprocessingPayload(prettyJson(defaultPreprocessingPayload(value)));
            }}
            setArtifactPreprocessingPayload={setArtifactPreprocessingPayload}
            setSystemPromptForm={setSystemPromptForm}
            setWarmerForm={setWarmerForm}
            setWarmerMessagesText={setWarmerMessagesText}
            setWarmerTagsText={setWarmerTagsText}
            setModelConfigForm={setModelConfigForm}
            setEvaluatorForm={setEvaluatorForm}
            setLLMJudgeConfigForm={setLLMJudgeConfigForm}
            setMetricAdapterConfigForm={setMetricAdapterConfigForm}
            setBenchmarkSuiteForm={setBenchmarkSuiteForm}
            onInspect={inspectLibraryItem}
            onSave={saveCurrentLibraryItem}
            onStartArtifactPreprocessing={runArtifactPreprocessing}
          />
        ) : route === "experiment" ? (
          <ExperimentBuilderScreen
            library={library}
            draft={draft}
            manifestText={manifestText}
            manifestError={manifestError}
            preview={activePreview}
            validationErrors={activeValidationErrors}
            setDraft={setDraft}
            setManifestText={(value) => {
              setManifestDirty(true);
              setManifestText(value);
            }}
            onRefreshManifest={refreshManifestEditor}
            onToggleSelection={toggleDraftSelection}
            onSave={() => saveDraftExperiment("draft")}
            onQueue={() => saveDraftExperiment("queued")}
          />
        ) : route === "monitor" ? (
          <RunMonitorScreen drafts={draftExperiments} onUseExperiment={useMonitorExperiment} />
        ) : route === "comparison" ? (
          <ComparisonScreen draft={selectedDraft} />
        ) : (
          <ResultsScreen draft={selectedDraft} library={library} />
        )}
      </section>
    </main>
  );
}

interface LibraryScreenProps {
  activeLibrary: LibraryKind;
  activeItems: LibraryState[LibraryKind];
  formError: string | null;
  caseForm: CaseRecord;
  artifactForm: ArtifactRecord;
  artifactPreprocessingParser: string;
  artifactPreprocessingPayload: string;
  artifactPreprocessingRun?: ApiArtifactPreprocessingRun;
  artifactPreprocessingHistory: ApiArtifactPreprocessingRun[];
  artifactDerivedArtifacts: ApiDerivedArtifact[];
  artifactPreprocessingError: string | null;
  artifactPreprocessingBusy: boolean;
  systemPromptForm: SystemPromptRecord;
  warmerForm: WarmerRecord;
  warmerMessagesText: string;
  warmerTagsText: string;
  modelConfigForm: ModelConfigRecord;
  evaluatorForm: EvaluatorRecord;
  llmJudgeConfigForm: LLMJudgeConfigRecord;
  metricAdapterConfigForm: MetricAdapterConfigRecord;
  benchmarkSuiteForm: BenchmarkSuiteRecord;
  setActiveLibrary: (value: LibraryKind) => void;
  setCaseForm: (value: CaseRecord) => void;
  setArtifactForm: (value: ArtifactRecord) => void;
  onArtifactInputModeChange: (inputMode: string) => void;
  setArtifactPreprocessingParser: (value: string) => void;
  setArtifactPreprocessingPayload: (value: string) => void;
  setSystemPromptForm: (value: SystemPromptRecord) => void;
  setWarmerForm: (value: WarmerRecord) => void;
  setWarmerMessagesText: (value: string) => void;
  setWarmerTagsText: (value: string) => void;
  setModelConfigForm: (value: ModelConfigRecord) => void;
  setEvaluatorForm: (value: EvaluatorRecord) => void;
  setLLMJudgeConfigForm: (value: LLMJudgeConfigRecord) => void;
  setMetricAdapterConfigForm: (value: MetricAdapterConfigRecord) => void;
  setBenchmarkSuiteForm: (value: BenchmarkSuiteRecord) => void;
  onInspect: (id: string) => void;
  onSave: () => void;
  onStartArtifactPreprocessing: () => void;
}

function LibraryScreen(props: LibraryScreenProps) {
  return (
    <div className="split-layout">
      <section className="panel library-list">
        <div className="tab-row" role="tablist" aria-label="Library tabs">
          {libraryTabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={props.activeLibrary === tab.id}
                className={props.activeLibrary === tab.id ? "selected" : ""}
                onClick={() => props.setActiveLibrary(tab.id)}
              >
                <Icon size={16} aria-hidden="true" />
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="item-table" aria-label={`${activeLibraryLabel(props.activeLibrary)} records`}>
          {props.activeItems.map((item, index) => {
            const itemKey =
              props.activeLibrary === "metricAdapterConfigs"
                ? `${metricAdapterConfigKey(item as MetricAdapterConfigRecord)}-${index}`
                : item.id;
            const inspectKey =
              props.activeLibrary === "metricAdapterConfigs"
                ? metricAdapterConfigKey(item as MetricAdapterConfigRecord)
                : itemKey;
            return (
              <button key={itemKey} type="button" onClick={() => props.onInspect(inspectKey)}>
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.id}</small>
                </span>
                <span>v{item.version}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="panel editor-panel">
        <div className="panel-heading">
          <div>
            <span className="section-label">{activeLibraryLabel(props.activeLibrary)}</span>
            <h2>Editor</h2>
          </div>
          <button type="button" onClick={props.onSave} title="Save library record">
            <Plus size={16} aria-hidden="true" />
            Save
          </button>
        </div>
        {props.formError ? <InlineError message={props.formError} /> : null}
        {props.activeLibrary === "cases" ? (
          <CaseEditor value={props.caseForm} onChange={props.setCaseForm} />
        ) : props.activeLibrary === "artifacts" ? (
          <ArtifactEditor
            value={props.artifactForm}
            preprocessingParser={props.artifactPreprocessingParser}
            preprocessingPayload={props.artifactPreprocessingPayload}
            preprocessingRun={props.artifactPreprocessingRun}
            preprocessingHistory={props.artifactPreprocessingHistory}
            derivedArtifacts={props.artifactDerivedArtifacts}
            preprocessingError={props.artifactPreprocessingError}
            preprocessingBusy={props.artifactPreprocessingBusy}
            onChange={props.setArtifactForm}
            onInputModeChange={props.onArtifactInputModeChange}
            onPreprocessingParserChange={props.setArtifactPreprocessingParser}
            onPreprocessingPayloadChange={props.setArtifactPreprocessingPayload}
            onStartPreprocessing={props.onStartArtifactPreprocessing}
          />
        ) : props.activeLibrary === "systemPrompts" ? (
          <SystemPromptEditor
            value={props.systemPromptForm}
            onChange={props.setSystemPromptForm}
          />
        ) : props.activeLibrary === "warmers" ? (
          <WarmerEditor
            value={props.warmerForm}
            messagesText={props.warmerMessagesText}
            tagsText={props.warmerTagsText}
            onChange={props.setWarmerForm}
            onMessagesChange={props.setWarmerMessagesText}
            onTagsChange={props.setWarmerTagsText}
          />
        ) : props.activeLibrary === "modelConfigs" ? (
          <ModelConfigEditor value={props.modelConfigForm} onChange={props.setModelConfigForm} />
        ) : props.activeLibrary === "evaluators" ? (
          <EvaluatorEditor value={props.evaluatorForm} onChange={props.setEvaluatorForm} />
        ) : props.activeLibrary === "llmJudgeConfigs" ? (
          <LLMJudgeConfigEditor
            value={props.llmJudgeConfigForm}
            onChange={props.setLLMJudgeConfigForm}
          />
        ) : props.activeLibrary === "metricAdapterConfigs" ? (
          <MetricAdapterConfigEditor
            value={props.metricAdapterConfigForm}
            onChange={props.setMetricAdapterConfigForm}
          />
        ) : (
          <BenchmarkSuiteEditor
            value={props.benchmarkSuiteForm}
            onChange={props.setBenchmarkSuiteForm}
          />
        )}
      </section>
    </div>
  );
}

interface ExperimentBuilderScreenProps {
  library: LibraryState;
  draft: ExperimentDraft;
  manifestText: string;
  manifestError: string | null;
  preview: ExperimentPreview;
  validationErrors: string[];
  setDraft: (value: ExperimentDraft | ((current: ExperimentDraft) => ExperimentDraft)) => void;
  setManifestText: (value: string) => void;
  onRefreshManifest: () => void;
  onToggleSelection: (key: DraftSelectionKey, id: string, fallbackId?: string) => void;
  onSave: () => void;
  onQueue: () => void;
}

function ExperimentBuilderScreen(props: ExperimentBuilderScreenProps) {
  return (
    <div className="builder-grid">
      <section className="panel builder-controls">
        <div className="panel-heading">
          <div>
            <span className="section-label">Full factorial</span>
            <h2>Dimensions</h2>
          </div>
          <span className="status-pill">Experiment</span>
        </div>

        <Field
          label="Experiment name"
          value={props.draft.name}
          onChange={(value) => props.setDraft((current) => ({ ...current, name: value }))}
        />
        <label className="field">
          <span>Design type</span>
          <select value="full_factorial" onChange={() => undefined}>
            <option value="full_factorial">Full factorial</option>
          </select>
        </label>
        <label className="field">
          <span>Benchmark suite</span>
          <select
            value={props.draft.benchmarkSuiteId ?? ""}
            onChange={(event) =>
              props.setDraft((current) =>
                applyBenchmarkSuiteSelection(
                  current,
                  props.library,
                  event.target.value,
                  current.suiteSplit ?? "",
                ),
              )
            }
          >
            <option value="">None</option>
            {props.library.benchmarkSuites.map((suite) => (
              <option key={suite.id} value={suite.id}>
                {suite.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Suite split</span>
          <select
            value={props.draft.suiteSplit ?? ""}
            onChange={(event) =>
              props.setDraft((current) =>
                applyBenchmarkSuiteSelection(
                  current,
                  props.library,
                  current.benchmarkSuiteId ?? "",
                  event.target.value as DatasetSplit | "",
                ),
              )
            }
          >
            <option value="">All active</option>
            <option value="dev">Dev</option>
            <option value="validation">Validation</option>
            <option value="holdout">Holdout</option>
          </select>
        </label>
        <SelectionGroup
          title="Cases"
          items={props.library.cases}
          selectedIds={props.draft.selectedCaseIds}
          onToggle={(id, fallbackId) => props.onToggleSelection("selectedCaseIds", id, fallbackId)}
        />
        <ArtifactSelectionGroup
          items={props.library.artifacts}
          selectedInputs={props.draft.selectedArtifactInputs ?? []}
          onToggle={(artifact) =>
            props.setDraft((current) => {
              const selectedInputs = current.selectedArtifactInputs ?? [];
              const selected = selectedInputs.some((item) =>
                artifactInputMatchesRecord(item, artifact),
              );
              return {
                ...current,
                selectedArtifactInputs: selected
                  ? selectedInputs.filter((item) => !artifactInputMatchesRecord(item, artifact))
                  : [
                      ...selectedInputs,
                      {
                        id: artifact.id,
                        version: artifact.version,
                        inputMode: artifact.inputMode || "direct_file",
                      },
                    ],
              };
            })
          }
          onInputModeChange={(artifact, inputMode) =>
            props.setDraft((current) => {
              const selectedInputs = current.selectedArtifactInputs ?? [];
              return {
                ...current,
                selectedArtifactInputs: selectedInputs.map((item) =>
                  artifactInputMatchesRecord(item, artifact) ? { ...item, inputMode } : item,
                ),
              };
            })
          }
        />
        <SelectionGroup
          title="Model configs"
          items={props.library.modelConfigs}
          selectedIds={props.draft.selectedModelConfigIds}
          onToggle={(id, fallbackId) =>
            props.onToggleSelection("selectedModelConfigIds", id, fallbackId)
          }
        />
        <SelectionGroup
          title="System prompts"
          items={props.library.systemPrompts}
          selectedIds={props.draft.selectedSystemPromptIds}
          onToggle={(id, fallbackId) =>
            props.onToggleSelection("selectedSystemPromptIds", id, fallbackId)
          }
        />
        <SelectionGroup
          title="Conversation warmers"
          items={props.library.warmers}
          selectedIds={props.draft.selectedWarmerIds}
          onToggle={(id, fallbackId) =>
            props.onToggleSelection("selectedWarmerIds", id, fallbackId)
          }
        />
        <SelectionGroup
          title="Evaluators"
          items={props.library.evaluators}
          selectedIds={props.draft.selectedEvaluatorIds}
          onToggle={(id, fallbackId) =>
            props.onToggleSelection("selectedEvaluatorIds", id, fallbackId)
          }
        />
      </section>

      <section className="panel preview-panel">
        <div className="panel-heading">
          <div>
            <span className="section-label">Preview</span>
            <h2>Run matrix</h2>
          </div>
          <button type="button" onClick={props.onRefreshManifest} title="Refresh manifest JSON">
            <FileText size={16} aria-hidden="true" />
            Refresh
          </button>
        </div>

        <div className="preview-metrics" role="region" aria-label="Run preview">
          <Metric label="Logical runs" value={String(props.preview.logicalRuns)} />
          <Metric label="Run attempts" value={String(props.preview.runAttempts)} />
          <Metric label="Tokens" value={props.preview.estimatedTokens.toLocaleString()} />
          <Metric label="Rough cost" value={`$${props.preview.estimatedCostUsd.toFixed(2)}`} />
        </div>

        <div className="controls-grid">
          <NumberField
            label="Replicates"
            value={props.draft.controls.replicates}
            min={1}
            onChange={(value) =>
              props.setDraft((current) => ({
                ...current,
                controls: { ...current.controls, replicates: value },
              }))
            }
          />
          <NumberField
            label="Max parallel"
            value={props.draft.controls.maxParallelRequests}
            min={1}
            onChange={(value) =>
              props.setDraft((current) => ({
                ...current,
                controls: { ...current.controls, maxParallelRequests: value },
              }))
            }
          />
          <NumberField
            label="Cost cap"
            value={props.draft.controls.maxTotalCostUsd}
            min={0}
            onChange={(value) =>
              props.setDraft((current) => ({
                ...current,
                controls: { ...current.controls, maxTotalCostUsd: value },
              }))
            }
          />
        </div>

        <div className="toggle-row" aria-label="Execution controls">
          <Toggle
            label="Retry failed"
            checked={props.draft.controls.retryFailed ?? true}
            onChange={(checked) =>
              props.setDraft((current) => ({
                ...current,
                controls: { ...current.controls, retryFailed: checked },
              }))
            }
          />
          <Toggle
            label="Cache calls"
            checked={props.draft.controls.cacheProviderCalls ?? true}
            onChange={(checked) =>
              props.setDraft((current) => ({
                ...current,
                controls: { ...current.controls, cacheProviderCalls: checked },
              }))
            }
          />
          <Toggle
            label="Local only"
            checked={props.draft.controls.localOnly ?? true}
            onChange={(checked) =>
              props.setDraft((current) => ({
                ...current,
                controls: { ...current.controls, localOnly: checked },
              }))
            }
          />
        </div>

        {props.validationErrors.length ? (
          <InlineError message={props.validationErrors.join(" ")} />
        ) : null}
        {props.manifestError ? <InlineError message={props.manifestError} /> : null}

        <label className="field">
          <span>Manifest JSON editor</span>
          <textarea
            className="code-editor manifest-editor"
            value={props.manifestText}
            onChange={(event) => props.setManifestText(event.target.value)}
            spellCheck={false}
          />
        </label>

        <div className="action-row">
          <button type="button" className="secondary" onClick={props.onSave}>
            <Save size={16} aria-hidden="true" />
            Save draft
          </button>
          <button type="button" onClick={props.onQueue}>
            <Play size={16} aria-hidden="true" />
            Queue run
          </button>
        </div>
      </section>
    </div>
  );
}

function CaseEditor({
  value,
  onChange,
}: {
  value: CaseRecord;
  onChange: (value: CaseRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <label className="field">
        <span>Dataset split</span>
        <select
          value={value.datasetSplit}
          onChange={(event) =>
            onChange({ ...value, datasetSplit: event.target.value as DatasetSplit })
          }
        >
          <option value="dev">Dev</option>
          <option value="validation">Validation</option>
          <option value="holdout">Holdout</option>
          <option value="archived">Archived</option>
        </select>
      </label>
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <label className="field wide">
        <span>Prompt text editor</span>
        <textarea
          className="code-editor"
          value={value.prompt}
          onChange={(event) => onChange({ ...value, prompt: event.target.value })}
        />
      </label>
    </div>
  );
}

function ArtifactEditor({
  value,
  preprocessingParser,
  preprocessingPayload,
  preprocessingRun,
  preprocessingHistory,
  derivedArtifacts,
  preprocessingError,
  preprocessingBusy,
  onChange,
  onInputModeChange,
  onPreprocessingParserChange,
  onPreprocessingPayloadChange,
  onStartPreprocessing,
}: {
  value: ArtifactRecord;
  preprocessingParser: string;
  preprocessingPayload: string;
  preprocessingRun?: ApiArtifactPreprocessingRun;
  preprocessingHistory: ApiArtifactPreprocessingRun[];
  derivedArtifacts: ApiDerivedArtifact[];
  preprocessingError: string | null;
  preprocessingBusy: boolean;
  onChange: (value: ArtifactRecord) => void;
  onInputModeChange: (inputMode: string) => void;
  onPreprocessingParserChange: (value: string) => void;
  onPreprocessingPayloadChange: (value: string) => void;
  onStartPreprocessing: () => void;
}) {
  return (
    <>
      <div className="form-grid">
        <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
        <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
        <Field
          label="Artifact type"
          value={value.artifactType}
          onChange={(artifactType) => onChange({ ...value, artifactType })}
        />
        <Field label="URI" value={value.uri} onChange={(uri) => onChange({ ...value, uri })} />
        <label className="field">
          <span>Input mode</span>
          <select
            value={value.inputMode}
            onChange={(event) => onInputModeChange(event.target.value)}
          >
            {artifactInputModes.map((mode) => (
              <option key={mode.value} value={mode.value}>
                {mode.label}
              </option>
            ))}
          </select>
        </label>
        <NumberField
          label="Version"
          value={value.version}
          min={1}
          onChange={(version) => onChange({ ...value, version })}
        />
        <label className="field wide">
          <span>Metadata JSON</span>
          <textarea
            className="code-editor"
            value={value.metadataJson}
            onChange={(event) => onChange({ ...value, metadataJson: event.target.value })}
            spellCheck={false}
          />
        </label>
      </div>

      <section className="preprocessing-panel" aria-label="Artifact preprocessing">
        <div className="panel-heading compact">
          <div>
            <span className="section-label">Local only</span>
            <h3>Preprocessing</h3>
          </div>
          <span className="status-pill">Local storage only</span>
        </div>
        {preprocessingError ? <InlineError message={preprocessingError} /> : null}
        <div className="preprocessing-controls">
          <label className="field">
            <span>Preprocessing parser</span>
            <select
              value={preprocessingParser}
              onChange={(event) => onPreprocessingParserChange(event.target.value)}
            >
              {preprocessingParsers.map((parser) => (
                <option key={parser.value} value={parser.value}>
                  {parser.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field wide">
            <span>Parser payload JSON</span>
            <textarea
              className="code-editor compact"
              value={preprocessingPayload}
              onChange={(event) => onPreprocessingPayloadChange(event.target.value)}
              spellCheck={false}
            />
          </label>
          <button type="button" onClick={onStartPreprocessing} disabled={preprocessingBusy}>
            <RefreshCw size={16} aria-hidden="true" />
            {preprocessingBusy ? "Starting" : "Start preprocessing"}
          </button>
        </div>
        <DerivedArtifactsList
          run={preprocessingRun}
          history={preprocessingHistory}
          derivedArtifacts={derivedArtifacts}
        />
      </section>
    </>
  );
}

function DerivedArtifactsList({
  run,
  history,
  derivedArtifacts,
}: {
  run?: ApiArtifactPreprocessingRun;
  history: ApiArtifactPreprocessingRun[];
  derivedArtifacts: ApiDerivedArtifact[];
}) {
  const artifacts = run?.derived_artifacts.length ? run.derived_artifacts : derivedArtifacts;
  if (!artifacts.length) {
    return (
      <>
        {run?.status === "failed" ? (
          <InlineError message={run.error_message ?? run.error_kind ?? "Preprocessing failed."} />
        ) : null}
        <div className="empty-state">No derived outputs available.</div>
      </>
    );
  }
  return (
    <>
      {run?.status === "failed" ? (
        <InlineError message={run.error_message ?? run.error_kind ?? "Preprocessing failed."} />
      ) : null}
      {history.length ? (
        <div className="preprocessing-history" aria-label="Preprocessing runs">
          {history.map((record) => (
            <span key={record.id}>
              {record.parser_name}: {record.status}
            </span>
          ))}
        </div>
      ) : null}
      <div className="derived-output-list" aria-label="Derived artifacts">
        {artifacts.map((artifact) => (
          <div key={`${artifact.id}-${artifact.slug}`} className="derived-output-row">
            <span>
              <strong>{artifact.slug}</strong>
              <small>
                {artifact.input_mode} · {artifact.artifact_type ?? "artifact"}
              </small>
            </span>
            <code>{prettyJson(artifact.metadata)}</code>
          </div>
        ))}
      </div>
    </>
  );
}

function SystemPromptEditor({
  value,
  onChange,
}: {
  value: SystemPromptRecord;
  onChange: (value: SystemPromptRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <label className="field wide">
        <span>System prompt text editor</span>
        <textarea
          className="code-editor"
          value={value.prompt}
          onChange={(event) => onChange({ ...value, prompt: event.target.value })}
        />
      </label>
    </div>
  );
}

function WarmerEditor({
  value,
  messagesText,
  tagsText,
  onChange,
  onMessagesChange,
  onTagsChange,
}: {
  value: WarmerRecord;
  messagesText: string;
  tagsText: string;
  onChange: (value: WarmerRecord) => void;
  onMessagesChange: (value: string) => void;
  onTagsChange: (value: string) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <Field
        label="Domain"
        value={value.domain}
        onChange={(domain) => onChange({ ...value, domain })}
      />
      <Field
        label="User level"
        value={value.userLevel}
        onChange={(userLevel) => onChange({ ...value, userLevel })}
      />
      <Field
        label="Intent"
        value={value.intent}
        onChange={(intent) => onChange({ ...value, intent })}
      />
      <Field label="Tags" value={tagsText} onChange={onTagsChange} />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <Field
        label="Version note"
        value={value.versionNote}
        onChange={(versionNote) => onChange({ ...value, versionNote })}
      />
      <label className="field wide">
        <span>Messages JSON editor</span>
        <textarea
          className="code-editor"
          value={messagesText}
          onChange={(event) => onMessagesChange(event.target.value)}
          spellCheck={false}
        />
      </label>
    </div>
  );
}

function ModelConfigEditor({
  value,
  onChange,
}: {
  value: ModelConfigRecord;
  onChange: (value: ModelConfigRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <Field
        label="Provider"
        value={value.provider}
        onChange={(provider) => onChange({ ...value, provider })}
      />
      <Field label="Model" value={value.model} onChange={(model) => onChange({ ...value, model })} />
      <label className="field">
        <span>Reasoning level</span>
        <select
          value={value.reasoningLevel}
          onChange={(event) =>
            onChange({ ...value, reasoningLevel: event.target.value as ModelConfigRecord["reasoningLevel"] })
          }
        >
          <option value="none">none</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </label>
      <NumberField
        label="Temperature"
        value={value.temperature}
        min={0}
        step={0.1}
        onChange={(temperature) => onChange({ ...value, temperature })}
      />
      <NumberField
        label="Max output tokens"
        value={value.maxOutputTokens}
        min={1}
        onChange={(maxOutputTokens) => onChange({ ...value, maxOutputTokens })}
      />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <div className="field wide capability-grid">
        <span>Capability flags</span>
        <Toggle
          label="Images"
          checked={value.supportsImages}
          onChange={(supportsImages) => onChange({ ...value, supportsImages })}
        />
        <Toggle
          label="Files"
          checked={value.supportsFiles}
          onChange={(supportsFiles) => onChange({ ...value, supportsFiles })}
        />
        <Toggle
          label="Tools"
          checked={value.supportsTools}
          onChange={(supportsTools) => onChange({ ...value, supportsTools })}
        />
        <Toggle
          label="JSON schema"
          checked={value.supportsJsonSchema}
          onChange={(supportsJsonSchema) => onChange({ ...value, supportsJsonSchema })}
        />
      </div>
      <label className="field wide">
        <span>Raw provider params JSON</span>
        <textarea
          className="code-editor"
          value={value.rawProviderParamsJson}
          onChange={(event) => onChange({ ...value, rawProviderParamsJson: event.target.value })}
          spellCheck={false}
        />
      </label>
    </div>
  );
}

function EvaluatorEditor({
  value,
  onChange,
}: {
  value: EvaluatorRecord;
  onChange: (value: EvaluatorRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <Field
        label="Type"
        value={value.evaluatorType}
        onChange={(evaluatorType) => onChange({ ...value, evaluatorType })}
      />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <label className="field wide">
        <span>Definition JSON</span>
        <textarea
          className="code-editor"
          value={value.definitionJson}
          onChange={(event) => onChange({ ...value, definitionJson: event.target.value })}
          spellCheck={false}
        />
      </label>
    </div>
  );
}

function LLMJudgeConfigEditor({
  value,
  onChange,
}: {
  value: LLMJudgeConfigRecord;
  onChange: (value: LLMJudgeConfigRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <Field
        label="Judge model config"
        value={value.judgeModelConfigSlug}
        onChange={(judgeModelConfigSlug) => onChange({ ...value, judgeModelConfigSlug })}
      />
      <Field
        label="Calibration status"
        value={value.calibrationStatus}
        onChange={(calibrationStatus) => onChange({ ...value, calibrationStatus })}
      />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <label className="field wide">
        <span>Judge prompt</span>
        <textarea
          className="code-editor"
          value={value.judgePrompt}
          onChange={(event) => onChange({ ...value, judgePrompt: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Rubric dimensions JSON</span>
        <textarea
          className="code-editor"
          value={value.rubricDimensionsJson}
          onChange={(event) => onChange({ ...value, rubricDimensionsJson: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Output schema JSON</span>
        <textarea
          className="code-editor"
          value={value.outputSchemaJson}
          onChange={(event) => onChange({ ...value, outputSchemaJson: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Raw provider params JSON</span>
        <textarea
          className="code-editor"
          value={value.rawProviderParamsJson}
          onChange={(event) => onChange({ ...value, rawProviderParamsJson: event.target.value })}
          spellCheck={false}
        />
      </label>
    </div>
  );
}

function MetricAdapterConfigEditor({
  value,
  onChange,
}: {
  value: MetricAdapterConfigRecord;
  onChange: (value: MetricAdapterConfigRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <label className="field">
        <span>Adapter kind</span>
        <select
          value={value.adapterKind}
          onChange={(event) => onChange({ ...value, adapterKind: event.target.value })}
        >
          <option value="retrieval_precision">retrieval_precision</option>
          <option value="citation_coverage">citation_coverage</option>
          <option value="groundedness_checklist">groundedness_checklist</option>
          <option value="answer_relevance">answer_relevance</option>
        </select>
      </label>
      <Field
        label="Adapter version"
        value={value.adapterVersion}
        onChange={(adapterVersion) => onChange({ ...value, adapterVersion })}
      />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <div className="field">
        <span>Execution mode</span>
        <Toggle
          label="Local only"
          checked={value.localOnly}
          onChange={(localOnly) => onChange({ ...value, localOnly })}
        />
      </div>
      <label className="field wide">
        <span>Required inputs</span>
        <textarea
          className="code-editor compact-editor"
          value={value.requiredInputsText}
          onChange={(event) => onChange({ ...value, requiredInputsText: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Output schema JSON</span>
        <textarea
          className="code-editor"
          value={value.outputSchemaJson}
          onChange={(event) => onChange({ ...value, outputSchemaJson: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Capability metadata JSON</span>
        <textarea
          className="code-editor"
          value={value.capabilityMetadataJson}
          onChange={(event) => onChange({ ...value, capabilityMetadataJson: event.target.value })}
          spellCheck={false}
        />
      </label>
    </div>
  );
}

function BenchmarkSuiteEditor({
  value,
  onChange,
}: {
  value: BenchmarkSuiteRecord;
  onChange: (value: BenchmarkSuiteRecord) => void;
}) {
  return (
    <div className="form-grid">
      <Field label="Name" value={value.name} onChange={(name) => onChange({ ...value, name })} />
      <Field label="Slug" value={value.id} onChange={(id) => onChange({ ...value, id })} />
      <NumberField
        label="Version"
        value={value.version}
        min={1}
        onChange={(version) => onChange({ ...value, version })}
      />
      <Field
        label="Description"
        value={value.description}
        onChange={(description) => onChange({ ...value, description })}
      />
      <label className="field wide">
        <span>Case IDs</span>
        <textarea
          className="code-editor compact-editor"
          value={value.caseIdsText}
          onChange={(event) => onChange({ ...value, caseIdsText: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Model config IDs</span>
        <textarea
          className="code-editor compact-editor"
          value={value.modelConfigIdsText}
          onChange={(event) => onChange({ ...value, modelConfigIdsText: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>System prompt IDs</span>
        <textarea
          className="code-editor compact-editor"
          value={value.systemPromptIdsText}
          onChange={(event) => onChange({ ...value, systemPromptIdsText: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Warmer IDs</span>
        <textarea
          className="code-editor compact-editor"
          value={value.warmerIdsText}
          onChange={(event) => onChange({ ...value, warmerIdsText: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Evaluator IDs</span>
        <textarea
          className="code-editor compact-editor"
          value={value.evaluatorIdsText}
          onChange={(event) => onChange({ ...value, evaluatorIdsText: event.target.value })}
          spellCheck={false}
        />
      </label>
      <label className="field wide">
        <span>Controls JSON</span>
        <textarea
          className="code-editor"
          value={value.controlsJson}
          onChange={(event) => onChange({ ...value, controlsJson: event.target.value })}
          spellCheck={false}
        />
      </label>
    </div>
  );
}

function SelectionGroup({
  title,
  items,
  selectedIds,
  onToggle,
}: {
  title: string;
  items: Array<{ id: string; name: string; version: number }>;
  selectedIds: string[];
  onToggle: (id: string, fallbackId?: string) => void;
}) {
  return (
    <section className="selector">
      <h3>{title}</h3>
      <div className="selector-list">
        {items.map((item) => {
          const selectionKey = libraryReferenceKey(item);
          return (
            <label key={selectionKey} className="check-row">
              <input
                type="checkbox"
                checked={selectedIds.includes(selectionKey) || selectedIds.includes(item.id)}
                onChange={() => onToggle(selectionKey, item.id)}
              />
              <span>
                <strong>{item.name}</strong>
                <small>
                  {item.id} · v{item.version}
                </small>
              </span>
            </label>
          );
        })}
      </div>
    </section>
  );
}

function ArtifactSelectionGroup({
  items,
  selectedInputs,
  onToggle,
  onInputModeChange,
}: {
  items: ArtifactRecord[];
  selectedInputs: SelectedArtifactInput[];
  onToggle: (item: ArtifactRecord) => void;
  onInputModeChange: (artifact: ArtifactRecord, inputMode: string) => void;
}) {
  return (
    <section className="selector">
      <h3>Artifacts</h3>
      <div className="selector-list">
        {items.map((item) => {
          const selectedInputMode = selectedInputs.find((input) =>
            artifactInputMatchesRecord(input, item),
          )?.inputMode;
          return (
            <div key={libraryReferenceKey(item)} className="artifact-input-row">
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={selectedInputMode !== undefined}
                  onChange={() => onToggle(item)}
                />
                <span>
                  <strong>{item.name}</strong>
                  <small>
                    {item.id} · v{item.version}
                  </small>
                </span>
              </label>
              {selectedInputMode !== undefined ? (
                <label className="field">
                  <span>{item.name} input mode</span>
                  <select
                    value={selectedInputMode}
                    onChange={(event) => onInputModeChange(item, event.target.value)}
                  >
                    {artifactInputModes.map((mode) => (
                      <option key={mode.value} value={mode.value}>
                        {mode.label}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

interface MonitorRow {
  experiment: ApiMonitorExperiment;
  run: ApiMonitorRun;
  attempts: ApiRunAttempt[];
  source: "api" | "local";
  runCount: number;
  attemptCount: number;
  costUsd: number;
}

interface MonitorFilters {
  caseSlug: string;
  modelSlug: string;
  promptSlug: string;
  warmerSlug: string;
  status: string;
  failureReason: string;
}

const emptyMonitorFilters: MonitorFilters = {
  caseSlug: "all",
  modelSlug: "all",
  promptSlug: "all",
  warmerSlug: "all",
  status: "all",
  failureReason: "all",
};

function RunMonitorScreen({
  drafts,
  onUseExperiment,
}: {
  drafts: DraftExperimentRecord[];
  onUseExperiment: (record: DraftExperimentRecord) => void;
}) {
  const [rows, setRows] = useState<MonitorRow[]>(() => draftMonitorRows(drafts));
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [filters, setFilters] = useState<MonitorFilters>(emptyMonitorFilters);
  const [selectedRow, setSelectedRow] = useState<MonitorRow | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setActionError(null);
      try {
        const loadedRows = await loadMonitorRows();
        if (active) {
          setRows(loadedRows.length ? loadedRows : draftMonitorRows(drafts));
        }
      } catch {
        if (active) {
          setRows(draftMonitorRows(drafts));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [drafts, reloadKey]);

  const filteredRows = useMemo(
    () => rows.filter((row) => monitorRowMatchesFilters(row, filters)),
    [rows, filters],
  );
  const summary = useMemo(() => summarizeMonitorRows(rows), [rows]);
  const filterOptions = useMemo(() => monitorFilterOptions(rows), [rows]);
  const safeguards = useMemo(() => monitorSafeguards(rows), [rows]);
  const experiments = useMemo(() => uniqueExperiments(rows), [rows]);

  async function retryRow(row: MonitorRow) {
    if (row.source !== "api") return;
    setActionError(null);
    try {
      await retryFailedRun(row.run.id);
      setReloadKey((current) => current + 1);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not retry failed run.");
    }
  }

  async function cancelExperiment(row: MonitorRow) {
    if (row.source !== "api") return;
    setActionError(null);
    try {
      await cancelMonitorExperiment(row.experiment.id);
      setReloadKey((current) => current + 1);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not cancel experiment.");
    }
  }

  return (
    <div className="monitor-layout">
      <section className="monitor-summary">
        <Metric label="Total runs" value={summary.totalRuns.toLocaleString()} />
        <Metric label="Completed runs" value={summary.completedRuns.toLocaleString()} />
        <Metric label="Failed runs" value={summary.failedRuns.toLocaleString()} />
        <Metric label="Total attempts" value={summary.totalAttempts.toLocaleString()} />
        <Metric label="Total cost" value={`$${summary.totalCostUsd.toFixed(2)}`} />
        <Metric label="Average latency" value={formatLatency(summary.averageLatencyMs)} />
        <Metric label="Remaining queued" value={summary.remainingQueued.toLocaleString()} />
      </section>

      {safeguards.length ? (
        <section className="safeguard-list" aria-label="Execution safeguards">
          {safeguards.map((item) => (
            <div key={`${item.row.run.id}-${item.message}`} className="safeguard-alert">
              <Ban size={16} aria-hidden="true" />
              <span>{item.message}</span>
              <small>
                {item.row.run.case_slug} · {item.row.run.model_config_slug}
              </small>
            </div>
          ))}
        </section>
      ) : null}

      <section className="panel monitor-panel">
        <div className="panel-heading">
          <div>
            <span className="section-label">Live execution</span>
            <h2>Run Monitor</h2>
          </div>
          <div className="monitor-actions">
            {experiments.map((experiment) => (
              <span key={experiment.id} className="experiment-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => cancelExperiment(experiment.row)}
                  title={`Cancel experiment ${experiment.name}`}
                  aria-label={`Cancel experiment ${experiment.name}`}
                  disabled={experiment.row.source !== "api"}
                >
                  <Ban size={16} aria-hidden="true" />
                  Cancel
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => onUseExperiment(monitorDraftRecord(experiment.row, rows))}
                  title={`Use ${experiment.name} for review and results`}
                  aria-label={`Use ${experiment.name} for review and results`}
                  disabled={
                    experiment.row.source !== "api" || experiment.row.experiment.status !== "complete"
                  }
                >
                  <GitCompareArrows size={16} aria-hidden="true" />
                  Use
                </button>
              </span>
            ))}
            <button
              type="button"
              className="secondary"
              onClick={() => setReloadKey((current) => current + 1)}
              title="Refresh monitor"
            >
              <RefreshCw size={16} aria-hidden="true" />
              Refresh
            </button>
          </div>
        </div>
        {actionError ? <InlineError message={actionError} /> : null}

        <div className="monitor-filters">
          <MonitorFilter
            label="Case"
            value={filters.caseSlug}
            options={filterOptions.caseSlugs}
            onChange={(caseSlug) => setFilters((current) => ({ ...current, caseSlug }))}
          />
          <MonitorFilter
            label="Model"
            value={filters.modelSlug}
            options={filterOptions.modelSlugs}
            onChange={(modelSlug) => setFilters((current) => ({ ...current, modelSlug }))}
          />
          <MonitorFilter
            label="Prompt"
            value={filters.promptSlug}
            options={filterOptions.promptSlugs}
            onChange={(promptSlug) => setFilters((current) => ({ ...current, promptSlug }))}
          />
          <MonitorFilter
            label="Warmer"
            value={filters.warmerSlug}
            options={filterOptions.warmerSlugs}
            onChange={(warmerSlug) => setFilters((current) => ({ ...current, warmerSlug }))}
          />
          <MonitorFilter
            label="Status"
            value={filters.status}
            options={filterOptions.statuses}
            onChange={(status) => setFilters((current) => ({ ...current, status }))}
          />
          <MonitorFilter
            label="Failure reason"
            value={filters.failureReason}
            options={filterOptions.failureReasons}
            onChange={(failureReason) =>
              setFilters((current) => ({ ...current, failureReason }))
            }
          />
        </div>

        {loading ? (
          <div className="empty-state">Loading monitor data.</div>
        ) : (
          <div className="run-table monitor-table" role="region" aria-label="Run Monitor table">
            <div className="table-head">
              <span>Experiment</span>
              <span>Case</span>
              <span>Model</span>
              <span>Prompt</span>
              <span>Warmer</span>
              <span>Status</span>
              <span>Runs</span>
              <span>Attempts</span>
              <span>Cost</span>
              <span>Latency</span>
              <span>Failure</span>
              <span>Actions</span>
            </div>
            {filteredRows.length ? (
              filteredRows.map((row) => {
                const latest = latestAttempt(row);
                const failure = failureReason(row);
                return (
                  <div key={`${row.source}-${row.run.id}`} className="table-row">
                    <span>{row.experiment.name}</span>
                    <span>{row.run.case_slug}</span>
                    <span>{row.run.model_config_slug}</span>
                    <span>{row.run.system_prompt_slug}</span>
                    <span>{row.run.warmer_slug}</span>
                    <span className={`status-pill ${row.run.status}`}>{row.run.status}</span>
                    <span>{row.runCount}</span>
                    <span>{row.attemptCount}</span>
                    <span>${row.costUsd.toFixed(2)}</span>
                    <span>{formatLatency(latest?.latency_ms ?? null)}</span>
                    <span>{failure || "none"}</span>
                    <span className="row-actions">
                      <button
                        type="button"
                        className="secondary icon-button"
                        onClick={() => setSelectedRow(row)}
                        title={`Inspect run ${row.run.case_slug}`}
                        aria-label={`Inspect run ${row.run.case_slug}`}
                      >
                        <FileText size={16} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="secondary icon-button"
                        onClick={() => retryRow(row)}
                        title={`Retry failed run ${row.run.case_slug}`}
                        aria-label={`Retry failed run ${row.run.case_slug}`}
                        disabled={row.source !== "api" || row.run.status !== "failed"}
                      >
                        <RotateCcw size={16} aria-hidden="true" />
                      </button>
                    </span>
                  </div>
                );
              })
            ) : (
              <div className="empty-state">No runs match the current monitor filters.</div>
            )}
          </div>
        )}
      </section>

      {selectedRow ? (
        <AttemptDetailDrawer row={selectedRow} onClose={() => setSelectedRow(null)} />
      ) : null}
    </div>
  );
}

function MonitorFilter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="all">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {filterOptionLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function AttemptDetailDrawer({ row, onClose }: { row: MonitorRow; onClose: () => void }) {
  const latest = latestAttempt(row);
  return (
    <aside className="attempt-drawer" role="dialog" aria-label="Attempt details">
      <div className="panel-heading">
        <div>
          <span className="section-label">{row.run.case_slug}</span>
          <h2>Attempt details</h2>
        </div>
        <button type="button" className="secondary icon-button" onClick={onClose} title="Close">
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="detail-grid">
        <Metric label="Attempt" value={latest?.attempt_id ?? "none"} />
        <Metric label="Status" value={latest?.status ?? row.run.status} />
        <Metric label="Provider response" value={latest?.provider_response_id ?? "none"} />
        <Metric label="Latency" value={formatLatency(latest?.latency_ms ?? null)} />
        <Metric label="Tokens" value={(latest?.total_tokens ?? 0).toLocaleString()} />
        <Metric label="Cost" value={`$${(latest?.cost_usd ?? 0).toFixed(2)}`} />
      </div>

      <section className="detail-section">
        <h3>Timing</h3>
        <dl>
          <dt>Started</dt>
          <dd>{latest?.started_at ?? "not started"}</dd>
          <dt>Completed</dt>
          <dd>{latest?.completed_at ?? "not complete"}</dd>
        </dl>
      </section>

      <section className="detail-section">
        <h3>Token usage</h3>
        <dl>
          <dt>Input</dt>
          <dd>{(latest?.input_tokens ?? 0).toLocaleString()}</dd>
          <dt>Output</dt>
          <dd>{(latest?.output_tokens ?? 0).toLocaleString()}</dd>
          <dt>Total</dt>
          <dd>{(latest?.total_tokens ?? 0).toLocaleString()} total tokens</dd>
        </dl>
      </section>

      <section className="detail-section">
        <h3>Error details</h3>
        <dl>
          <dt>Kind</dt>
          <dd>{latest?.error_kind ?? "none"}</dd>
          <dt>Message</dt>
          <dd>{latest?.error_message ?? "none"}</dd>
          <dt>Terminal reason</dt>
          <dd>{latest?.terminal_failure_reason ?? "none"}</dd>
        </dl>
      </section>

      <section className="detail-section">
        <h3>Request metadata</h3>
        <pre className="code-editor detail-json">
          {prettyJson(latest?.request_payload ?? {})}
        </pre>
      </section>

      <section className="detail-section">
        <h3>Response metadata</h3>
        <pre className="code-editor detail-json">
          {prettyJson(latest?.response_payload ?? {})}
        </pre>
      </section>
    </aside>
  );
}

async function loadMonitorRows(): Promise<MonitorRow[]> {
  const experiments = await listMonitorExperiments();
  if (!Array.isArray(experiments)) {
    throw new Error("Monitor experiment payload is invalid.");
  }
  const runGroups = await Promise.all(
    experiments.map(async (experiment) => ({
      experiment,
      runs: await listMonitorRuns(experiment.id),
    })),
  );
  const rowInputs = runGroups.flatMap(({ experiment, runs }) =>
    Array.isArray(runs) ? runs.map((run) => ({ experiment, run })) : [],
  );
  const attempts = await Promise.all(
    rowInputs.map(async ({ run }) => {
      try {
        const response = await listRunAttempts(run.id);
        return Array.isArray(response) ? response : [];
      } catch {
        return [];
      }
    }),
  );
  return rowInputs.map(({ experiment, run }, index) => {
    const rowAttempts = attempts[index] ?? [];
    return {
      experiment,
      run,
      attempts: rowAttempts,
      source: "api",
      runCount: 1,
      attemptCount: rowAttempts.length,
      costUsd: sumAttemptCost(rowAttempts),
    };
  });
}

function draftMonitorRows(drafts: DraftExperimentRecord[]): MonitorRow[] {
  return drafts.map((draft, index) => ({
    experiment: {
      id: draft.apiId ?? -(index + 1),
      slug: draft.experimentSlug ?? draft.id,
      name: draft.name,
      status: draft.status,
      created_at: null,
    },
    run: {
      id: draft.apiId ?? -(index + 1),
      run_id: draft.experimentSlug ?? draft.id,
      experiment_id: draft.apiId ?? -(index + 1),
      case_slug: "browser-session",
      model_config_slug: "draft-manifest",
      system_prompt_slug: "draft-manifest",
      warmer_slug: "draft-manifest",
      status: draft.status,
    },
    attempts: [],
    source: "local",
    runCount: draft.preview.logicalRuns,
    attemptCount: draft.preview.runAttempts,
    costUsd: draft.preview.estimatedCostUsd,
  }));
}

function monitorDraftRecord(row: MonitorRow, rows: MonitorRow[]): DraftExperimentRecord {
  const experimentRows = rows.filter((item) => item.experiment.id === row.experiment.id);
  const manifest = buildExperimentManifest(initialExperimentDraft);
  const projectSlug = row.experiment.project_slug ?? "default";
  return {
    id: `${projectSlug}:${row.experiment.slug}`,
    apiId: row.experiment.id,
    experimentSlug: row.experiment.slug,
    projectSlug,
    name: row.experiment.name,
    manifest: { ...manifest, id: row.experiment.slug, name: row.experiment.name },
    preview: {
      logicalRuns: experimentRows.reduce((total, item) => total + item.runCount, 0),
      runAttempts: experimentRows.reduce((total, item) => total + item.attemptCount, 0),
      estimatedTokens: experimentRows.reduce(
        (total, item) =>
          total +
          item.attempts.reduce(
            (attemptTotal, attempt) => attemptTotal + (attempt.total_tokens ?? 0),
            0,
          ),
        0,
      ),
      estimatedCostUsd: experimentRows.reduce((total, item) => total + item.costUsd, 0),
    },
    status: monitorDraftStatus(row.experiment.status),
  };
}

function monitorDraftStatus(status: string): DraftStatus {
  if (
    status === "queued" ||
    status === "running" ||
    status === "complete" ||
    status === "failed" ||
    status === "canceled" ||
    status === "skipped"
  ) {
    return status;
  }
  return "draft";
}

function monitorRowMatchesFilters(row: MonitorRow, filters: MonitorFilters): boolean {
  return (
    matchesFilter(row.run.case_slug, filters.caseSlug) &&
    matchesFilter(row.run.model_config_slug, filters.modelSlug) &&
    matchesFilter(row.run.system_prompt_slug, filters.promptSlug) &&
    matchesFilter(row.run.warmer_slug, filters.warmerSlug) &&
    matchesFilter(row.run.status, filters.status) &&
    matchesFilter(failureReason(row), filters.failureReason)
  );
}

function matchesFilter(value: string | null, filter: string): boolean {
  return filter === "all" || value === filter;
}

function monitorFilterOptions(rows: MonitorRow[]) {
  return {
    caseSlugs: sortedUnique(rows.map((row) => row.run.case_slug)),
    modelSlugs: sortedUnique(rows.map((row) => row.run.model_config_slug)),
    promptSlugs: sortedUnique(rows.map((row) => row.run.system_prompt_slug)),
    warmerSlugs: sortedUnique(rows.map((row) => row.run.warmer_slug)),
    statuses: sortedUnique(rows.map((row) => row.run.status)),
    failureReasons: sortedUnique(rows.map(failureReason).filter(Boolean)),
  };
}

function sortedUnique(values: string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function summarizeMonitorRows(rows: MonitorRow[]) {
  const latencies = rows
    .map((row) => latestAttempt(row)?.latency_ms)
    .filter((value): value is number => typeof value === "number");
  return {
    totalRuns: rows.reduce((total, row) => total + row.runCount, 0),
    completedRuns: sumRunsByStatus(rows, ["complete"]),
    failedRuns: sumRunsByStatus(rows, ["failed"]),
    totalAttempts: rows.reduce((total, row) => total + row.attemptCount, 0),
    totalCostUsd: rows.reduce((total, row) => total + row.costUsd, 0),
    averageLatencyMs: latencies.length
      ? latencies.reduce((total, value) => total + value, 0) / latencies.length
      : null,
    remainingQueued: sumRunsByStatus(rows, ["pending", "queued"]),
  };
}

function sumRunsByStatus(rows: MonitorRow[], statuses: string[]): number {
  const statusSet = new Set(statuses);
  return rows.reduce((total, row) => total + (statusSet.has(row.run.status) ? row.runCount : 0), 0);
}

function monitorSafeguards(rows: MonitorRow[]) {
  return rows.flatMap((row) => {
    const latest = latestAttempt(row);
    if (!latest) return [];
    if (latest.terminal_failure_reason === "cost_cap_exceeded") {
      return [{ row, message: "Cost cap exceeded before provider call." }];
    }
    if (
      latest.error_kind === "blocked_by_config" &&
      latest.terminal_failure_reason !== "cost_cap_exceeded"
    ) {
      return [{ row, message: "Provider is blocked by allow/deny configuration." }];
    }
    return [];
  });
}

function uniqueExperiments(rows: MonitorRow[]) {
  const byId = new Map<number, { id: number; name: string; row: MonitorRow }>();
  for (const row of rows) {
    if (!byId.has(row.experiment.id)) {
      byId.set(row.experiment.id, { id: row.experiment.id, name: row.experiment.name, row });
    }
  }
  return [...byId.values()];
}

function latestAttempt(row: MonitorRow): ApiRunAttempt | undefined {
  return row.attempts.reduce<ApiRunAttempt | undefined>((latest, attempt) => {
    if (!latest) {
      return attempt;
    }
    if (attempt.attempt_number !== latest.attempt_number) {
      return attempt.attempt_number > latest.attempt_number ? attempt : latest;
    }
    return attempt.id > latest.id ? attempt : latest;
  }, undefined);
}

function failureReason(row: MonitorRow): string {
  const latest = latestAttempt(row);
  return latest?.terminal_failure_reason || latest?.error_kind || latest?.error_message || "";
}

function sumAttemptCost(attempts: ApiRunAttempt[]): number {
  return attempts.reduce((total, attempt) => total + (attempt.cost_usd ?? 0), 0);
}

function formatLatency(value: number | null): string {
  if (value === null) return "n/a";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function filterOptionLabel(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

type ReviewWinner = "A" | "B" | "tie" | "cannot_judge";
type ReviewPassFail = Record<string, boolean | undefined>;
type ReviewFailureTags = Record<string, string[]>;
type ReviewMessage = { tone: "error" | "success"; text: string };

function ComparisonScreen({ draft }: { draft?: DraftExperimentRecord }) {
  const [reviewSet, setReviewSet] = useState<ApiReviewSet | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [winner, setWinner] = useState<ReviewWinner>("cannot_judge");
  const [passFail, setPassFail] = useState<ReviewPassFail>({});
  const [failureTags, setFailureTags] = useState<ReviewFailureTags>({});
  const [notes, setNotes] = useState("");
  const [reviewerSlug, setReviewerSlug] = useState(getReviewerId);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<ReviewMessage | null>(null);
  const selectedItem = reviewSet?.items[selectedIndex];
  const itemCount = reviewSet?.items.length ?? 0;
  const availableFailureTags =
    reviewSet?.metadata.failure_taxonomy?.tags ?? reviewSet?.metadata.failure_tags ?? [];
  const reviewAnswers = selectedItem?.answers ?? [{ label: "A" }, { label: "B" }];

  useEffect(() => {
    setSelectedIndex(0);
    resetReviewForm();
  }, [reviewSet?.id]);

  async function createBlindReviewSet() {
    if (!draft?.apiId) return;
    setLoading(true);
    setMessage(null);
    const slug = `${draft.experimentSlug ?? draft.id}-human-review`;
    const reviewer = reviewerSlug.trim() || getReviewerId();
    try {
      try {
        await createReviewer({ slug: reviewer, name: reviewer }, draft.projectSlug);
      } catch (error) {
        if (!isConflict(error)) throw error;
      }
      const created = await createReviewSetFromExperiment(draft.apiId, {
        slug,
        name: `${draft.name} human review`,
        reviewer_slugs: [reviewer],
      }, draft.projectSlug);
      setReviewSet(await reviewSetWithReviewerQueue(created, reviewer));
    } catch (error) {
      if (isConflict(error)) {
        try {
          const existing = await getReviewSetForExperiment(draft.apiId, slug, draft.projectSlug);
          if (existing) {
            setReviewSet(await reviewSetWithReviewerQueue(existing, reviewer));
            return;
          }
        } catch {
          // Fall through to the original create error below.
        }
      }
      setMessage(reviewErrorMessage(error, "Could not create review set."));
    } finally {
      setLoading(false);
    }
  }

  async function loadReviewerQueue() {
    if (!reviewSet) return;
    setLoading(true);
    setMessage(null);
    try {
      const reviewer = reviewerSlug.trim();
      await ensureReviewerAssignments(reviewSet.id, reviewer, draft?.projectSlug);
      setReviewSet(await reviewSetWithReviewerQueue(reviewSet, reviewer));
      setSelectedIndex(0);
      resetReviewForm();
    } catch (error) {
      setMessage(reviewErrorMessage(error, "Could not load reviewer queue."));
    } finally {
      setLoading(false);
    }
  }

  async function revealMetadata() {
    if (!reviewSet) return;
    setLoading(true);
    setMessage(null);
    try {
      const revealed = await getReviewSet(reviewSet.id, { revealMetadata: true });
      setReviewSet(mergeReviewMetadata(reviewSet, revealed));
    } catch (error) {
      setMessage(reviewErrorMessage(error, "Could not reveal metadata."));
    } finally {
      setLoading(false);
    }
  }

  async function submitDecision() {
    if (!selectedItem) return;
    setLoading(true);
    setMessage(null);
    try {
      const decision = {
        reviewer_id: reviewerSlug.trim() || getReviewerId(),
        winner,
        pass_fail: Object.fromEntries(
          Object.entries(passFail).filter((entry): entry is [string, boolean] =>
            typeof entry[1] === "boolean"
          ),
        ),
        failure_tags: selectedFailureTags(passFail, failureTags),
        rubric_notes: {},
        notes: notes.trim() || undefined,
      };
      if (selectedItem.assignment_id) {
        await submitReviewAssignmentDecision(selectedItem.assignment_id, decision);
      } else {
        await submitReviewDecision(selectedItem.id, decision);
      }
      setMessage({ tone: "success", text: "Review submitted." });
    } catch (error) {
      setMessage(reviewErrorMessage(error, "Could not submit review."));
    } finally {
      setLoading(false);
    }
  }

  function setAnswerPassFail(label: string, passed: boolean) {
    setPassFail((current) => ({ ...current, [label]: passed }));
    if (passed) {
      setFailureTags((current) => {
        const next = { ...current };
        delete next[label];
        return next;
      });
    } else {
      setFailureTags((current) => ({ ...current, [label]: current[label] ?? [] }));
    }
  }

  function toggleFailureTag(label: string, tag: string) {
    setFailureTags((current) =>
      current[label]?.includes(tag)
        ? { ...current, [label]: current[label].filter((item) => item !== tag) }
        : { ...current, [label]: [...(current[label] ?? []), tag] },
    );
  }

  function selectReviewItem(nextIndex: number) {
    if (!itemCount) return;
    setSelectedIndex(Math.max(0, Math.min(nextIndex, itemCount - 1)));
    resetReviewForm();
  }

  function resetReviewForm() {
    setWinner("cannot_judge");
    setPassFail({});
    setFailureTags({});
    setNotes("");
    setMessage(null);
  }

  return (
    <section className="comparison-stack">
      <div className="panel panel-heading comparison-header">
        <div>
          <span className="section-label">Blind pairwise review</span>
          <h2>{reviewSet?.name ?? draft?.name ?? "No review set loaded"}</h2>
        </div>
        <div className="action-row">
          <button
            type="button"
            className="secondary"
            onClick={createBlindReviewSet}
            disabled={!draft?.apiId || loading}
          >
            <GitCompareArrows size={16} aria-hidden="true" />
            Create blind review set
          </button>
          <button
            type="button"
            className="secondary"
            onClick={revealMetadata}
            disabled={!reviewSet || loading}
          >
            <FileText size={16} aria-hidden="true" />
            Reveal metadata
          </button>
        </div>
      </div>

      {message ? (
        message.tone === "error" ? (
          <InlineError message={message.text} />
        ) : (
          <InlineStatus message={message.text} />
        )
      ) : null}

      <section className="comparison-layout">
        {selectedItem?.answers.length ? (
          selectedItem.answers.map((answer) => (
            <div key={answer.label} className="panel output-pane">
              <span className="section-label">Answer {answer.label}</span>
              <h2>Blind review slot</h2>
              <pre className="output-text">{answer.text || "No output text captured."}</pre>
              <MetadataReveal item={selectedItem} label={answer.label} />
            </div>
          ))
        ) : (
          <>
            <div className="panel output-pane">
              <span className="section-label">Answer A</span>
              <h2>Blind review slot</h2>
              <p>
                {draft
                  ? "Create a blind review set to load pairwise outputs."
                  : "Save a draft experiment first."}
              </p>
            </div>
            <div className="panel output-pane">
              <span className="section-label">Answer B</span>
              <h2>Blind review slot</h2>
              <p>No output pair loaded.</p>
            </div>
          </>
        )}

        <div className="panel review-rail">
          <div className="review-rail-heading">
            <h2>Review</h2>
            <span>
              Pair {itemCount ? selectedIndex + 1 : 0} of {itemCount}
            </span>
          </div>
          <label className="field">
            <span>Reviewer</span>
            <input
              value={reviewerSlug}
              onChange={(event) =>
                setReviewerSlug(event.target.value.trim() ? slugify(event.target.value) : "")
              }
            />
          </label>
          <div className="summary-list">
            <div>
              <span>Assigned</span>
              <strong>{reviewSet?.assignment_progress?.assigned ?? 0}</strong>
            </div>
            <div>
              <span>Submitted</span>
              <strong>{reviewSet?.assignment_progress?.submitted ?? 0}</strong>
            </div>
            <div>
              <span>Pending</span>
              <strong>{reviewSet?.assignment_progress?.pending ?? 0}</strong>
            </div>
          </div>
          <div className="pair-navigation" aria-label="Review pair navigation">
            <button
              type="button"
              className="secondary"
              onClick={() => selectReviewItem(selectedIndex - 1)}
              disabled={!itemCount || selectedIndex === 0 || loading}
            >
              Previous pair
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => selectReviewItem(selectedIndex + 1)}
              disabled={!itemCount || selectedIndex >= itemCount - 1 || loading}
            >
              Next pair
            </button>
            <button
              type="button"
              className="secondary"
              onClick={loadReviewerQueue}
              disabled={!reviewSet || !reviewerSlug.trim() || loading}
            >
              Load queue
            </button>
          </div>
          <div className="decision-grid" aria-label="Pairwise decision">
            {(["A", "B", "tie", "cannot_judge"] as ReviewWinner[]).map((value) => (
              <button
                key={value}
                type="button"
                className={winner === value ? "selected secondary" : "secondary"}
                onClick={() => setWinner(value)}
              >
                <Check size={16} aria-hidden="true" />
                {winnerLabel(value)}
              </button>
            ))}
          </div>

          <div className="decision-grid" aria-label="Pass fail decisions">
            {reviewAnswers.flatMap((answer) => [
              <button
                key={`${answer.label}-pass`}
                type="button"
                className={passFail[answer.label] === true ? "selected secondary" : "secondary"}
                onClick={() => setAnswerPassFail(answer.label, true)}
              >
                Pass {answer.label}
              </button>,
              <button
                key={`${answer.label}-fail`}
                type="button"
                className={passFail[answer.label] === false ? "selected secondary" : "secondary"}
                onClick={() => setAnswerPassFail(answer.label, false)}
              >
                Fail {answer.label}
              </button>,
            ])}
          </div>

          {availableFailureTags.length ? (
            <div className="failure-tags" aria-label="Failure tags">
              {reviewAnswers
                .filter((answer) => passFail[answer.label] === false)
                .map((answer) => (
                  <fieldset key={answer.label} className="failure-tag-group">
                    <legend>Failure tags for Answer {answer.label}</legend>
                    {availableFailureTags.map((tag) => (
                      <label key={tag} className="check-row compact">
                        <input
                          type="checkbox"
                          checked={(failureTags[answer.label] ?? []).includes(tag)}
                          onChange={() => toggleFailureTag(answer.label, tag)}
                        />
                        <span>
                          Answer {answer.label}: {tag}
                        </span>
                      </label>
                    ))}
                  </fieldset>
                ))}
            </div>
          ) : null}

          <label className="field">
            <span>Review notes</span>
            <textarea
              className="notes-editor"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </label>

          <button type="button" onClick={submitDecision} disabled={!selectedItem || loading}>
            <Save size={16} aria-hidden="true" />
            Submit review
          </button>
        </div>
      </section>
    </section>
  );
}

function getReviewerId(): string {
  const key = "model-eval-reviewer-id";
  let storage: Storage | undefined;
  try {
    storage = window.localStorage;
    const current = typeof storage?.getItem === "function" ? storage.getItem(key) : null;
    if (current) return current;
  } catch {
    storage = undefined;
  }
  const generated =
    typeof window.crypto?.randomUUID === "function"
      ? `human-${window.crypto.randomUUID()}`
      : `human-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  try {
    if (typeof storage?.setItem === "function") {
      storage.setItem(key, generated);
    }
  } catch {
    // Private browsing and embedded browsers may deny localStorage writes.
  }
  return generated;
}

function MetadataReveal({ item, label }: { item: ApiReviewItem; label: string }) {
  const metadata = item.reveal_metadata?.answers?.find((answer) => answer.label === label);
  if (!metadata) return null;
  return (
    <dl className="metadata-list">
      <dt>Model</dt>
      <dd>{metadata.model_config_slug ?? "unknown"}</dd>
      <dt>System prompt</dt>
      <dd>{metadata.system_prompt_slug ?? "unknown"}</dd>
      <dt>Warmer</dt>
      <dd>{metadata.warmer_slug ?? "unknown"}</dd>
      <dt>Cost</dt>
      <dd>${(metadata.cost_usd ?? 0).toFixed(2)}</dd>
    </dl>
  );
}

function winnerLabel(value: ReviewWinner): string {
  if (value === "A") return "Prefer A";
  if (value === "B") return "Prefer B";
  if (value === "tie") return "Tie";
  return "Cannot judge";
}

function selectedFailureTags(
  passFail: ReviewPassFail,
  failureTags: ReviewFailureTags,
): ReviewFailureTags {
  return Object.fromEntries(
    Object.entries(failureTags).filter(
      ([label, tags]) => passFail[label] === false && tags.length > 0,
    ),
  );
}

async function ensureReviewerAssignments(
  reviewSetId: number,
  reviewerSlug: string,
  projectSlug?: string,
): Promise<void> {
  if (!reviewerSlug) return;
  try {
    await createReviewer({ slug: reviewerSlug, name: reviewerSlug }, projectSlug);
  } catch (error) {
    if (!isConflict(error)) throw error;
  }
  await createReviewAssignments(reviewSetId, [reviewerSlug]);
}

function mergeReviewMetadata(current: ApiReviewSet, revealed: ApiReviewSet): ApiReviewSet {
  const metadataByItemId = new Map(revealed.items.map((item) => [item.id, item.reveal_metadata]));
  return {
    ...current,
    metadata: revealed.metadata,
    items: current.items.map((item) => ({
      ...item,
      reveal_metadata: metadataByItemId.get(item.id),
    })),
  };
}

async function reviewSetWithReviewerQueue(
  reviewSet: ApiReviewSet,
  reviewerSlug: string,
): Promise<ApiReviewSet> {
  if (!reviewerSlug.trim()) return reviewSet;
  const queue = await getReviewerQueue(reviewSet.id, reviewerSlug);
  return {
    ...reviewSet,
    assignment_progress: queue.progress,
    metadata: {
      ...reviewSet.metadata,
      failure_taxonomy: queue.failure_taxonomy.slug
        ? {
            slug: queue.failure_taxonomy.slug,
            name: queue.failure_taxonomy.name ?? queue.failure_taxonomy.slug,
            version: queue.failure_taxonomy.version ?? 1,
            tags: queue.failure_taxonomy.tags ?? reviewSet.metadata.failure_tags ?? [],
          }
        : reviewSet.metadata.failure_taxonomy,
      failure_tags:
        queue.failure_taxonomy.tags ??
        reviewSet.metadata.failure_taxonomy?.tags ??
        reviewSet.metadata.failure_tags,
    },
    items: queue.items,
  };
}

function reviewErrorMessage(error: unknown, fallback: string): ReviewMessage {
  return { tone: "error", text: error instanceof Error ? error.message : fallback };
}

type ResultsView = "all" | "frontier" | "uncertainty" | "calibration" | "sensitivity";

const resultsViewOptions: Array<{ id: ResultsView; label: string }> = [
  { id: "all", label: "All" },
  { id: "frontier", label: "Frontier" },
  { id: "uncertainty", label: "Uncertainty" },
  { id: "calibration", label: "Calibration" },
  { id: "sensitivity", label: "Sensitivity" },
];

function ResultsScreen({
  draft,
  library,
}: {
  draft?: DraftExperimentRecord;
  library: LibraryState;
}) {
  const [analytics, setAnalytics] = useState<ApiResultsAnalytics | null>(null);
  const [loading, setLoading] = useState(false);
  const [resultsView, setResultsView] = useState<ResultsView>("all");
  const [modelFilter, setModelFilter] = useState("");
  const [promptFilter, setPromptFilter] = useState("");
  const [warmerFilter, setWarmerFilter] = useState("");
  const [judgeRunning, setJudgeRunning] = useState(false);
  const [metricAdapterRunning, setMetricAdapterRunning] = useState(false);
  const [metricAdapterSlug, setMetricAdapterSlug] = useState(
    () => (library.metricAdapterConfigs[0] ? metricAdapterConfigKey(library.metricAdapterConfigs[0]) : ""),
  );
  const [metricAdapterDryRun, setMetricAdapterDryRun] = useState(false);
  const [metricAdapterSummary, setMetricAdapterSummary] = useState<string | null>(null);
  const [exportRunningFormat, setExportRunningFormat] = useState<string | null>(null);
  const [exportSummary, setExportSummary] = useState<string | null>(null);
  const [exportContent, setExportContent] = useState("");
  const [exportWarnings, setExportWarnings] = useState<ApiPromptfooWarning[]>([]);
  const [exportError, setExportError] = useState<string | null>(null);
  const [promptfooImportContent, setPromptfooImportContent] = useState("");
  const [promptfooImportRunning, setPromptfooImportRunning] = useState<
    "preview" | "persist" | null
  >(null);
  const [promptfooImportSummary, setPromptfooImportSummary] = useState<string | null>(null);
  const [promptfooImportWarnings, setPromptfooImportWarnings] = useState<ApiPromptfooWarning[]>([]);
  const [promptfooImportError, setPromptfooImportError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const judgeEvaluatorId = draft?.manifest.evaluation.evaluators.find(isJudgeEvaluator)?.id;
  const selectedMetricAdapter =
    library.metricAdapterConfigs.find((item) => metricAdapterConfigKey(item) === metricAdapterSlug) ??
    library.metricAdapterConfigs[0];
  const analyticsFilters = useMemo<Partial<ApiAnalyticsFilters>>(
    () => ({
      model_config_slug: modelFilter || null,
      system_prompt_slug: promptFilter || null,
      warmer_slug: warmerFilter || null,
    }),
    [modelFilter, promptFilter, warmerFilter],
  );

  function loadAnalytics() {
    let active = true;
    if (!draft?.apiId) {
      setAnalytics(null);
      setLoading(false);
      setError(null);
      return () => undefined;
    }
    setLoading(true);
    setError(null);
    getExperimentAnalytics(draft.apiId, analyticsFilters)
      .then((payload) => {
        if (active) setAnalytics(payload);
      })
      .catch((fetchError: unknown) => {
        if (active) {
          setAnalytics(null);
          setError(fetchError instanceof Error ? fetchError.message : "Could not load results.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }

  useEffect(() => {
    return loadAnalytics();
  }, [draft?.apiId, analyticsFilters]);

  useEffect(() => {
    if (!library.metricAdapterConfigs.length) {
      setMetricAdapterSlug("");
      return;
    }
    if (!library.metricAdapterConfigs.some((item) => metricAdapterConfigKey(item) === metricAdapterSlug)) {
      setMetricAdapterSlug(metricAdapterConfigKey(library.metricAdapterConfigs[0]));
    }
  }, [library.metricAdapterConfigs, metricAdapterSlug]);

  async function runJudge() {
    if (!draft?.apiId || !judgeEvaluatorId) return;
    setJudgeRunning(true);
    setError(null);
    try {
      await runExperimentJudge(draft.apiId, judgeEvaluatorId);
      await getExperimentAnalytics(draft.apiId, analyticsFilters).then(setAnalytics);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Could not run judge.");
    } finally {
      setJudgeRunning(false);
    }
  }

  async function runMetricAdapter() {
    if (!draft?.apiId || !selectedMetricAdapter) return;
    setMetricAdapterRunning(true);
    setMetricAdapterSummary(null);
    setError(null);
    try {
      await persistLibraryRecord("metricAdapterConfigs", selectedMetricAdapter);
      const result = await runExperimentMetricAdapters(draft.apiId, {
        adapterConfigSlug: selectedMetricAdapter.id,
        adapterConfigVersion: selectedMetricAdapter.version,
        dryRun: metricAdapterDryRun,
        localOnly: true,
      });
      setMetricAdapterSummary(formatMetricAdapterRunSummary(result));
      await getExperimentAnalytics(draft.apiId, analyticsFilters).then(setAnalytics);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "Could not run metric adapter.");
    } finally {
      setMetricAdapterRunning(false);
    }
  }

  async function runExport(format: "markdown" | "csv" | "json" | "promptfoo" | "otel-json") {
    if (!draft?.apiId) return;
    setExportRunningFormat(format);
    setExportSummary(null);
    setExportContent("");
    setExportWarnings([]);
    setExportError(null);
    try {
      const result = await exportExperiment(draft.apiId, format, analyticsFilters);
      setExportWarnings(result.warnings ?? []);
      setExportContent(result.content);
      setExportSummary(formatExportSummary(result.format, result.content));
    } catch (runError) {
      setExportError(runError instanceof Error ? runError.message : "Could not export results.");
    } finally {
      setExportRunningFormat(null);
    }
  }

  async function previewPromptfooConfig(persist: boolean) {
    if (!promptfooImportContent.trim()) {
      setPromptfooImportError("Promptfoo config is required.");
      setPromptfooImportSummary(null);
      setPromptfooImportWarnings([]);
      return;
    }
    setPromptfooImportRunning(persist ? "persist" : "preview");
    setPromptfooImportSummary(null);
    setPromptfooImportWarnings([]);
    setPromptfooImportError(null);
    try {
      const result = await previewPromptfooImport(promptfooImportContent, persist, draft?.projectSlug);
      setPromptfooImportWarnings(result.warnings ?? []);
      setPromptfooImportSummary(formatPromptfooImportPreview(result));
    } catch (runError) {
      setPromptfooImportError(
        runError instanceof Error ? runError.message : "Could not preview Promptfoo import.",
      );
    } finally {
      setPromptfooImportRunning(null);
    }
  }

  if (!draft?.apiId) {
    return <div className="empty-state">No completed experiment selected.</div>;
  }

  if (loading) {
    return <div className="empty-state">Loading results.</div>;
  }

  if (error) {
    return <InlineError message={error} />;
  }

  if (!analytics) {
    return <div className="empty-state">No results available.</div>;
  }

  const summary = analytics.summary;
  const uncertaintyRows: ApiNondeterminismRow[] =
    analytics.nondeterminism_by_dimension?.model_config_slug ?? [];
  const divergenceRows: ApiDivergenceSummaryRow[] = analytics.divergence_summary ?? [];
  const carryoverRows: ApiCarryoverSummaryRow[] = analytics.carryover_summary ?? [];
  const metricAdapterRows: ApiMetricAdapterScoreRow[] = analytics.metric_adapter_scores ?? [];
  const frontierRows: ApiCostQualityFrontierRow[] = analytics.cost_quality_frontier ?? [];
  const modelFilterOptions = uniqueFrontierOptions(
    frontierRows,
    (row) => row.model_config_slug,
    modelFilter,
    draft.manifest.models.map((item) => item.id),
  );
  const promptFilterOptions = uniqueFrontierOptions(
    frontierRows,
    (row) => row.system_prompt_slug,
    promptFilter,
    draft.manifest.system_prompts.map((item) => item.id),
  );
  const warmerFilterOptions = uniqueFrontierOptions(
    frontierRows,
    (row) => row.warmer_slug,
    warmerFilter,
    draft.manifest.warmers.map((item) => item.id),
  );
  const leadingSensitivity = strongestContextSensitivity(analytics.context_sensitivity);
  const leadingLift = strongestWarmerLift(analytics.warmer_lift);
  const showAllResults = resultsView === "all";
  const showFrontier = showAllResults || resultsView === "frontier";
  const showUncertainty = showAllResults || resultsView === "uncertainty";
  const showCalibration = showAllResults || resultsView === "calibration";
  const showSensitivity = showAllResults || resultsView === "sensitivity";

  return (
    <section className="results-dashboard">
      <div className="results-summary">
        <Metric label="Pass rate" value={formatRate(summary.pass_rate)} />
        <Metric label="Win rate" value={formatRate(summary.win_rate)} />
        <Metric label="Failure rate" value={formatRate(summary.failure_rate)} />
        <Metric label="Average cost" value={formatCurrency(summary.average_cost_usd)} />
        <Metric label="Average latency" value={formatLatency(summary.average_latency_ms)} />
        <Metric label="Total tokens" value={summary.token_totals.total_tokens.toLocaleString()} />
      </div>

      <section className="panel results-caution">
        <AlertCircle size={18} aria-hidden="true" />
        <p>
          Numeric result rates are uncalibrated directional summaries. Use counts, failure tags,
          and review notes when comparing model, prompt, and warmer behavior.
        </p>
      </section>

      <section className="panel results-controls" aria-label="Results controls">
        <div className="results-view-tabs" aria-label="Results view">
          {resultsViewOptions.map((option) => (
            <button
              key={option.id}
              type="button"
              className={resultsView === option.id ? "selected" : ""}
              aria-pressed={resultsView === option.id}
              onClick={() => setResultsView(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="results-filter-grid">
          <label className="field">
            <span>Model filter</span>
            <select value={modelFilter} onChange={(event) => setModelFilter(event.target.value)}>
              <option value="">All models</option>
              {modelFilterOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Prompt filter</span>
            <select value={promptFilter} onChange={(event) => setPromptFilter(event.target.value)}>
              <option value="">All prompts</option>
              {promptFilterOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Warmer filter</span>
            <select value={warmerFilter} onChange={(event) => setWarmerFilter(event.target.value)}>
              <option value="">All warmers</option>
              {warmerFilterOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      {showUncertainty ? (
      <section className="panel">
        <div className="panel-heading">
          <div>
            <span className="section-label">Uncertainty</span>
            <h2>Replicate reliability</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No replicate reliability rows yet."
          columns={["Model", "Samples", "Failure interval", "Cost interval", "Retries", "Label"]}
          rows={uncertaintyRows}
          renderRow={(row) => [
            String(row.model_config_slug ?? "all"),
            row.sample_count.toLocaleString(),
            formatInterval(row.failure_rate_interval, formatRate),
            formatInterval(row.cost_usd_interval, formatCurrency),
            row.retry_attempt_count.toLocaleString(),
            resultLabel(row.failure_rate_interval.label || row.cost_usd_interval.label),
          ]}
        />
      </section>
      ) : null}

      {showCalibration ? (
      <section className="panel">
        <div className="panel-heading">
          <div>
            <span className="section-label">LLM judge</span>
            <h2>Calibration status</h2>
          </div>
          <button type="button" onClick={runJudge} disabled={!judgeEvaluatorId || judgeRunning}>
            <Play size={16} aria-hidden="true" />
            {judgeRunning ? "Running" : "Run judge"}
          </button>
        </div>
        <ResultsTable
          emptyLabel={
            judgeEvaluatorId
              ? "No judge calibration rows yet."
              : "No LLM judge evaluator selected."
          }
          columns={["Judge", "Pairwise", "Pass/fail", "Rubric", "Agreement", "Low confidence"]}
          rows={analytics.judge_calibration}
          renderRow={(row) => [
            row.evaluator_id,
            row.pairwise_comparison_count.toLocaleString(),
            row.pass_fail_comparison_count.toLocaleString(),
            row.rubric_comparison_count.toLocaleString(),
            formatRate(row.agreement_rate),
            row.low_confidence_count.toLocaleString(),
          ]}
        />
      </section>
      ) : null}

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Metric adapters</span>
            <h2>Adapter scores</h2>
          </div>
          <button
            type="button"
            onClick={runMetricAdapter}
            disabled={!selectedMetricAdapter || metricAdapterRunning}
          >
            <Play size={16} aria-hidden="true" />
            {metricAdapterRunning ? "Running" : "Run adapter"}
          </button>
        </div>
        {library.metricAdapterConfigs.length ? (
          <div className="adapter-run-controls">
            <label className="field">
              <span>Metric adapter</span>
              <select
                value={selectedMetricAdapter ? metricAdapterConfigKey(selectedMetricAdapter) : ""}
                onChange={(event) => setMetricAdapterSlug(event.target.value)}
              >
                {library.metricAdapterConfigs.map((config, index) => (
                  <option
                    key={`${metricAdapterConfigKey(config)}-${index}`}
                    value={metricAdapterConfigKey(config)}
                  >
                    {config.name}
                  </option>
                ))}
              </select>
            </label>
            <Toggle
              label="Dry run"
              checked={metricAdapterDryRun}
              onChange={setMetricAdapterDryRun}
            />
            {metricAdapterSummary ? <InlineStatus message={metricAdapterSummary} /> : null}
          </div>
        ) : (
          <div className="empty-state">No metric adapter configs available.</div>
        )}
        <ResultsTable
          emptyLabel="No adapter scores yet."
          columns={["Adapter", "Criterion", "Score", "Label", "Attempt", "Source"]}
          rows={metricAdapterRows}
          renderRow={(row) => [
            adapterScoreConfigLabel(row),
            row.criterion,
            formatRate(row.score),
            resultLabel(row.label),
            row.attempt_id,
            sourceKindLabel(row.source_kind),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Bias controls</span>
            <h2>Verbosity bias</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No judge verbosity rows yet."
          columns={["Judge", "Comparisons", "Longer wins", "Longer win rate", "Winner tokens"]}
          rows={analytics.judge_verbosity_bias}
          renderRow={(row) => [
            row.evaluator_id,
            row.comparison_count.toLocaleString(),
            row.longer_answer_win_count.toLocaleString(),
            formatRate(row.longer_answer_win_rate),
            formatNumber(row.winner_average_tokens),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Quality</span>
            <h2>Score table</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No scored attempts yet."
          columns={["Model", "Prompt", "Warmer", "Quality", "Pass", "Win", "Attempts"]}
          rows={analytics.cost_quality_table}
          renderRow={(row) => [
            row.model_config_slug,
            row.system_prompt_slug,
            row.warmer_slug,
            metricLabel(row.quality_metric, row.quality_rate),
            formatRate(row.pass_rate),
            formatRate(row.win_rate),
            row.attempt_count.toLocaleString(),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Cost</span>
            <h2>Cost table</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No cost data captured."
          columns={["Model", "Warmer", "Cost", "Quality", "Cost per quality point"]}
          rows={analytics.cost_quality_table}
          renderRow={(row) => [
            row.model_config_slug,
            row.warmer_slug,
            formatCurrency(row.average_cost_usd),
            metricLabel(row.quality_metric, row.quality_rate),
            formatCurrency(row.cost_usd_per_quality_point ?? null),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Latency</span>
            <h2>Latency table</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No latency data captured."
          columns={["Model", "Warmer", "Latency", "Quality", "Failure rate"]}
          rows={analytics.latency_quality_table}
          renderRow={(row) => [
            row.model_config_slug,
            row.warmer_slug,
            formatLatency(row.average_latency_ms),
            metricLabel(row.quality_metric, row.quality_rate),
            formatRate(row.failure_rate),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Failure modes</span>
            <h2>Failure tag table</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No failure tags recorded."
          columns={["Tag", "Count", "Rate"]}
          rows={analytics.failure_tag_frequency}
          renderRow={(row) => [row.tag, row.count.toLocaleString(), formatRate(row.rate)]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Human review</span>
            <h2>Reviewer coverage</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No reviewer assignments recorded."
          columns={["Review set", "Reviewers", "Submitted", "Pending", "Coverage"]}
          rows={analytics.reviewer_coverage}
          renderRow={(row) => [
            row.review_set_id.toLocaleString(),
            row.reviewer_count.toLocaleString(),
            `${row.submitted_count}/${row.assigned_count}`,
            row.pending_count.toLocaleString(),
            formatRate(row.coverage_rate),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Human review</span>
            <h2>Reviewer disagreement</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No multi-reviewer disagreements recorded."
          columns={["Review item", "Reviewers", "Pairwise", "Pass/fail", "Failure tags"]}
          rows={analytics.reviewer_disagreement}
          renderRow={(row) => [
            row.review_item_id.toLocaleString(),
            row.reviewer_count.toLocaleString(),
            row.pairwise_disagreement ? "disagreed" : "agreed",
            row.pass_fail_disagreement_count.toLocaleString(),
            row.failure_tag_disagreement_count.toLocaleString(),
          ]}
        />
      </section>

      <section className="panel" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Taxonomy</span>
            <h2>Failure taxonomy rollup</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No taxonomy-backed failure tags recorded."
          columns={["Tag", "Version", "Count"]}
          rows={analytics.failure_taxonomy_rollup}
          renderRow={(row) => [
            row.tag,
            row.taxonomy_version === null ? "n/a" : row.taxonomy_version.toLocaleString(),
            row.count.toLocaleString(),
          ]}
        />
      </section>

      {showSensitivity ? (
      <section className="panel wide-result">
        <div className="panel-heading">
          <div>
            <span className="section-label">Context</span>
            <h2>Warmer lift chart</h2>
          </div>
          <strong className="result-callout">
            {leadingLift ? formatSignedRate(leadingLift.lift) : "no baseline"}
          </strong>
        </div>
        <div className="lift-list" aria-label="Warmer lift chart">
          {analytics.warmer_lift.length ? (
            analytics.warmer_lift.map((row) => <WarmerLiftBar key={warmerLiftKey(row)} row={row} />)
          ) : (
            <div className="empty-state">No warmer lift rows available.</div>
          )}
        </div>
      </section>
      ) : null}

      {showSensitivity ? (
      <section className="panel wide-result">
        <div className="panel-heading">
          <div>
            <span className="section-label">Sensitivity</span>
            <h2>Context sensitivity table</h2>
          </div>
          <span className={`sensitivity-label ${leadingSensitivity?.label ?? "insufficient_data"}`}>
            {resultLabel(leadingSensitivity?.label)}
          </span>
        </div>
        <ResultsTable
          emptyLabel="No context sensitivity rows available."
          columns={["Case", "Model", "Prompt", "Best warmer", "Worst warmer", "Spread", "Label"]}
          rows={analytics.context_sensitivity}
          renderRow={(row) => [
            row.case_slug,
            row.model_config_slug,
            row.system_prompt_slug,
            row.best_warmer_slug ?? "n/a",
            row.worst_warmer_slug ?? "n/a",
            formatRate(row.score_spread),
            resultLabel(row.label),
          ]}
        />
      </section>
      ) : null}

      {showFrontier ? (
      <section className="panel wide-result">
        <div className="panel-heading">
          <div>
            <span className="section-label">Frontier</span>
            <h2>Cost-quality frontier</h2>
          </div>
        </div>
        <CostQualityFrontier rows={frontierRows} />
        <ResultsTable
          emptyLabel="No frontier rows available."
          columns={[
            "Case",
            "Model",
            "Warmer",
            "Status",
            "Quality",
            "Cost",
            "Latency",
            "Uncertainty",
            "Calibration",
            "Lift",
            "Divergence",
          ]}
          rows={frontierRows}
          renderRow={(row) => [
            `${row.case_slug}/${row.suite_split}`,
            row.model_config_slug,
            row.warmer_slug,
            frontierStatusLabel(row),
            metricLabel(row.quality_metric, row.quality_rate),
            formatCurrency(row.average_cost_usd),
            formatLatency(row.average_latency_ms),
            frontierUncertaintyLabel(row),
            frontierCalibrationLabel(row),
            row.warmer_lift ? formatSignedRate(row.warmer_lift.lift) : "n/a",
            frontierDivergenceLabel(row),
          ]}
        />
      </section>
      ) : null}

      <section className="panel wide-result" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Failure rates</span>
            <h2>Failure rate by model, prompt, warmer, and case</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No failed attempts recorded."
          columns={["Case", "Model", "Prompt", "Warmer", "Failures", "Failure rate"]}
          rows={analytics.failure_rate_table}
          renderRow={(row) => [
            row.case_slug,
            row.model_config_slug,
            row.system_prompt_slug,
            row.warmer_slug,
            `${row.failed_attempt_count}/${row.attempt_count}`,
            formatRate(row.failure_rate),
          ]}
        />
      </section>

      <section className="panel wide-result" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Divergence</span>
            <h2>Divergence placeholders</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No divergence signals available."
          columns={["Case", "Model", "Prompt", "Score spread", "Failure tags", "Label"]}
          rows={analytics.divergence_placeholders}
          renderRow={(row) => [
            row.case_slug,
            row.model_config_slug,
            row.system_prompt_slug,
            formatRate(row.score_spread),
            row.failure_tag_spread ? "varied" : "stable",
            resultLabel(row.label),
          ]}
        />
      </section>

      <section className="panel wide-result" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Divergence</span>
            <h2>Divergence metrics</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No divergence metric rows available."
          columns={[
            "Case",
            "Model",
            "Prompt",
            "Warmer",
            "Signal",
            "Source",
            "Samples",
            "Warning",
            "Label",
          ]}
          rows={divergenceRows}
          renderRow={(row) => [
            row.case_slug,
            row.model_config_slug,
            row.system_prompt_slug,
            row.warmer_slug,
            metricSourceLabel(row.criterion),
            sourceKindLabel(row.source_kind),
            row.sample_count.toLocaleString(),
            warningLabel(row.warning_label),
            resultLabel(row.label),
          ]}
        />
      </section>

      <section className="panel wide-result" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Carryover</span>
            <h2>Carryover audit</h2>
          </div>
        </div>
        <ResultsTable
          emptyLabel="No carryover audit rows available."
          columns={["Case", "Model", "Prompt", "Warmer", "Status", "Source", "Samples", "Warning"]}
          rows={carryoverRows}
          renderRow={(row) => [
            row.case_slug,
            row.model_config_slug,
            row.system_prompt_slug,
            row.warmer_slug,
            resultLabel(row.status),
            sourceKindLabel(row.source_kind),
            row.sample_count.toLocaleString(),
            warningLabel(row.warning_label),
          ]}
        />
      </section>

      <section className="panel wide-result" hidden={!showAllResults}>
        <div className="panel-heading">
          <div>
            <span className="section-label">Exports</span>
            <h2>Export actions</h2>
          </div>
        </div>
        <div className="interop-actions">
          <div>
            <div className="export-actions">
              {(
                [
                  ["markdown", "Markdown"],
                  ["csv", "CSV"],
                  ["json", "JSON"],
                  ["promptfoo", "Promptfoo"],
                  ["otel-json", "OpenTelemetry JSON"],
                ] as const
              ).map(([format, label]) => (
                <button
                  key={format}
                  type="button"
                  className="secondary"
                  onClick={() => void runExport(format)}
                  disabled={exportRunningFormat !== null}
                >
                  <Download size={16} aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>
            <p className="export-note">Metadata-only local-file trace export.</p>
            {exportError ? <InlineError message={exportError} /> : null}
            {exportSummary ? <InlineStatus message={exportSummary} /> : null}
            <PromptfooWarnings title="Promptfoo export warning" warnings={exportWarnings} />
            {exportContent ? (
              <label className="field wide export-content">
                <span>Export content</span>
                <textarea className="code-editor" value={exportContent} readOnly />
              </label>
            ) : null}
          </div>
          <div className="promptfoo-import-panel">
            <h3>Promptfoo import</h3>
            <label className="field wide">
              <span>Promptfoo config</span>
              <textarea
                className="code-editor"
                value={promptfooImportContent}
                onChange={(event) => setPromptfooImportContent(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="secondary"
              onClick={() => void previewPromptfooConfig(false)}
              disabled={promptfooImportRunning !== null}
            >
              <FileText size={16} aria-hidden="true" />
              {promptfooImportRunning === "preview" ? "Previewing" : "Preview import"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => void previewPromptfooConfig(true)}
              disabled={promptfooImportRunning !== null}
            >
              <Save size={16} aria-hidden="true" />
              {promptfooImportRunning === "persist" ? "Persisting" : "Persist import"}
            </button>
            {promptfooImportError ? <InlineError message={promptfooImportError} /> : null}
            {promptfooImportSummary ? <InlineStatus message={promptfooImportSummary} /> : null}
            <PromptfooWarnings
              title="Promptfoo import warning"
              warnings={promptfooImportWarnings}
            />
          </div>
        </div>
      </section>
    </section>
  );
}

function ResultsTable<T>({
  columns,
  rows,
  renderRow,
  emptyLabel,
}: {
  columns: string[];
  rows: T[];
  renderRow: (row: T) => string[];
  emptyLabel: string;
}) {
  if (!rows.length) {
    return <div className="empty-state">{emptyLabel}</div>;
  }
  return (
    <div className="results-table" role="table">
      <div className="results-table-head" role="row">
        {columns.map((column) => (
          <span key={column} role="columnheader">
            {column}
          </span>
        ))}
      </div>
      {rows.map((row, rowIndex) => (
        <div key={rowIndex} className="results-table-row" role="row">
          {renderRow(row).map((cell, cellIndex) => (
            <span key={`${rowIndex}-${cellIndex}`} role="cell">
              {cell}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

function WarmerLiftBar({ row }: { row: ApiWarmerLiftRow }) {
  const value = row.lift ?? 0;
  const width = `${Math.min(Math.abs(value), 1) * 50}%`;
  return (
    <div className="lift-row">
      <div>
        <strong>{row.warmer_slug}</strong>
        <small>
          {row.case_slug} · {row.model_config_slug} · {row.system_prompt_slug}
        </small>
      </div>
      <div className="lift-track" aria-label={`${row.warmer_slug} lift ${formatSignedRate(row.lift)}`}>
        <span className="lift-midpoint" />
        {row.lift === null ? null : (
          <span
            className={`lift-fill ${row.lift >= 0 ? "positive" : "negative"}`}
            style={row.lift >= 0 ? { left: "50%", width } : { right: "50%", width }}
          />
        )}
      </div>
      <strong>{row.baseline_missing ? "missing baseline" : formatSignedRate(row.lift)}</strong>
    </div>
  );
}

function CostQualityFrontier({ rows }: { rows: ApiCostQualityFrontierRow[] }) {
  const points = rows.filter(
    (row) => row.average_cost_usd !== null && row.quality_rate !== null,
  );
  if (!points.length) {
    return <div className="empty-state">No cost-quality points available.</div>;
  }
  const maxCost = Math.max(...points.map((row) => row.average_cost_usd ?? 0), 0.01);
  return (
    <div className="frontier-plot" aria-label="Cost-quality frontier">
      <div className="frontier-legend" aria-hidden="true">
        <span>
          <i className="frontier-dot frontier" />
          Frontier
        </span>
        <span>
          <i className="frontier-dot dominated" />
          Dominated
        </span>
      </div>
      {points.map((row) => {
        const x = clampPercent(((row.average_cost_usd ?? 0) / maxCost) * 100, 3, 97);
        const y = clampPercent(100 - (row.quality_rate ?? 0) * 100, 3, 97);
        return (
          <span
            key={frontierRowKey(row)}
            className={`frontier-point ${row.is_frontier ? "frontier" : "dominated"}`}
            style={{ left: `${x}%`, top: `${y}%` }}
            title={frontierPointTitle(row)}
          />
        );
      })}
      <span className="frontier-axis x">Higher cost</span>
      <span className="frontier-axis y">Higher quality</span>
    </div>
  );
}

function uniqueFrontierOptions(
  rows: ApiCostQualityFrontierRow[],
  valueForRow: (row: ApiCostQualityFrontierRow) => string | null | undefined,
  selected: string,
  fallbackValues: string[] = [],
): string[] {
  const options = new Set(fallbackValues.filter(Boolean));
  for (const value of rows.map(valueForRow)) {
    if (value) options.add(value);
  }
  if (selected) options.add(selected);
  return Array.from(options).sort((left, right) => left.localeCompare(right));
}

function frontierRowKey(row: ApiCostQualityFrontierRow): string {
  return row.frontier_key || analyticsTupleKey(
    row.case_slug,
    row.suite_slug,
    row.suite_split,
    row.model_config_slug,
    row.system_prompt_slug,
    row.warmer_slug,
  );
}

function frontierStatusLabel(row: ApiCostQualityFrontierRow): string {
  return row.dominated_by ? "Dominated" : resultLabel(row.dominance_status);
}

function frontierUncertaintyLabel(row: ApiCostQualityFrontierRow): string {
  const labels = [
    row.quality_uncertainty_label,
    row.cost_uncertainty_label,
    row.latency_uncertainty_label,
  ].filter(Boolean);
  const uniqueLabels = Array.from(new Set(labels));
  return uniqueLabels.length ? uniqueLabels.map(resultLabel).join(", ") : "n/a";
}

function frontierCalibrationLabel(row: ApiCostQualityFrontierRow): string {
  if (!row.judge_calibration_overlays.length) return "n/a";
  return row.judge_calibration_overlays
    .map((overlay) => `${overlay.evaluator_id} ${formatRate(overlay.agreement_rate)}`)
    .join(", ");
}

function frontierDivergenceLabel(row: ApiCostQualityFrontierRow): string {
  if (!row.divergence_summary.length) return "n/a";
  return row.divergence_summary
    .map((summary) => `${sourceKindLabel(summary.source_kind)} ${resultLabel(summary.label)}`)
    .join(", ");
}

function frontierPointTitle(row: ApiCostQualityFrontierRow): string {
  return [
    row.model_config_slug,
    row.warmer_slug,
    frontierStatusLabel(row),
    formatCurrency(row.average_cost_usd),
    metricLabel(row.quality_metric, row.quality_rate),
    frontierUncertaintyLabel(row),
  ].join(" - ");
}

function warmerLiftKey(row: ApiWarmerLiftRow): string {
  return analyticsTupleKey(
    row.case_slug,
    row.model_config_slug,
    row.system_prompt_slug,
    row.warmer_slug,
  );
}

function analyticsTupleKey(...parts: string[]): string {
  return JSON.stringify(parts);
}

function strongestWarmerLift(rows: ApiWarmerLiftRow[]): ApiWarmerLiftRow | undefined {
  return rows
    .filter((row) => row.lift !== null)
    .sort((left, right) => Math.abs(right.lift ?? 0) - Math.abs(left.lift ?? 0))[0];
}

function strongestContextSensitivity(
  rows: ApiContextSensitivityRow[],
): ApiContextSensitivityRow | undefined {
  return rows
    .filter((row) => row.score_spread !== null)
    .sort((left, right) => (right.score_spread ?? 0) - (left.score_spread ?? 0))[0];
}

function clampPercent(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function formatCurrency(value: number | null): string {
  if (value === null) return "n/a";
  return `$${value.toFixed(value < 1 ? 3 : 2)}`;
}

function formatRate(value: number | null): string {
  if (value === null) return "n/a";
  return `${Math.round(value * 100)}%`;
}

function formatNumber(value: number | null): string {
  if (value === null) return "n/a";
  return value.toFixed(value % 1 === 0 ? 0 : 1);
}

function isJudgeEvaluator(item: { id: string; type?: string }): boolean {
  return item.type === "llm_judge" || item.id.toLowerCase().includes("judge");
}

function formatMetricAdapterRunSummary(result: ApiMetricAdapterRunSummary): string {
  const skipped = result.skipped?.length ?? 0;
  if (result.dry_run) {
    return `${result.planned_scores ?? 0} planned, ${skipped} skipped`;
  }
  return `${result.scores_recorded ?? 0} score(s), ${skipped} skipped`;
}

function formatExportSummary(format: string, content: string): string {
  if (format === "otel-json") {
    return `OpenTelemetry JSON export ready, ${content.length.toLocaleString()} characters`;
  }
  return `${filterOptionLabel(format)} export ready, ${content.length.toLocaleString()} characters`;
}

function formatPromptfooImportPreview(result: ApiPromptfooImportPreviewResponse): string {
  if (result.persisted) {
    const created = result.persisted.created ?? {};
    const count = Object.values(created).reduce((total, value) => total + value, 0);
    return `Promptfoo import persisted, ${count.toLocaleString()} record(s) created`;
  }
  const logicalRuns = result.preview?.logical_runs;
  const runAttempts = result.preview?.run_attempts;
  if (logicalRuns !== undefined && runAttempts !== undefined) {
    return `${logicalRuns.toLocaleString()} logical run(s), ${runAttempts.toLocaleString()} attempt(s)`;
  }
  return "Promptfoo import preview ready";
}

function PromptfooWarnings({
  title,
  warnings,
}: {
  title: string;
  warnings: ApiPromptfooWarning[];
}) {
  if (!warnings.length) return null;
  return (
    <div className="promptfoo-warnings" role="status">
      <strong>{title}</strong>
      <ul>
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${warning.path ?? "root"}-${index}`}>
            <span>{warning.message}</span>
            <small>
              {[warning.code, warning.path].filter(Boolean).join(" - ")}
            </small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function formatSignedRate(value: number | null): string {
  if (value === null) return "n/a";
  const rounded = Math.round(value * 100);
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

function metricLabel(metric: string | null, rate: number | null): string {
  if (!metric || rate === null) return "n/a";
  return `${filterOptionLabel(metric)} ${formatRate(rate)}`;
}

function derivedArtifactRecord(artifact: ApiDerivedArtifact): ArtifactRecord {
  return {
    id: artifact.slug,
    name: artifact.name,
    artifactType: artifact.artifact_type ?? "",
    uri: "",
    inputMode: artifact.input_mode,
    metadataJson: prettyJson(artifact.metadata ?? {}),
    version: 1,
  };
}

function artifactRecordMatchesExistingExceptInputMode(
  existing: ArtifactRecord,
  record: ArtifactRecord,
): boolean {
  return (
    existing.id === record.id &&
    existing.name === record.name &&
    existing.artifactType === record.artifactType &&
    existing.uri === record.uri &&
    existing.metadataJson === record.metadataJson &&
    existing.version === record.version
  );
}

function upsertArtifactRecords(
  existing: ArtifactRecord[],
  additions: ArtifactRecord[],
): ArtifactRecord[] {
  const records = new Map(existing.map((record) => [record.id, record]));
  for (const record of additions) {
    records.set(record.id, record);
  }
  return Array.from(records.values());
}

function metricAdapterConfigKey(config: MetricAdapterConfigRecord): string {
  return `${config.id}@${config.version}`;
}

function formatInterval(
  interval: { lower: number | null; upper: number | null },
  formatter: (value: number | null) => string,
): string {
  if (interval.lower === null || interval.upper === null) return "n/a";
  return `${formatter(interval.lower)}-${formatter(interval.upper)}`;
}

function resultLabel(value: string | undefined | null): string {
  if (!value) return "Insufficient data";
  return filterOptionLabel(value);
}

function metricSourceLabel(value: string): string {
  return value
    .replace(/^divergence_/, "")
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function adapterScoreConfigLabel(row: ApiMetricAdapterScoreRow): string {
  return row.adapter_config_version === null
    ? row.adapter_config_slug
    : `${row.adapter_config_slug} v${row.adapter_config_version}`;
}

function sourceKindLabel(value: string): string {
  switch (value) {
    case "deterministic_heuristic":
      return "Deterministic heuristic";
    case "judge_backed":
      return "Judge backed";
    case "human_backed":
      return "Human backed";
    default:
      return resultLabel(value);
  }
}

function warningLabel(value: string | undefined | null): string {
  switch (value) {
    case "heuristic":
      return "Heuristic";
    case "judge_needs_calibration":
      return "Judge calibration";
    case "human_labeled":
      return "Human labeled";
    case "none":
      return "None";
    default:
      return resultLabel(value);
  }
}

function ModePanel({ mode }: { mode: ModeId }) {
  return (
    <section className="mode-panel">
      <div className="panel">
        <span className="section-label">{modeLabel(mode)}</span>
        <h2>{modeTitle(mode)}</h2>
        <div className="mode-tiles">
          <MetricCard title="Playground" value={mode === "playground" ? "active" : "separate"} />
          <MetricCard title="Experiment" value={mode === "experiment" ? "active" : "versioned"} />
          <MetricCard title="Benchmark Suite" value={mode === "benchmark" ? "active" : "locked"} />
        </div>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MetricCard({ title, value }: { title: string; value: string }) {
  return (
    <article className="panel metric-card">
      <span className="section-label">{title}</span>
      <strong>{value}</strong>
    </article>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

async function persistLibraryRecord(
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
): Promise<boolean> {
  try {
    await createLibraryRecord(kind, record);
    return true;
  } catch (error) {
    if (isConflict(error)) {
      return false;
    }
    throw error;
  }
}

async function persistManifestLibraryRecords(manifest: ExperimentManifest, library: LibraryState) {
  for (const record of matchingRecords(manifest.cases, library.cases)) {
    await createLibraryRecord("cases", record);
  }
  for (const record of matchingRecords(manifest.artifacts ?? [], library.artifacts)) {
    await createLibraryRecord("artifacts", record);
  }
  for (const record of matchingRecords(manifest.models, library.modelConfigs)) {
    await createLibraryRecord("modelConfigs", record);
  }
  for (const record of matchingRecords(manifest.system_prompts, library.systemPrompts)) {
    await createLibraryRecord("systemPrompts", record);
  }
  for (const record of matchingRecords(manifest.warmers, library.warmers)) {
    await createLibraryRecord("warmers", record);
  }
  for (const record of matchingRecords(manifest.evaluation.evaluators, library.evaluators)) {
    await createLibraryRecord("evaluators", record);
  }
  if (manifest.suite?.id) {
    for (const record of matchingRecords([manifest.suite], library.benchmarkSuites)) {
      await createLibraryRecord("benchmarkSuites", record);
    }
  }
}

function matchingRecords<T extends { id: string; version: number }>(
  references: Array<{ id: string; version?: number }>,
  records: T[],
): T[] {
  return records.filter((record) =>
    references.some(
      (reference) =>
        record.id === reference.id &&
        (reference.version === undefined || record.version === reference.version),
    ),
  );
}

function libraryReferenceKey(item: { id: string; version: number }): string {
  return `${item.id}@${item.version}`;
}

function artifactInputMatchesRecord(input: SelectedArtifactInput, record: ArtifactRecord): boolean {
  return input.id === record.id && (input.version === undefined || input.version === record.version);
}

function draftManifestKey(manifest: ExperimentManifest): string {
  return manifest.id || slugify(manifest.name);
}

function validateManifestEditor(value: string): ManifestEditorState {
  try {
    const parsed = JSON.parse(value) as ExperimentManifest;
    const errors = validateManifestForSave(parsed);
    if (errors.length) {
      return { errors, preview: emptyPreview() };
    }
    return { errors: [], preview: estimateManifestPreview(parsed) };
  } catch {
    return { errors: ["Manifest JSON is invalid."], preview: emptyPreview() };
  }
}

function emptyPreview(): ExperimentPreview {
  return {
    logicalRuns: 0,
    runAttempts: 0,
    estimatedTokens: 0,
    estimatedCostUsd: 0,
  };
}

function manifestChanged(previous: ExperimentManifest, next: ExperimentManifest): boolean {
  return JSON.stringify(previous) !== JSON.stringify(next);
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="inline-error" role="alert">
      <AlertCircle size={16} aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

function InlineStatus({ message }: { message: string }) {
  return (
    <div className="inline-status" role="status">
      <CheckCircle2 size={16} aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

function modeLabel(mode: ModeId): string {
  if (mode === "playground") return "Playground";
  if (mode === "benchmark") return "Benchmark Suite";
  return "Experiment";
}

function modeTitle(mode: ModeId): string {
  if (mode === "playground") return "Disposable prompt run";
  if (mode === "benchmark") return "Reusable regression pack";
  return "Versioned experiment workspace";
}

function routeTitle(route: RouteId): string {
  if (route === "library") return "Library";
  if (route === "experiment") return "Experiment Builder";
  if (route === "monitor") return "Run Monitor";
  if (route === "comparison") return "Comparison Workspace";
  return "Results";
}

function activeLibraryLabel(value: LibraryKind): string {
  const tab = libraryTabs.find((item) => item.id === value);
  return tab?.label ?? "Library";
}

function applyBenchmarkSuiteSelection(
  draft: ExperimentDraft,
  library: LibraryState,
  suiteId: string,
  split: DatasetSplit | "",
): ExperimentDraft {
  if (!suiteId) {
    return { ...draft, benchmarkSuiteId: undefined, suiteSplit: split || undefined };
  }
  const suite = library.benchmarkSuites.find((item) => item.id === suiteId);
  if (!suite) {
    return { ...draft, benchmarkSuiteId: suiteId, suiteSplit: split || undefined };
  }
  const caseIds = parseIdList(suite.caseIdsText);
  const selectedCaseIds = split
    ? caseIds.filter((caseId) =>
        library.cases.some((item) => item.id === caseId && item.datasetSplit === split),
      )
    : caseIds;
  return {
    ...draft,
    benchmarkSuiteId: suite.id,
    suiteSplit: split || undefined,
    selectedCaseIds,
    selectedModelConfigIds: parseIdList(suite.modelConfigIdsText),
    selectedSystemPromptIds: parseIdList(suite.systemPromptIdsText),
    selectedWarmerIds: parseIdList(suite.warmerIdsText),
    selectedEvaluatorIds: parseIdList(suite.evaluatorIdsText),
    controls: {
      ...draft.controls,
      ...suiteControls(suite),
    },
  };
}

function parseIdList(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function suiteControls(suite: BenchmarkSuiteRecord): Partial<ExperimentDraft["controls"]> {
  try {
    const controls = parseJsonObject(suite.controlsJson);
    const mapped: Partial<ExperimentDraft["controls"]> = {};
    if (typeof controls.replicates === "number") mapped.replicates = controls.replicates;
    if (typeof controls.max_parallel_requests === "number") {
      mapped.maxParallelRequests = controls.max_parallel_requests;
    }
    if (typeof controls.max_total_cost_usd === "number") {
      mapped.maxTotalCostUsd = controls.max_total_cost_usd;
    }
    if (typeof controls.retry_failed === "boolean") mapped.retryFailed = controls.retry_failed;
    if (typeof controls.cache_provider_calls === "boolean") {
      mapped.cacheProviderCalls = controls.cache_provider_calls;
    }
    if (typeof controls.local_only === "boolean") mapped.localOnly = controls.local_only;
    return mapped;
  } catch {
    return {};
  }
}

function parseTags(value: string): string[] {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function parseJsonArray(value: string): unknown[] {
  const parsed: unknown = JSON.parse(value || "[]");
  if (!Array.isArray(parsed)) {
    throw new Error("Expected a JSON array.");
  }
  return parsed;
}

function withRecordId<T extends { id: string; name: string }>(record: T): T {
  const name = record.name.trim() || "Untitled";
  return { ...record, name, id: record.id.trim() || slugify(name) };
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export default App;
