from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from model_eval_api.artifact_types import ArtifactInputMode


SUPPORTED_DESIGN_TYPES = {"full_factorial"}
DATASET_SPLITS = {"dev", "validation", "holdout", "archived"}


class ManifestValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


class IdObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str

    @field_validator("id")
    @classmethod
    def id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("id must not be blank")
        return value


class CaseManifest(IdObject):
    prompt: str | None = None
    prompt_ref: str | None = None
    version: int | None = None


class SystemPromptManifest(IdObject):
    prompt: str | None = None
    prompt_ref: str | None = None
    messages: list[dict[str, Any]] | None = None
    version: int | None = None


class WarmerManifest(IdObject):
    prompt: str | None = None
    prompt_ref: str | None = None
    messages: list[dict[str, Any]] | None = None
    version: int | None = None


class ArtifactManifest(IdObject):
    name: str | None = None
    artifact_type: str | None = None
    uri: str | None = None
    input_mode: ArtifactInputMode | None = None
    filename: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    storage_uri: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None


class EvaluatorManifest(IdObject):
    type: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None


class ModelConfigManifest(IdObject):
    provider: str | None = None
    model: str | None = None
    params: Any = Field(default_factory=dict)
    version: int | None = None

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower()

    @property
    def is_library_reference(self) -> bool:
        return self.provider is None and self.model is None and self.params == {}

    @property
    def raw_provider_params(self) -> dict[str, Any]:
        if isinstance(self.params, dict):
            return dict(self.params)
        return {}

    @property
    def temperature(self) -> float | None:
        value = self.raw_provider_params.get("temperature")
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @property
    def reasoning_level(self) -> str | None:
        for key in ("reasoning_effort", "reasoning_level", "thinking_budget"):
            value = self.raw_provider_params.get(key)
            if isinstance(value, str):
                return value
        return None

    def normalized_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "reasoning_level": self.reasoning_level,
            "raw_provider_params": self.raw_provider_params,
        }


class DesignManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "full_factorial"
    replicates: Any = 1
    randomize_run_order: Any = False
    random_seed: Any = None
    cases: list[str] | None = None
    models: list[str] | None = None
    system_prompts: list[str] | None = None
    warmers: list[str] | None = None
    split: str | None = None


class EvaluationManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    evaluators: list[EvaluatorManifest] = Field(default_factory=list)

    @field_validator("evaluators", mode="before")
    @classmethod
    def normalize_evaluators(cls, value: Any) -> Any:
        return _normalize_id_objects(value)


class ControlsManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_parallel_requests: int | None = None
    max_total_cost_usd: float | None = None
    reliability_replicates: Any = None
    context_budget_tokens: int | None = None
    max_context_tokens: int | None = None
    truncation_policy: str = "fail_on_over_budget"
    data_egress_label: str | None = None
    retry_failed: bool | None = None
    cache_provider_calls: bool | None = None
    local_only: bool | None = None


class BenchmarkSuiteReference(IdObject):
    version: int | None = None
    split: str | None = None


class ExperimentManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str
    suite: BenchmarkSuiteReference | None = None
    cases: list[CaseManifest] = Field(default_factory=list)
    artifacts: list[ArtifactManifest] = Field(default_factory=list)
    models: list[ModelConfigManifest] = Field(default_factory=list)
    system_prompts: list[SystemPromptManifest] = Field(default_factory=list)
    warmers: list[WarmerManifest] = Field(default_factory=list)
    design: DesignManifest = Field(default_factory=DesignManifest)
    evaluation: EvaluationManifest = Field(default_factory=EvaluationManifest)
    controls: ControlsManifest = Field(default_factory=ControlsManifest)

    @field_validator("cases", mode="before")
    @classmethod
    def normalize_cases(cls, value: Any) -> Any:
        return _normalize_id_objects(value)

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifacts(cls, value: Any) -> Any:
        return _normalize_id_objects(value)

    @field_validator("models", mode="before")
    @classmethod
    def normalize_models(cls, value: Any) -> Any:
        return _normalize_id_objects(value)

    @field_validator("system_prompts", mode="before")
    @classmethod
    def normalize_system_prompts(cls, value: Any) -> Any:
        return _normalize_id_objects(value)

    @field_validator("warmers", mode="before")
    @classmethod
    def normalize_warmers(cls, value: Any) -> Any:
        return _normalize_id_objects(value)

    @property
    def experiment_id(self) -> str:
        return self.id or self.name


class ManifestValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class RunAttemptDefinition(BaseModel):
    attempt_id: str
    replicate_index: int
    replicate_group_id: str
    attempt_kind: str = "replicate"


class LogicalRunDefinition(BaseModel):
    run_id: str
    experiment_id: str
    case_id: str
    model_config_id: str
    system_prompt_id: str
    warmer_id: str
    attempts: list[RunAttemptDefinition]


