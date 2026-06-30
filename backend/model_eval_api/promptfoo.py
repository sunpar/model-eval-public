from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from model_eval_api.manifest import (
    ExperimentManifest,
    ManifestPreviewResponse,
    expand_manifest,
    parse_manifest,
)
from model_eval_api.persistence import repositories
from model_eval_api.persistence.models import (
    Case,
    ConversationWarmer,
    Evaluator,
    Experiment,
    MetricAdapterConfig,
    ModelConfig,
    Project,
    SystemPrompt,
)


@dataclass(frozen=True)
class PromptfooImportPreview:
    manifest: ExperimentManifest
    run_preview: ManifestPreviewResponse
    warnings: list[dict[str, str]]
    library_records: dict[str, list[dict[str, Any]]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "manifest": _manifest_payload(self.manifest),
            "preview": self.run_preview.model_dump(mode="json"),
            "warnings": list(self.warnings),
            "library_records": self.library_records,
        }


@dataclass(frozen=True)
class PromptfooExport:
    content: str
    warnings: list[dict[str, str]]

    def to_payload(self) -> dict[str, Any]:
        return {"format": "promptfoo", "content": self.content, "warnings": self.warnings}


def preview_promptfoo_import(path: Path) -> PromptfooImportPreview:
    with path.open("r", encoding="utf-8") as file:
        loaded = _load_promptfoo_payload(file.read())
    return build_promptfoo_import_preview(loaded, source_name=path.stem)


def preview_promptfoo_import_content(
    content: str, *, source_name: str = "promptfoo_import"
) -> PromptfooImportPreview:
    loaded = _load_promptfoo_payload(content)
    return build_promptfoo_import_preview(loaded, source_name=source_name)


