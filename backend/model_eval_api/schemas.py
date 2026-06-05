from typing import Any, Literal

from pydantic import BaseModel, Field

from model_eval_api.artifact_types import ArtifactInputMode


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ManifestPreviewRequest(BaseModel):
    case_count: int = Field(ge=0)
    model_count: int = Field(ge=0)
    system_prompt_count: int = Field(ge=0)
    warmer_count: int = Field(ge=0)
    design_type: str = "full_factorial"
    replicates: int = Field(default=1, ge=1)


class CountManifestPreviewResponse(BaseModel):
    design_type: str
    logical_runs: int
    run_attempts: int
    replicates: int


class LibraryItemCreate(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    archived: bool = False


class CaseCreate(LibraryItemCreate):
    prompt: str | None = None
    prompt_ref: str | None = None
    dataset_split: Literal["dev", "validation", "holdout", "archived"] = "dev"


class SystemPromptCreate(LibraryItemCreate):
    prompt: str | None = None
    prompt_ref: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ConversationWarmerCreate(LibraryItemCreate):
    domain: str | None = None
    user_level: str | None = None
    intent: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version_note: str | None = None


class ArtifactCreate(LibraryItemCreate):
    artifact_type: str | None = None
    uri: str | None = None
    input_mode: ArtifactInputMode = ArtifactInputMode.DIRECT_FILE
    filename: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    mime_type: str | None = None
    storage_uri: str | None = None
    image_width: int | None = Field(default=None, ge=0)
    image_height: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactInputModeUpdate(BaseModel):
    input_mode: ArtifactInputMode


class ArtifactPreprocessingCreate(BaseModel):
    parser_name: str = Field(min_length=1)
    parser_version: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    region: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] | None = None
    citation: dict[str, Any] | None = None
    sections: list[dict[str, Any]] | None = None


class ModelConfigCreate(LibraryItemCreate):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    reasoning_level: str | None = None
    capability_flags: dict[str, Any] = Field(default_factory=dict)
    raw_provider_params: dict[str, Any] = Field(default_factory=dict)


class EvaluatorCreate(LibraryItemCreate):
    evaluator_type: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)


class LLMJudgeConfigCreate(LibraryItemCreate):
    judge_prompt: str = Field(min_length=1)
    rubric_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    judge_model_config_slug: str = Field(min_length=1)
    judge_model_config_version: int | None = Field(default=None, ge=1)
    raw_provider_params: dict[str, Any] = Field(default_factory=dict)
    calibration_status: str = "draft"


class MetricAdapterConfigCreate(LibraryItemCreate):
    adapter_kind: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    required_inputs: list[str] = Field(min_length=1)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    capability_metadata: dict[str, Any] = Field(default_factory=dict)
    local_only: bool = True


class BenchmarkSuiteCreate(LibraryItemCreate):
    description: str | None = None
    case_ids: list[str] = Field(min_length=1)
    model_config_ids: list[str] = Field(min_length=1)
    system_prompt_ids: list[str] = Field(min_length=1)
    warmer_ids: list[str] = Field(min_length=1)
    evaluator_ids: list[str] = Field(default_factory=list)
    controls: dict[str, Any] = Field(default_factory=dict)


class ProjectProviderPolicyUpdate(BaseModel):
    provider_allow_list: list[str] = Field(default_factory=list)
    provider_deny_list: list[str] = Field(default_factory=list)


class ReviewSetCreate(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    random_seed: int | None = None
    reviewer_slugs: list[str] = Field(default_factory=list)
    failure_taxonomy_slug: str | None = None


class ReviewerCreate(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    email: str | None = None


class FailureTaxonomyCreate(BaseModel):
    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    archived: bool = False


class ReviewAssignmentCreate(BaseModel):
    reviewer_slugs: list[str] = Field(min_length=1)


class ReviewDecisionCreate(BaseModel):
    reviewer_id: str = Field(default="human")
    winner: Literal["A", "B", "tie", "cannot_judge"]
    pass_fail: dict[str, bool] = Field(default_factory=dict)
    failure_tags: dict[str, list[str]] = Field(default_factory=dict)
    rubric_notes: dict[str, str] = Field(default_factory=dict)
    notes: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class JudgeRunCreate(BaseModel):
    dry_run: bool = True
    local_only: bool = True
    position_swap: bool = True
    random_seed: int | None = 1


class MetricAdapterRunCreate(BaseModel):
    adapter_config_slug: str | None = None
    adapter_config_version: int | None = Field(default=None, ge=1)
    dry_run: bool = False
    local_only: bool = True
    force: bool = False


class PromptfooImportPreviewCreate(BaseModel):
    content: str = Field(min_length=1)
    persist: bool = False