class ManifestPreviewResponse(BaseModel):
    design_type: str
    logical_runs: int
    run_attempts: int
    replicates: int
    reliability_replicates: int
    replicate_groups: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: dict[str, int]
    randomize_run_order: bool
    random_seed: int | None
    estimated_token_count: int
    estimated_cost_usd: float
    runs: list[LogicalRunDefinition] = Field(default_factory=list)


def _normalize_id_objects(value: Any) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    for item in value:
        if isinstance(item, str):
            normalized.append({"id": item})
        else:
            normalized.append(item)
    return normalized


def load_manifest_file(path: Path) -> ExperimentManifest:
    try:
        with path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file) or {}
    except FileNotFoundError as error:
        raise ManifestValidationError([f"Manifest file not found: {path}"]) from error
    except OSError as error:
        detail = error.strerror or str(error)
        raise ManifestValidationError([f"Manifest file could not be read: {path}: {detail}"]) from error
    except yaml.YAMLError as error:
        raise ManifestValidationError([f"Manifest file could not be parsed: {error}"]) from error
    if not isinstance(loaded, dict):
        raise ManifestValidationError(["Manifest root must be a YAML mapping."])
    return parse_manifest(loaded)


def parse_manifest(payload: dict[str, Any]) -> ExperimentManifest:
    try:
        manifest = ExperimentManifest.model_validate(payload)
    except ValidationError as error:
        raise ManifestValidationError(_format_pydantic_errors(error)) from error

    result = validate_manifest_payload(manifest)
    if not result.valid:
        raise ManifestValidationError(result.errors)
    return manifest


def validate_manifest_payload(payload: dict[str, Any] | ExperimentManifest) -> ManifestValidationResult:
    if isinstance(payload, ExperimentManifest):
        manifest = payload
        errors: list[str] = []
    elif not isinstance(payload, dict):
        return ManifestValidationResult(valid=False, errors=["Manifest root must be a mapping."])
    else:
        try:
            manifest = ExperimentManifest.model_validate(payload)
        except ValidationError as error:
            return ManifestValidationResult(valid=False, errors=_format_pydantic_errors(error))
        errors = []

    _validate_design(manifest, errors)
    _validate_dimensions(manifest, errors)
    _validate_duplicate_ids("case", manifest.cases, errors)
    _validate_duplicate_ids("artifact", manifest.artifacts, errors)
    _validate_duplicate_ids("model", manifest.models, errors)
    _validate_duplicate_ids("system prompt", manifest.system_prompts, errors)
    _validate_duplicate_ids("warmer", manifest.warmers, errors)
    _validate_duplicate_ids("evaluator", manifest.evaluation.evaluators, errors)
    _validate_models(manifest.models, errors)
    _validate_controls(manifest.controls, errors)
    _validate_design_references(manifest, errors)
    return ManifestValidationResult(valid=not errors, errors=errors)


def expand_manifest(manifest: ExperimentManifest) -> ManifestPreviewResponse:
    result = validate_manifest_payload(manifest)
    if not result.valid:
        raise ManifestValidationError(result.errors)

    case_ids = _selected_ids(manifest.cases, manifest.design.cases)
    model_ids = _selected_ids(manifest.models, manifest.design.models)
    system_prompt_ids = _selected_ids(manifest.system_prompts, manifest.design.system_prompts)
    warmer_ids = _selected_ids(manifest.warmers, manifest.design.warmers)
    replicates = int(manifest.design.replicates)
    random_seed = _random_seed(manifest)

    runs: list[LogicalRunDefinition] = []
    for case_id in case_ids:
        for model_id in model_ids:
            for system_prompt_id in system_prompt_ids:
                for warmer_id in warmer_ids:
                    run_base = {
                        "experiment_id": manifest.experiment_id,
                        "case_id": case_id,
                        "model_config_id": model_id,
                        "system_prompt_id": system_prompt_id,
                        "warmer_id": warmer_id,
                    }
                    replicate_group_id = _stable_id("group", run_base)
                    attempts = [
                        RunAttemptDefinition(
                            attempt_id=_stable_id("attempt", {**run_base, "replicate_index": index}),
                            replicate_index=index,
                            replicate_group_id=replicate_group_id,
                            attempt_kind="replicate",
                        )
                        for index in range(replicates)
                    ]
                    runs.append(
                        LogicalRunDefinition(
                            run_id=_stable_id("run", run_base),
                            attempts=attempts,
                            **run_base,
                        )
                    )

    if manifest.design.randomize_run_order:
        random.Random(random_seed).shuffle(runs)

    return ManifestPreviewResponse(
        design_type=manifest.design.type,
        logical_runs=len(runs),
        run_attempts=len(runs) * replicates,
        replicates=replicates,
        reliability_replicates=min(
            _reliability_replicates(manifest, replicates), replicates
        ),
        replicate_groups=[
            {
                "replicate_group_id": run.attempts[0].replicate_group_id,
                "sample_size": len(run.attempts),
                "attempt_ids": [attempt.attempt_id for attempt in run.attempts],
            }
            for run in runs
            if run.attempts
        ],
        dimensions={
            "cases": len(case_ids),
            "models": len(model_ids),
            "system_prompts": len(system_prompt_ids),
            "warmers": len(warmer_ids),
        },
        randomize_run_order=manifest.design.randomize_run_order,
        random_seed=random_seed if manifest.design.randomize_run_order else manifest.design.random_seed,
        estimated_token_count=0,
        estimated_cost_usd=0.0,
        runs=runs,
    )