def _load_promptfoo_payload(content: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(content) or {}
    except yaml.YAMLError as error:
        detail = " ".join(str(error).split())
        raise ValueError(f"Promptfoo config could not be parsed: {detail}") from error
    if not isinstance(loaded, dict):
        raise ValueError("Promptfoo config root must be a YAML or JSON mapping.")
    return loaded


def build_promptfoo_import_preview(
    payload: dict[str, Any], *, source_name: str = "promptfoo_import"
) -> PromptfooImportPreview:
    warnings: list[dict[str, str]] = []
    _warn_unsupported_keys(
        payload,
        "$",
        {
            "description",
            "prompts",
            "providers",
            "targets",
            "tests",
            "defaultTest",
            "options",
            "evaluateOptions",
        },
        warnings,
        "unsupported_top_level_field",
    )

    default_test = _default_test(payload.get("defaultTest"), warnings)
    prompt_records = _prompt_records(payload.get("prompts"), warnings)
    provider_source = payload.get("providers") if "providers" in payload else payload.get("targets")
    provider_path = "$.providers" if "providers" in payload else "$.targets"
    provider_records = _provider_records(provider_source, warnings, path_root=provider_path)
    case_records = _case_records(payload.get("tests"), warnings, default_test["vars"])
    assertion_records = _assertion_records(payload, warnings)
    controls = _controls(payload.get("options"), warnings)
    controls.update(_controls(payload.get("evaluateOptions"), warnings, path_root="$.evaluateOptions"))

    if not prompt_records["system_prompts"]:
        warnings.append(
            _warning(
                "missing_system_prompt",
                "$.prompts",
                "No Promptfoo prompt mapped to a system prompt; added a default prompt reference.",
            )
        )
        prompt_records["system_prompts"].append(
            {"id": "promptfoo_default_system_prompt", "prompt": None}
        )
    if not provider_records:
        warnings.append(
            _warning(
                "missing_provider",
                provider_path,
                "No Promptfoo provider mapped to a model config; added an id-only model reference.",
            )
        )
        provider_records.append({"id": "promptfoo_missing_provider"})
    if not case_records and not prompt_records["cases"]:
        warnings.append(
            _warning(
                "missing_tests",
                "$.tests",
                "No Promptfoo tests mapped to cases; added a placeholder case.",
            )
        )
        case_records.append({"id": "promptfoo_case", "prompt": None})

    manifest_payload = {
        "name": str(payload.get("description") or source_name),
        "cases": [*prompt_records["cases"], *case_records],
        "models": provider_records,
        "system_prompts": prompt_records["system_prompts"],
        "warmers": ["none"],
        "design": {"type": "full_factorial", "replicates": 1},
        "evaluation": {"evaluators": assertion_records["evaluators"]},
        "controls": controls,
    }
    manifest = parse_manifest(manifest_payload)
    return PromptfooImportPreview(
        manifest=manifest,
        run_preview=expand_manifest(manifest),
        warnings=warnings,
        library_records={"metric_adapter_configs": assertion_records["metric_adapter_configs"]},
    )


def persist_promptfoo_import(
    session: Session, *, project: Project, preview: PromptfooImportPreview
) -> dict[str, Any]:
    created = {
        "cases": 0,
        "system_prompts": 0,
        "warmers": 0,
        "model_configs": 0,
        "evaluators": 0,
        "metric_adapter_configs": 0,
    }
    for item in preview.manifest.cases:
        repositories.create_case(
            session,
            project=project,
            slug=item.id,
            name=_extra_string(item.model_extra, "name") or _title(item.id),
            prompt=item.prompt,
            prompt_ref=item.prompt_ref,
            version=_next_version(session, Case, project, item.id),
        )
        created["cases"] += 1

    for item in preview.manifest.system_prompts:
        repositories.create_system_prompt(
            session,
            project=project,
            slug=item.id,
            name=_extra_string(item.model_extra, "name") or _title(item.id),
            prompt=item.prompt,
            prompt_ref=item.prompt_ref,
            messages=item.messages,
            version=_next_version(session, SystemPrompt, project, item.id),
        )
        created["system_prompts"] += 1

    for item in preview.manifest.warmers:
        repositories.create_conversation_warmer(
            session,
            project=project,
            slug=item.id,
            name=_title(item.id),
            messages=item.messages,
            version=_next_version(session, ConversationWarmer, project, item.id),
        )
        created["warmers"] += 1

    for item in preview.manifest.models:
        if item.is_library_reference:
            continue
        repositories.create_model_config(
            session,
            project=project,
            slug=item.id,
            name=_extra_string(item.model_extra, "name") or _title(item.id),
            provider=str(item.provider),
            model=str(item.model),
            raw_provider_params=item.raw_provider_params,
            version=_next_version(session, ModelConfig, project, item.id),
        )
        created["model_configs"] += 1

    for item in preview.manifest.evaluation.evaluators:
        repositories.create_evaluator(
            session,
            project=project,
            slug=item.id,
            name=_extra_string(item.model_extra, "name") or _title(item.id),
            evaluator_type=item.type or "deterministic",
            definition=item.definition,
            version=_next_version(session, Evaluator, project, item.id),
        )
        created["evaluators"] += 1

    for item in preview.library_records["metric_adapter_configs"]:
        repositories.create_metric_adapter_config(
            session,
            project=project,
            slug=item["slug"],
            name=item["name"],
            adapter_kind=item["adapter_kind"],
            adapter_version=item["adapter_version"],
            required_inputs=item["required_inputs"],
            output_schema=item["output_schema"],
            capability_metadata=item["capability_metadata"],
            local_only=item["local_only"],
            version=_next_version(session, MetricAdapterConfig, project, item["slug"]),
        )
        created["metric_adapter_configs"] += 1
    return {"project_slug": project.slug, "created": created}


def export_experiment_to_promptfoo(experiment: Experiment) -> PromptfooExport:
    warnings: list[dict[str, str]] = []
    assertions = _promptfoo_assertions_from_evaluators(
        experiment.evaluator_snapshots,
        warnings,
    )
    payload: dict[str, Any] = {
        "description": experiment.name,
        "prompts": _promptfoo_export_prompts(experiment.system_prompt_snapshots, warnings),
        "providers": _promptfoo_export_providers(experiment.model_config_snapshots),
        "tests": _promptfoo_export_tests(experiment.case_snapshots, assertions, warnings),
    }
    options = _promptfoo_export_options(experiment.controls_snapshot, warnings)
    if options:
        payload["options"] = options
    _warn_promptfoo_warmer_loss(experiment.warmer_snapshots, warnings)
    _warn_promptfoo_design_loss(experiment.design_snapshot, warnings)
    content = yaml.safe_dump(payload, sort_keys=False)
    if warnings:
        warning_lines = ["# Promptfoo export warnings:"]
        warning_lines.extend(f"# - {warning['path']}: {warning['message']}" for warning in warnings)
        content = "\n".join(warning_lines) + "\n" + content
    return PromptfooExport(content=content, warnings=warnings)


def _prompt_records(value: Any, warnings: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {"system_prompts": [], "cases": []}
    prompts = _as_list(value)
    for index, item in enumerate(prompts):
        path = f"$.prompts[{index}]"
        prompt_id = f"promptfoo_prompt_{index + 1}"
        if isinstance(item, str):
            if "{{" in item:
                warnings.append(
                    _warning(
                        "ambiguous_prompt_shape",
                        path,
                        "Prompt template variables may represent case inputs; mapped to system prompt.",
                    )
                )
            records["system_prompts"].append(
                {"id": _slugify(item[:40], prompt_id), "prompt": item}
            )
            continue
        if not isinstance(item, dict):
            warnings.append(
                _warning("unsupported_prompt", path, "Prompt entry is not a string or mapping.")
            )
            continue
        _warn_unsupported_keys(
            item,
            path,
            {"id", "label", "raw", "prompt", "messages"},
            warnings,
            "unsupported_prompt_field",
        )
        prompt_id = _slugify(
            str(item.get("id") or item.get("label") or f"promptfoo_prompt_{index + 1}"),
            prompt_id,
        )
        messages = item.get("messages")
        if isinstance(messages, list):
            roles = {str(message.get("role") or "").lower() for message in messages if isinstance(message, dict)}
            if roles and roles <= {"user"}:
                records["cases"].append(
                    {
                        "id": prompt_id,
                        "prompt": "\n".join(_message_content(message) for message in messages),
                        "messages": messages,
                    }
                )
            else:
                if "user" in roles:
                    warnings.append(
                        _warning(
                            "ambiguous_prompt_shape",
                            path,
                            "Mixed-role Promptfoo messages were mapped to a system prompt.",
                        )
                    )
                records["system_prompts"].append({"id": prompt_id, "messages": messages})
            continue
        text = item.get("raw") or item.get("prompt")
        if not isinstance(text, str):
            warnings.append(_warning("unsupported_prompt", path, "Prompt mapping has no raw text."))
            continue
        if "{{" in text:
            warnings.append(
                _warning(
                    "ambiguous_prompt_shape",
                    path,
                    "Prompt template variables may represent case inputs; mapped to system prompt.",
                )
            )
        records["system_prompts"].append({"id": prompt_id, "prompt": text})
    return records


def _provider_records(
    value: Any, warnings: list[dict[str, str]], *, path_root: str = "$.providers"
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(value)):
        path = f"{path_root}[{index}]"
        provider_id: str | None = None
        label: str | None = None
        config: dict[str, Any] = {}
        if isinstance(item, str):
            provider_id = item
        elif isinstance(item, dict):
            _warn_unsupported_keys(
                item,
                path,
                {"id", "label", "config"},
                warnings,
                "unsupported_provider_field",
            )
            provider_id = _string_or_none(item.get("id"))
            label = _string_or_none(item.get("label"))
            if isinstance(item.get("config"), dict):
                config = dict(item["config"])
        else:
            warnings.append(
                _warning("unsupported_provider", path, "Provider entry is not a string or mapping.")
            )
            continue
        if provider_id is None:
            warnings.append(_warning("unsupported_provider", path, "Provider entry has no id."))
            continue
        provider, model = _provider_model(provider_id)
        raw_params = {**config, "promptfoo_provider_id": provider_id}
        records.append(
            {
                "id": _slugify(label or provider_id, f"provider_{index + 1}"),
                "provider": provider,
                "model": model,
                "params": raw_params,
                "name": label or provider_id,
            }
        )
    return records


def _case_records(
    value: Any, warnings: list[dict[str, str]], default_vars: dict[str, Any]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for index, item in enumerate(_as_list(value)):
        path = f"$.tests[{index}]"
        if not isinstance(item, dict):
            warnings.append(_warning("unsupported_test", path, "Test entry is not a mapping."))
            continue
        _warn_unsupported_keys(
            item,
            path,
            {"description", "vars", "assert", "options"},
            warnings,
            "unsupported_test_field",
        )
        variables = dict(default_vars)
        if "vars" in item:
            variables.update(
                _vars_mapping_or_warning(
                    item["vars"],
                    warnings,
                    path=f"{path}.vars",
                    invalid_warning_code="unsupported_test_vars",
                    source_warning_code="unsupported_test_vars_source",
                    invalid_message="Promptfoo test vars must be a mapping or file path source.",
                    source_message="Promptfoo test vars file sources are not loaded.",
                )
            )
        description = str(item.get("description") or f"promptfoo_case_{index + 1}")
        case_id = _unique_slug(
            _slugify(description, f"promptfoo_case_{index + 1}"),
            case_ids,
            warnings=warnings,
            path=f"{path}.description",
            warning_code="duplicate_case_id",
        )
        records.append(
            {
                "id": case_id,
                "name": description,
                "prompt": _case_prompt(variables, description),
                "variables": dict(variables),
            }
        )
        if isinstance(item.get("options"), dict):
            for key in item["options"]:
                warnings.append(
                    _warning(
                        "unsupported_test_option",
                        f"{path}.options.{key}",
                        f"Promptfoo test option '{key}' is not mapped.",
                    )
                )
    return records


def _assertion_records(
    payload: dict[str, Any], warnings: list[dict[str, str]]
) -> dict[str, list[dict[str, Any]]]:
    evaluators: list[dict[str, Any]] = []
    metric_adapters: list[dict[str, Any]] = []
    seen_evaluators: dict[str, str] = {}
    seen_adapters: dict[str, str] = {}
    evaluator_slugs: set[str] = set()
    adapter_slugs: set[str] = set()
    assertions = _assertions_with_paths(payload.get("defaultTest"), "$.defaultTest.assert")
    for test_index, test in enumerate(_as_list(payload.get("tests"))):
        assertions.extend(_assertions_with_paths(test, f"$.tests[{test_index}].assert"))
    for assertion, path in assertions:
        if not isinstance(assertion, dict):
            warnings.append(
                _warning("unsupported_assertion", path, "Assertion entry is not a mapping.")
            )
            continue
        assertion_type = _normalized_assertion_type(assertion.get("type"))
        if assertion_type in {"not_empty", "not_empty_output"}:
            slug = _dedupe_slug(
                "promptfoo_not_empty",
                {"kind": "no_empty_output"},
                seen_evaluators,
                evaluator_slugs,
            )
            if slug is not None:
                evaluators.append(
                    {
                        "id": slug,
                        "type": "deterministic",
                        "definition": {
                            "kind": "no_empty_output",
                            "criterion": slug,
                            "promptfoo_assertion_type": str(assertion.get("type")),
                        },
                    }
                )
        elif assertion_type in {"is_json", "json_schema"}:
            schema = assertion.get("value") if isinstance(assertion.get("value"), dict) else {}
            slug = _dedupe_slug(
                "promptfoo_json_schema",
                {"kind": "json_schema", "schema": schema},
                seen_evaluators,
                evaluator_slugs,
            )
            if slug is not None:
                evaluators.append(
                    {
                        "id": slug,
                        "type": "deterministic",
                        "definition": {
                            "kind": "json_schema",
                            "criterion": slug,
                            "schema": schema,
                            "promptfoo_assertion_type": str(assertion.get("type")),
                        },
                    }
                )
        elif assertion_type == "answer_relevance":
            metadata = {"promptfoo_assertion_type": str(assertion.get("type"))}
            threshold = assertion.get("threshold")
            if type(threshold) in {int, float}:
                metadata["threshold"] = float(threshold)
            elif threshold is not None:
                warnings.append(
                    _warning(
                        "unsupported_assertion_threshold",
                        f"{path}.threshold",
                        "Promptfoo answer-relevance threshold must be numeric.",
                    )
                )
            slug = _dedupe_slug(
                "promptfoo_answer_relevance",
                {"kind": "answer_relevance", "metadata": metadata},
                seen_adapters,
                adapter_slugs,
            )
            if slug is not None:
                metric_adapters.append(
                    {
                        "slug": slug,
                        "name": "Promptfoo Answer Relevance",
                        "adapter_kind": "answer_relevance",
                        "adapter_version": "promptfoo-1",
                        "required_inputs": ["answer_text", "reference_answers"],
                        "output_schema": {"type": "object"},
                        "capability_metadata": metadata,
                        "local_only": True,
                    }
                )
        else:
            warnings.append(
                _warning(
                    "unsupported_assertion",
                    path,
                    f"Promptfoo assertion type '{assertion.get('type')}' is not mapped.",
                )
            )
    return {"evaluators": evaluators, "metric_adapter_configs": metric_adapters}


def _default_test(value: Any, warnings: list[dict[str, str]]) -> dict[str, Any]:
    if value is None:
        return {"vars": {}}
    if not isinstance(value, dict):
        warnings.append(
            _warning("unsupported_default_test", "$.defaultTest", "defaultTest must be a mapping.")
        )
        return {"vars": {}}
    _warn_unsupported_keys(
        value,
        "$.defaultTest",
        {"vars", "assert", "options"},
        warnings,
        "unsupported_default_test_field",
    )
    if "options" in value:
        if isinstance(value["options"], dict):
            for key in value["options"]:
                warnings.append(
                    _warning(
                        "unsupported_option",
                        f"$.defaultTest.options.{key}",
                        f"Promptfoo defaultTest option '{key}' is not mapped.",
                    )
                )
        else:
            warnings.append(
                _warning(
                    "unsupported_option",
                    "$.defaultTest.options",
                    "Promptfoo defaultTest options must be a mapping.",
                )
            )
    if "vars" not in value:
        return {"vars": {}}
    return {
        "vars": _vars_mapping_or_warning(
            value["vars"],
            warnings,
            path="$.defaultTest.vars",
            invalid_warning_code="unsupported_default_test_vars",
            source_warning_code="unsupported_default_test_vars_source",
            invalid_message="Promptfoo defaultTest vars must be a mapping or file path source.",
            source_message="Promptfoo defaultTest vars file sources are not loaded.",
        )
    }


def _controls(
    value: Any, warnings: list[dict[str, str]], *, path_root: str = "$.options"
) -> dict[str, Any]:
    controls: dict[str, Any] = {}
    if value is None:
        return controls
    if not isinstance(value, dict):
        warnings.append(_warning("unsupported_option", path_root, "Options must be a mapping."))
        return controls
    for key, item in value.items():
        if key == "maxConcurrency" and _is_positive_int(item):
            controls["max_parallel_requests"] = item
        else:
            warnings.append(
                _warning(
                    "unsupported_option",
                    f"{path_root}.{key}",
                    f"Promptfoo option '{key}' is not mapped.",
                )
            )
    return controls


def _is_positive_int(value: Any) -> bool:
    return type(value) is int and value >= 1


def _promptfoo_export_prompts(
    snapshots: dict[str, dict[str, Any]], warnings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    for slug, snapshot in sorted(snapshots.items()):
        prompt = snapshot.get("prompt")
        messages = snapshot.get("messages")
        if isinstance(prompt, str) and prompt.strip():
            prompts.append({"id": slug, "raw": prompt})
        elif isinstance(messages, list) and messages:
            prompts.append({"id": slug, "messages": messages})
        else:
            warnings.append(
                _warning(
                    "lossy_prompt_mapping",
                    f"$.system_prompts.{slug}",
                    "System prompt has no raw prompt or messages to export.",
                )
            )
    return prompts


def _promptfoo_export_providers(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for _slug, snapshot in sorted(snapshots.items()):
        raw_params = dict(snapshot.get("raw_provider_params") or {})
        provider_id = str(
            raw_params.pop("promptfoo_provider_id", None)
            or f"{snapshot.get('provider')}:{snapshot.get('model')}"
        )
        max_output_tokens = snapshot.get("max_output_tokens")
        if isinstance(max_output_tokens, int) and raw_params.get("max_tokens") == "[redacted]":
            raw_params["max_tokens"] = max_output_tokens
        entry: dict[str, Any] = {"id": provider_id}
        if raw_params:
            entry["config"] = raw_params
        providers.append(entry)
    return providers


def _promptfoo_export_tests(
    snapshots: dict[str, dict[str, Any]],
    assertions: list[dict[str, Any]],
    warnings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for slug, snapshot in sorted(snapshots.items()):
        name = snapshot.get("name")
        test: dict[str, Any] = {
            "description": name if isinstance(name, str) and name != slug else _title(slug),
            "vars": _promptfoo_export_case_vars(slug, snapshot, warnings),
        }
        if assertions:
            test["assert"] = assertions
        tests.append(test)
    return tests


def _promptfoo_export_case_vars(
    slug: str, snapshot: dict[str, Any], warnings: list[dict[str, str]]
) -> dict[str, Any]:
    variables = snapshot.get("variables")
    if isinstance(variables, dict):
        for key, value in sorted(variables.items()):
            if isinstance(value, list):
                warnings.append(
                    _warning(
                        "lossy_case_var_expansion",
                        f"$.cases.{slug}.vars.{key}",
                        "Array-valued case variables may expand differently in Promptfoo unless disableVarExpansion is set.",
                    )
                )
        return dict(variables)
    prompt = snapshot.get("prompt")
    if isinstance(prompt, str) and prompt:
        return {"prompt": prompt}
    if snapshot.get("prompt_ref"):
        warnings.append(
            _warning(
                "lossy_case_prompt_mapping",
                f"$.cases.{slug}",
                "Case prompt_ref is not embedded in Promptfoo export variables.",
            )
        )
        return {}
    warnings.append(
        _warning(
            "lossy_case_prompt_mapping",
            f"$.cases.{slug}",
            "Case has no prompt or variables to export.",
        )
    )
    return {}


def _promptfoo_assertions_from_evaluators(
    snapshots: dict[str, dict[str, Any]], warnings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for slug, snapshot in sorted(snapshots.items(), key=lambda item: _evaluator_sort_key(item[0], item[1])):
        definition = dict(snapshot.get("definition") or {})
        kind = str(definition.get("kind") or "")
        if kind == "no_empty_output":
            assertions.append({"type": "not-empty"})
        elif kind == "json_schema":
            assertion: dict[str, Any] = {"type": "is-json"}
            schema = definition.get("schema")
            if isinstance(schema, dict):
                assertion["value"] = schema
            assertions.append(assertion)
        else:
            warnings.append(
                _warning(
                    "unsupported_evaluator_mapping",
                    f"$.evaluation.evaluators.{slug}",
                    f"Evaluator kind '{kind or 'unknown'}' is not mapped to Promptfoo.",
                )
            )
    return assertions


def _evaluator_sort_key(slug: str, snapshot: dict[str, Any]) -> tuple[int, str]:
    definition = dict(snapshot.get("definition") or {})
    priority = {"no_empty_output": 0, "json_schema": 1}.get(str(definition.get("kind") or ""), 9)
    return priority, slug


def _promptfoo_export_options(
    controls: dict[str, Any], warnings: list[dict[str, str]]
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key, value in sorted((controls or {}).items()):
        if key == "max_parallel_requests" and _is_positive_int(value):
            options["maxConcurrency"] = value
        elif key == "truncation_policy" and value == "fail_on_over_budget":
            continue
        elif value not in (None, [], {}):
            warnings.append(
                _warning(
                    "unsupported_control_mapping",
                    f"$.controls.{key}",
                    f"Control '{key}' is not mapped to Promptfoo options.",
                )
            )
    return options


def _warn_promptfoo_warmer_loss(
    snapshots: dict[str, dict[str, Any]], warnings: list[dict[str, str]]
) -> None:
    for slug, snapshot in sorted(snapshots.items()):
        if slug == "none" and not snapshot.get("messages"):
            continue
        warnings.append(
            _warning(
                "lossy_warmer_mapping",
                f"$.warmers.{slug}",
                "Conversation warmer dimensions are not represented in Promptfoo export.",
            )
        )


def _warn_promptfoo_design_loss(
    design: dict[str, Any], warnings: list[dict[str, str]]
) -> None:
    if design.get("replicates") not in (None, 1):
        warnings.append(
            _warning(
                "lossy_design_mapping",
                "$.design.replicates",
                "Promptfoo export does not represent Model Eval reliability replicates.",
            )
        )
    if design.get("randomize_run_order"):
        warnings.append(
            _warning(
                "lossy_design_mapping",
                "$.design.randomize_run_order",
                "Promptfoo export does not represent randomized run ordering.",
            )
        )


def _manifest_payload(manifest: ExperimentManifest) -> dict[str, Any]:
    payload = manifest.model_dump(mode="json")
    payload["models"] = [model.normalized_dump() for model in manifest.models]
    return payload


def _next_version(session: Session, model: type[Any], project: Project, slug: str) -> int:
    latest = session.scalar(
        select(func.max(model.version)).where(model.project_id == project.id, model.slug == slug)
    )
    return int(latest or 0) + 1


def _assertions_from_mapping(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return []
    return _as_list(value.get("assert"))


def _assertions_with_paths(value: Any, path_root: str) -> list[tuple[Any, str]]:
    return [
        (assertion, f"{path_root}[{index}]")
        for index, assertion in enumerate(_assertions_from_mapping(value))
    ]


def _provider_model(provider_id: str) -> tuple[str, str]:
    provider, separator, model = provider_id.partition(":")
    if separator and provider.strip() and model.strip():
        return provider.strip().lower(), model.strip()
    return provider_id.strip().lower(), provider_id.strip()


def _case_prompt(variables: dict[str, Any], fallback: str) -> str:
    for key in ("prompt", "input", "question", "topic", "query"):
        value = variables.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(variables, sort_keys=True) if variables else fallback


def _vars_mapping_or_warning(
    value: Any,
    warnings: list[dict[str, str]],
    *,
    path: str,
    invalid_warning_code: str,
    source_warning_code: str,
    invalid_message: str,
    source_message: str,
) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if _is_vars_file_source(value):
        warnings.append(_warning(source_warning_code, path, source_message))
        return {}
    warnings.append(_warning(invalid_warning_code, path, invalid_message))
    return {}


def _is_vars_file_source(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(item, str) for item in value)
    )


def _normalized_assertion_type(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _warn_unsupported_keys(
    payload: dict[str, Any],
    path: str,
    supported: set[str],
    warnings: list[dict[str, str]],
    code: str,
) -> None:
    for key in sorted(set(payload) - supported):
        warnings.append(
            _warning(code, f"{path}.{key}", f"Promptfoo field '{key}' is not mapped.")
        )


def _warning(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or fallback


def _unique_slug(
    slug: str,
    used_slugs: set[str],
    *,
    warnings: list[dict[str, str]] | None = None,
    path: str | None = None,
    warning_code: str | None = None,
) -> str:
    if slug not in used_slugs:
        used_slugs.add(slug)
        return slug
    index = 2
    candidate = f"{slug}_{index}"
    while candidate in used_slugs:
        index += 1
        candidate = f"{slug}_{index}"
    used_slugs.add(candidate)
    if warnings is not None and path is not None and warning_code is not None:
        warnings.append(
            _warning(
                warning_code,
                path,
                f"Duplicate generated id '{slug}' was renamed to '{candidate}'.",
            )
        )
    return candidate


def _dedupe_slug(
    base_slug: str,
    signature_payload: dict[str, Any],
    seen_signatures: dict[str, str],
    used_slugs: set[str],
) -> str | None:
    signature = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    if signature in seen_signatures:
        return None
    slug = _unique_slug(base_slug, used_slugs)
    seen_signatures[signature] = slug
    return slug


def _message_content(message: Any) -> str:
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return ""


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extra_string(extra: dict[str, Any] | None, key: str) -> str | None:
    if not extra:
        return None
    return _string_or_none(extra.get(key))


def _title(value: str) -> str:
    return value.replace("_", " ").title()