def _format_pydantic_errors(error: ValidationError) -> list[str]:
    messages: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        messages.append(f"Manifest field '{location}' is invalid: {item['msg']}.")
    return messages


def _validate_design(manifest: ExperimentManifest, errors: list[str]) -> None:
    if manifest.design.type not in SUPPORTED_DESIGN_TYPES:
        errors.append(
            f"Design type '{manifest.design.type}' is not supported; supported types: full_factorial."
        )
    if type(manifest.design.replicates) is not int or manifest.design.replicates < 1:
        errors.append("Design replicates must be an integer greater than or equal to 1.")
    if type(manifest.design.randomize_run_order) is not bool:
        errors.append("Design randomize_run_order must be a boolean.")
    if manifest.design.random_seed is not None and type(manifest.design.random_seed) is not int:
        errors.append("Design random_seed must be an integer when provided.")
    if manifest.design.split is not None and manifest.design.split not in DATASET_SPLITS:
        errors.append(
            "Design split must be one of: archived, dev, holdout, validation."
        )
    if manifest.suite is not None and manifest.suite.split is not None:
        if manifest.suite.split not in DATASET_SPLITS:
            errors.append(
                "Benchmark suite split must be one of: archived, dev, holdout, validation."
            )
        elif manifest.suite.split == "archived":
            errors.append("Benchmark suite split 'archived' cannot be executed.")


def _validate_dimensions(manifest: ExperimentManifest, errors: list[str]) -> None:
    dimensions: dict[str, list[IdObject]] = {
        "cases": manifest.cases,
        "models": manifest.models,
        "system_prompts": manifest.system_prompts,
        "warmers": manifest.warmers,
    }
    for name, values in dimensions.items():
        if not values:
            errors.append(f"Manifest dimension '{name}' must include at least one item.")


def _validate_duplicate_ids(label: str, values: list[IdObject], errors: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value.id in seen:
            errors.append(f"Duplicate {label} id '{value.id}'.")
        seen.add(value.id)


def _validate_models(models: list[ModelConfigManifest], errors: list[str]) -> None:
    for model in models:
        if model.is_library_reference:
            continue
        if not model.provider:
            errors.append(f"Model '{model.id}' must include provider.")
        if not model.model:
            errors.append(f"Model '{model.id}' must include model.")
        if not isinstance(model.params, dict):
            errors.append(f"Model '{model.id}' params must be a mapping of provider parameters.")


def _validate_controls(controls: ControlsManifest, errors: list[str]) -> None:
    if controls.reliability_replicates is not None and (
        type(controls.reliability_replicates) is not int or controls.reliability_replicates < 1
    ):
        errors.append("Controls reliability_replicates must be an integer greater than or equal to 1.")
    for field_name in ("context_budget_tokens", "max_context_tokens"):
        value = getattr(controls, field_name)
        if value is not None and (type(value) is not int or value < 1):
            errors.append(f"Controls {field_name} must be an integer greater than or equal to 1.")
    if controls.truncation_policy != "fail_on_over_budget":
        errors.append("Controls truncation_policy must be 'fail_on_over_budget' for the MVP.")


def _validate_design_references(manifest: ExperimentManifest, errors: list[str]) -> None:
    references: list[tuple[str, list[str] | None, list[IdObject]]] = [
        ("case", manifest.design.cases, manifest.cases),
        ("model", manifest.design.models, manifest.models),
        ("system prompt", manifest.design.system_prompts, manifest.system_prompts),
        ("warmer", manifest.design.warmers, manifest.warmers),
    ]
    for label, selected_ids, values in references:
        if selected_ids is None:
            continue
        if not selected_ids:
            errors.append(f"Design {label} selection must include at least one item.")
            continue
        selected_seen: set[str] = set()
        known_ids = {value.id for value in values}
        for selected_id in selected_ids:
            if selected_id in selected_seen:
                errors.append(f"Duplicate design {label} reference '{selected_id}'.")
            selected_seen.add(selected_id)
            if selected_id not in known_ids:
                errors.append(f"Unknown design {label} reference '{selected_id}'.")


def _selected_ids(values: list[IdObject], selected_ids: list[str] | None) -> list[str]:
    if selected_ids is None:
        return [value.id for value in values]
    return list(selected_ids)


def _random_seed(manifest: ExperimentManifest) -> int:
    if manifest.design.random_seed is not None:
        return manifest.design.random_seed
    digest = hashlib.sha256(manifest.experiment_id.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _reliability_replicates(manifest: ExperimentManifest, default: int) -> int:
    value = manifest.controls.reliability_replicates
    if isinstance(value, int) and value >= 1:
        return value
    return default


def _stable_id(prefix: Literal["run", "attempt", "group"], payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    return f"{prefix}_{digest}"
