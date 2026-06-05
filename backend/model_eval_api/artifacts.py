from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
import shlex
import socket
import struct
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from sqlalchemy.orm import Session

from model_eval_api.artifact_types import ArtifactInputMode
from model_eval_api.persistence.repositories import (
    complete_artifact_preprocessing_run,
    create_artifact,
    create_artifact_preprocessing_run,
    fail_artifact_preprocessing_run,
)

if TYPE_CHECKING:
    from model_eval_api.persistence.models import Artifact, ArtifactPreprocessingRun, Project

PDF_TEXT_PARSER_NAME = "pdf_text"
PDF_TEXT_PARSER_VERSION = "1.0.0"
PDF_VISUAL_PARSER_NAME = "pdf_page_screenshots"
PDF_VISUAL_PARSER_VERSION = "1.0.0"
IMAGE_NORMALIZATION_PARSER_NAME = "image_normalization"
IMAGE_NORMALIZATION_PARSER_VERSION = "1.0.0"
FIGURE_EXTRACTION_PARSER_NAME = "selected_figure"
FIGURE_EXTRACTION_PARSER_VERSION = "1.0.0"
TABLE_EXTRACTION_PARSER_NAME = "table_extraction"
TABLE_EXTRACTION_PARSER_VERSION = "1.0.0"
RETRIEVAL_CHUNKS_PARSER_NAME = "retrieval_chunks"
RETRIEVAL_CHUNKS_PARSER_VERSION = "1.0.0"
PAPER_CARD_PARSER_NAME = "paper_card"
PAPER_CARD_PARSER_VERSION = "1.0.0"
ARTIFACT_SLUG_MAX_LENGTH = 160
ARTIFACT_NAME_MAX_LENGTH = 255
PDF_TEXT_SLUG_SUFFIX = "_pdf_text"
PDF_TEXT_NAME_SUFFIX = " extracted text"
PDF_PAGE_SLUG_SUFFIX = "_pdf_page"
IMAGE_NORMALIZED_SLUG_SUFFIX = "_normalized_image"
OCR_TEXT_SLUG_SUFFIX = "_ocr_text"
FIGURE_SLUG_SUFFIX = "_figure"
TABLE_SLUG_SUFFIX = "_table"
RETRIEVAL_CHUNK_SLUG_SUFFIX = "_retrieval_chunk"
PAPER_CARD_SLUG_SUFFIX = "_paper_card"
IMAGE_NORMALIZED_NAME_SUFFIX = " normalized image"
OCR_TEXT_NAME_SUFFIX = " OCR text"
FIGURE_NAME_SUFFIX = " selected figure"
TABLE_NAME_SUFFIX = " extracted table"
RETRIEVAL_CHUNK_NAME_SUFFIX = " retrieval chunk"
PAPER_CARD_NAME_SUFFIX = " paper card"
OCR_COMMAND_ENV = "MODEL_EVAL_OCR_COMMAND"
OCR_TIMEOUT_SECONDS = 30
PDF_SCREENSHOT_DPI = 144


def get_artifact_storage_root(root: str | Path | None = None) -> Path:
    configured = (
        root
        or os.getenv("MODEL_EVAL_ARTIFACT_STORAGE_ROOT")
        or os.getenv("ARTIFACT_STORAGE_ROOT")
        or "~/.model-eval/artifacts"
    )
    return Path(configured).expanduser().resolve()


def coerce_artifact_input_mode(value: ArtifactInputMode | str | None) -> ArtifactInputMode:
    if value is None:
        return ArtifactInputMode.NONE
    if isinstance(value, ArtifactInputMode):
        return value
    return ArtifactInputMode(value)


def register_file_artifact(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    source_path: str | Path,
    storage_root: str | Path | None = None,
    input_mode: ArtifactInputMode | str | None = None,
    artifact_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    version: int = 1,
) -> Artifact:
    path = Path(source_path).expanduser()
    stored = copy_file_to_storage(
        path=path,
        project_slug=project.slug,
        storage_root=storage_root,
    )
    normalized_mode = coerce_artifact_input_mode(input_mode) if input_mode else mode_for_file(stored)
    return create_artifact(
        session,
        project=project,
        slug=slug,
        name=name,
        artifact_type=artifact_type or artifact_type_for_mime(stored["mime_type"]),
        uri=stored["storage_uri"],
        input_mode=normalized_mode,
        filename=stored["filename"],
        checksum_sha256=stored["checksum_sha256"],
        size_bytes=stored["size_bytes"],
        mime_type=stored["mime_type"],
        storage_uri=stored["storage_uri"],
        image_width=stored.get("width"),
        image_height=stored.get("height"),
        metadata=metadata or {},
        version=version,
    )


def ingest_text_artifact(
    session: Session,
    *,
    project: Project,
    slug: str,
    name: str,
    text: str,
    filename: str = "source.txt",
    storage_root: str | Path | None = None,
    input_mode: ArtifactInputMode | str = ArtifactInputMode.PDF_TEXT,
    metadata: dict[str, Any] | None = None,
    version: int = 1,
) -> Artifact:
    stored = write_bytes_to_storage(
        data=text.encode("utf-8"),
        filename=filename,
        project_slug=project.slug,
        storage_root=storage_root,
    )
    artifact_metadata = {"encoding": "utf-8", **(metadata or {})}
    return create_artifact(
        session,
        project=project,
        slug=slug,
        name=name,
        artifact_type="text",
        uri=stored["storage_uri"],
        input_mode=coerce_artifact_input_mode(input_mode),
        filename=stored["filename"],
        checksum_sha256=stored["checksum_sha256"],
        size_bytes=stored["size_bytes"],
        mime_type=stored["mime_type"],
        storage_uri=stored["storage_uri"],
        image_width=stored.get("width"),
        image_height=stored.get("height"),
        metadata=artifact_metadata,
        version=version,
    )


def create_preprocessing_run_for_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    parser_name: str,
    parser_version: str,
    storage_root: str | Path | None = None,
) -> ArtifactPreprocessingRun:
    record = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=parser_name,
        parser_version=parser_version,
        local_storage_uri=source_artifact.storage_uri or source_artifact.uri,
    )
    source_path = local_storage_path(source_artifact.storage_uri or source_artifact.uri)
    source_status = local_artifact_source_failure_status(source_path, storage_root=storage_root)
    if source_status is not None:
        fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind="missing_source",
            error_message="Source file is not available in local artifact storage.",
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": parser_name,
                "source_artifact_id": source_artifact.id,
                "source_path": str(source_path) if source_path is not None else None,
                "source_status": source_status,
            },
        )
    return record


def preprocess_pdf_text_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    storage_root: str | Path | None = None,
    parser_version: str = PDF_TEXT_PARSER_VERSION,
    derived_slug: str | None = None,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=PDF_TEXT_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    source_path = local_storage_path(source_artifact.storage_uri or source_artifact.uri)
    if source_path is None:
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind="missing_source",
            error_message="Source file is not available in local artifact storage.",
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": PDF_TEXT_PARSER_NAME,
                "source_artifact_id": source_artifact.id,
                "source_status": "non_file_uri",
            },
        )

    extracted = extract_pdf_text_pages(source_path)
    if extracted["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=extracted["error_kind"],
            error_message=extracted["error_message"],
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": PDF_TEXT_PARSER_NAME,
                "source_artifact_id": source_artifact.id,
                **extracted["error_metadata"],
            },
        )

    pages = extracted["pages"]
    page_metadata = [
        {
            "page_number": page["page_number"],
            "char_count": page["char_count"],
            "checksum_sha256": page["checksum_sha256"],
        }
        for page in pages
    ]
    derived_text = "\n\n".join(page["text"] for page in pages)
    session.flush()
    derived_artifact = ingest_text_artifact(
        session,
        project=project,
        slug=derived_slug or derived_pdf_text_slug(source_artifact, record.id),
        name=derived_pdf_text_name(source_artifact),
        text=derived_text,
        filename=derived_text_filename(source_artifact),
        storage_root=storage_root,
        input_mode=ArtifactInputMode.PDF_TEXT,
        metadata={
            "source_artifact_id": source_artifact.id,
            "source_checksum_sha256": source_artifact.checksum_sha256,
            "parser_name": PDF_TEXT_PARSER_NAME,
            "parser_version": parser_version,
            "page_count": len(page_metadata),
            "pages": page_metadata,
        },
    )
    session.flush()
    updated_metadata = {
        **dict(derived_artifact.metadata_json),
        "derived_artifact_id": derived_artifact.id,
    }
    derived_artifact.metadata_json = updated_metadata
    derived_artifact.snapshot = derived_artifact.snapshot | {"metadata": updated_metadata}
    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=[derived_artifact],
        local_storage_uri=derived_artifact.storage_uri,
        output_checksums={
            "pdf_text": derived_artifact.checksum_sha256,
            "pages": {
                str(page["page_number"]): page["checksum_sha256"] for page in page_metadata
            },
        },
    )


def preprocess_pdf_visual_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    storage_root: str | Path | None = None,
    parser_version: str = PDF_VISUAL_PARSER_VERSION,
    ocr_command: Sequence[str] | str | None = None,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=PDF_VISUAL_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    source_path = local_storage_path(source_artifact.storage_uri or source_artifact.uri)
    if source_path is None:
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind="missing_source",
            error_message="Source file is not available in local artifact storage.",
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": PDF_VISUAL_PARSER_NAME,
                "source_artifact_id": source_artifact.id,
                "source_status": "non_file_uri",
            },
        )

    session.flush()
    derived_artifacts: list[Artifact] = []
    page_metadata: list[dict[str, Any]] = []
    ocr_output_checksums: dict[str, str] = {}

    def persist_page(page: dict[str, Any]) -> None:
        stored = write_bytes_to_storage(
            data=page["image_bytes"],
            filename=derived_pdf_page_filename(source_artifact, page["page_number"]),
            project_slug=project.slug,
            storage_root=storage_root,
        )
        stored_path = local_storage_path(stored["storage_uri"])
        ocr_result = capture_ocr_text(stored_path, ocr_command=ocr_command)
        page_artifact = create_artifact(
            session,
            project=project,
            slug=derived_pdf_page_slug(source_artifact, record.id, page["page_number"]),
            name=derived_pdf_page_name(source_artifact, page["page_number"]),
            artifact_type="image",
            uri=stored["storage_uri"],
            input_mode=ArtifactInputMode.PDF_PAGE_SCREENSHOTS,
            filename=stored["filename"],
            checksum_sha256=stored["checksum_sha256"],
            size_bytes=stored["size_bytes"],
            mime_type=stored["mime_type"],
            storage_uri=stored["storage_uri"],
            image_width=stored["width"],
            image_height=stored["height"],
            metadata={
                "source_artifact_id": source_artifact.id,
                "source_checksum_sha256": source_artifact.checksum_sha256,
                "parser_name": PDF_VISUAL_PARSER_NAME,
                "parser_version": parser_version,
                "page_number": page["page_number"],
                "width": stored["width"],
                "height": stored["height"],
                "checksum_sha256": stored["checksum_sha256"],
                "ocr": ocr_result_metadata(ocr_result),
            },
        )
        derived_artifacts.append(page_artifact)
        session.flush()
        page_item = {
            "page_number": page["page_number"],
            "width": stored["width"],
            "height": stored["height"],
            "checksum_sha256": stored["checksum_sha256"],
            "derived_artifact_id": page_artifact.id,
            "ocr": ocr_result_metadata(ocr_result),
        }
        if ocr_result["status"] == "captured":
            ocr_artifact = ingest_text_artifact(
                session,
                project=project,
                slug=derived_ocr_text_slug(
                    source_artifact, record.id, page_number=page["page_number"]
                ),
                name=derived_ocr_text_name(source_artifact, page_number=page["page_number"]),
                text=ocr_result["text"],
                filename=derived_ocr_text_filename(source_artifact, page["page_number"]),
                storage_root=storage_root,
                input_mode=ArtifactInputMode.OCR_TEXT,
                metadata={
                    "source_artifact_id": source_artifact.id,
                    "source_checksum_sha256": source_artifact.checksum_sha256,
                    "parser_name": PDF_VISUAL_PARSER_NAME,
                    "parser_version": parser_version,
                    "page_number": page["page_number"],
                    "image_checksum_sha256": stored["checksum_sha256"],
                    "ocr": ocr_result_metadata(ocr_result),
                },
            )
            derived_artifacts.append(ocr_artifact)
            session.flush()
            ocr_metadata = {
                **dict(ocr_artifact.metadata_json),
                "derived_artifact_id": ocr_artifact.id,
            }
            ocr_artifact.metadata_json = ocr_metadata
            ocr_artifact.snapshot = ocr_artifact.snapshot | {"metadata": ocr_metadata}
            page_item["ocr"]["derived_artifact_id"] = ocr_artifact.id
            ocr_output_checksums[str(page["page_number"])] = ocr_result["checksum_sha256"]
        updated_metadata = dict(page_artifact.metadata_json)
        updated_metadata["derived_artifact_id"] = page_artifact.id
        updated_metadata["ocr"] = dict(page_item["ocr"])
        page_artifact.metadata_json = updated_metadata
        page_artifact.snapshot = page_artifact.snapshot | {"metadata": updated_metadata}
        page_metadata.append(page_item)

    rendered = render_pdf_page_images(source_path, on_page=persist_page)
    if rendered["status"] == "failed":
        for artifact in derived_artifacts:
            session.delete(artifact)
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=rendered["error_kind"],
            error_message=rendered["error_message"],
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": PDF_VISUAL_PARSER_NAME,
                "source_artifact_id": source_artifact.id,
                **rendered["error_metadata"],
            },
        )

    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=derived_artifacts,
        local_storage_uri=derived_artifacts[0].storage_uri if derived_artifacts else None,
        output_checksums={
            "pages": {
                str(page["page_number"]): page["checksum_sha256"] for page in page_metadata
            },
            "ocr": ocr_output_checksums,
        },
    )


def preprocess_image_visual_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    storage_root: str | Path | None = None,
    parser_version: str = IMAGE_NORMALIZATION_PARSER_VERSION,
    ocr_command: Sequence[str] | str | None = None,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=IMAGE_NORMALIZATION_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    source_path = local_storage_path(source_artifact.storage_uri or source_artifact.uri)
    if source_path is None:
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind="missing_source",
            error_message="Source file is not available in local artifact storage.",
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": IMAGE_NORMALIZATION_PARSER_NAME,
                "source_artifact_id": source_artifact.id,
                "source_status": "non_file_uri",
            },
        )

    normalized = normalize_image_file(source_path)
    if normalized["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=normalized["error_kind"],
            error_message=normalized["error_message"],
            error_metadata={
                "filename": source_artifact.filename,
                "parser_name": IMAGE_NORMALIZATION_PARSER_NAME,
                "source_artifact_id": source_artifact.id,
                **normalized["error_metadata"],
            },
        )

    session.flush()
    stored = write_bytes_to_storage(
        data=normalized["image_bytes"],
        filename=derived_normalized_image_filename(source_artifact),
        project_slug=project.slug,
        storage_root=storage_root,
    )
    stored_path = local_storage_path(stored["storage_uri"])
    ocr_result = capture_ocr_text(stored_path, ocr_command=ocr_command)
    normalized_metadata = {
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": IMAGE_NORMALIZATION_PARSER_NAME,
        "parser_version": parser_version,
        "original": normalized["original"],
        "normalized": {
            "width": stored["width"],
            "height": stored["height"],
            "mime_type": stored["mime_type"],
            "checksum_sha256": stored["checksum_sha256"],
        },
        "ocr": ocr_result_metadata(ocr_result),
    }
    normalized_artifact = create_artifact(
        session,
        project=project,
        slug=derived_normalized_image_slug(source_artifact, record.id),
        name=derived_normalized_image_name(source_artifact),
        artifact_type="image",
        uri=stored["storage_uri"],
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
        filename=stored["filename"],
        checksum_sha256=stored["checksum_sha256"],
        size_bytes=stored["size_bytes"],
        mime_type=stored["mime_type"],
        storage_uri=stored["storage_uri"],
        image_width=stored["width"],
        image_height=stored["height"],
        metadata=normalized_metadata,
    )
    derived_artifacts = [normalized_artifact]
    output_checksums: dict[str, Any] = {"normalized_image": stored["checksum_sha256"]}
    session.flush()
    if ocr_result["status"] == "captured":
        ocr_artifact = ingest_text_artifact(
            session,
            project=project,
            slug=derived_ocr_text_slug(source_artifact, record.id),
            name=derived_ocr_text_name(source_artifact),
            text=ocr_result["text"],
            filename=derived_ocr_text_filename(source_artifact),
            storage_root=storage_root,
            input_mode=ArtifactInputMode.OCR_TEXT,
            metadata={
                "source_artifact_id": source_artifact.id,
                "source_checksum_sha256": source_artifact.checksum_sha256,
                "parser_name": IMAGE_NORMALIZATION_PARSER_NAME,
                "parser_version": parser_version,
                "image_checksum_sha256": stored["checksum_sha256"],
                "ocr": ocr_result_metadata(ocr_result),
            },
        )
        derived_artifacts.append(ocr_artifact)
        session.flush()
        ocr_metadata = {
            **dict(ocr_artifact.metadata_json),
            "derived_artifact_id": ocr_artifact.id,
        }
        ocr_artifact.metadata_json = ocr_metadata
        ocr_artifact.snapshot = ocr_artifact.snapshot | {"metadata": ocr_metadata}
        updated_metadata = dict(normalized_artifact.metadata_json)
        updated_metadata["ocr"] = {
            **dict(updated_metadata["ocr"]),
            "derived_artifact_id": ocr_artifact.id,
        }
        normalized_artifact.metadata_json = updated_metadata
        normalized_artifact.snapshot = normalized_artifact.snapshot | {
            "metadata": updated_metadata
        }
        output_checksums["ocr"] = ocr_result["checksum_sha256"]

    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=derived_artifacts,
        local_storage_uri=normalized_artifact.storage_uri,
        output_checksums=output_checksums,
    )


def preprocess_selected_figure_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    page_number: int,
    region: dict[str, Any] | None,
    image_bytes: bytes | None = None,
    storage_root: str | Path | None = None,
    parser_version: str = FIGURE_EXTRACTION_PARSER_VERSION,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=FIGURE_EXTRACTION_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    if image_bytes is not None:
        extracted = selected_figure_from_bytes(
            image_bytes,
            page_number=page_number,
            region=region,
            source_size=source_artifact_image_size(source_artifact),
        )
    else:
        source_path = local_storage_path(source_artifact.storage_uri or source_artifact.uri)
        if source_path is None:
            return fail_artifact_preprocessing_run(
                session,
                preprocessing_run=record,
                error_kind="missing_source",
                error_message="Source file is not available in local artifact storage.",
                error_metadata={
                    "filename": source_artifact.filename,
                    "parser_name": FIGURE_EXTRACTION_PARSER_NAME,
                    "source_artifact_id": source_artifact.id,
                    "source_status": "non_file_uri",
                },
            )
        extracted = extract_figure_region(source_path, page_number=page_number, region=region)
    if extracted["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=extracted["error_kind"],
            error_message=extracted["error_message"],
            error_metadata=extraction_failure_metadata(
                parser_name=FIGURE_EXTRACTION_PARSER_NAME,
                source_artifact=source_artifact,
                page_number=page_number,
                failure=extracted,
            ),
        )

    session.flush()
    stored = write_bytes_to_storage(
        data=extracted["image_bytes"],
        filename=derived_figure_filename(source_artifact, page_number),
        project_slug=project.slug,
        storage_root=storage_root,
    )
    metadata = {
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": FIGURE_EXTRACTION_PARSER_NAME,
        "parser_version": parser_version,
        "page_number": page_number,
        "region": extracted["region"],
        "width": stored["width"],
        "height": stored["height"],
        "checksum_sha256": stored["checksum_sha256"],
    }
    figure_artifact = create_artifact(
        session,
        project=project,
        slug=derived_figure_slug(source_artifact, record.id),
        name=derived_figure_name(source_artifact),
        artifact_type="image",
        uri=stored["storage_uri"],
        input_mode=ArtifactInputMode.SELECTED_FIGURES,
        filename=stored["filename"],
        checksum_sha256=stored["checksum_sha256"],
        size_bytes=stored["size_bytes"],
        mime_type=stored["mime_type"],
        storage_uri=stored["storage_uri"],
        image_width=stored["width"],
        image_height=stored["height"],
        metadata=metadata,
    )
    session.flush()
    updated_metadata = {**metadata, "derived_artifact_id": figure_artifact.id}
    figure_artifact.metadata_json = updated_metadata
    figure_artifact.snapshot = figure_artifact.snapshot | {"metadata": updated_metadata}
    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=[figure_artifact],
        local_storage_uri=figure_artifact.storage_uri,
        output_checksums={"figures": {str(page_number): figure_artifact.checksum_sha256}},
    )


def preprocess_table_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    page_number: int,
    region: dict[str, Any] | None,
    table: dict[str, Any],
    storage_root: str | Path | None = None,
    parser_version: str = TABLE_EXTRACTION_PARSER_VERSION,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=TABLE_EXTRACTION_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    page_validation = validate_page_number(page_number)
    if page_validation is not None:
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=page_validation["error_kind"],
            error_message=page_validation["error_message"],
            error_metadata=extraction_failure_metadata(
                parser_name=TABLE_EXTRACTION_PARSER_NAME,
                source_artifact=source_artifact,
                page_number=page_number,
                failure=page_validation,
            ),
        )
    source_size = source_artifact_image_size(source_artifact)
    validated_region = validate_extraction_region(region, source_size=source_size)
    if validated_region["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=validated_region["error_kind"],
            error_message=validated_region["error_message"],
            error_metadata=extraction_failure_metadata(
                parser_name=TABLE_EXTRACTION_PARSER_NAME,
                source_artifact=source_artifact,
                page_number=page_number,
                failure=validated_region,
            ),
        )
    validated_table = validate_table_payload(table)
    if validated_table["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=validated_table["error_kind"],
            error_message=validated_table["error_message"],
            error_metadata=extraction_failure_metadata(
                parser_name=TABLE_EXTRACTION_PARSER_NAME,
                source_artifact=source_artifact,
                page_number=page_number,
                failure=validated_table,
            ),
        )

    session.flush()
    table_payload = validated_table["table"]
    table_bytes = stable_json_bytes(table_payload)
    table_checksum = hashlib.sha256(table_bytes).hexdigest()
    payload = {
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": TABLE_EXTRACTION_PARSER_NAME,
        "parser_version": parser_version,
        "page_number": page_number,
        "region": validated_region["region"],
        "table": table_payload,
    }
    payload_bytes = stable_json_bytes(payload)
    stored = write_bytes_to_storage(
        data=payload_bytes,
        filename=derived_table_filename(source_artifact, page_number),
        project_slug=project.slug,
        storage_root=storage_root,
    )
    table_metadata = structured_table_metadata(table_payload, checksum_sha256=table_checksum)
    metadata = {
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": TABLE_EXTRACTION_PARSER_NAME,
        "parser_version": parser_version,
        "page_number": page_number,
        "region": validated_region["region"],
        "table": table_metadata,
        "checksum_sha256": stored["checksum_sha256"],
    }
    table_artifact = create_artifact(
        session,
        project=project,
        slug=derived_table_slug(source_artifact, record.id),
        name=derived_table_name(source_artifact),
        artifact_type="table",
        uri=stored["storage_uri"],
        input_mode=ArtifactInputMode.TABLE_EXTRACTION,
        filename=stored["filename"],
        checksum_sha256=stored["checksum_sha256"],
        size_bytes=stored["size_bytes"],
        mime_type=stored["mime_type"],
        storage_uri=stored["storage_uri"],
        metadata=metadata,
    )
    session.flush()
    updated_metadata = {**metadata, "derived_artifact_id": table_artifact.id}
    table_artifact.metadata_json = updated_metadata
    table_artifact.snapshot = table_artifact.snapshot | {"metadata": updated_metadata}
    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=[table_artifact],
        local_storage_uri=table_artifact.storage_uri,
        output_checksums={"tables": {str(page_number): table_artifact.checksum_sha256}},
    )


def preprocess_retrieval_chunks_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    chunks: Sequence[Mapping[str, Any]] | None = None,
    storage_root: str | Path | None = None,
    parser_version: str = RETRIEVAL_CHUNKS_PARSER_VERSION,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=RETRIEVAL_CHUNKS_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    source_text = read_source_text_artifact(source_artifact)
    if source_text["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=source_text["error_kind"],
            error_message=source_text["error_message"],
            error_metadata=preprocessing_failure_metadata(
                parser_name=RETRIEVAL_CHUNKS_PARSER_NAME,
                source_artifact=source_artifact,
                failure=source_text,
            ),
        )
    normalized_chunks = normalize_retrieval_chunks(source_text["text"], chunks)
    if normalized_chunks["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=normalized_chunks["error_kind"],
            error_message=normalized_chunks["error_message"],
            error_metadata=preprocessing_failure_metadata(
                parser_name=RETRIEVAL_CHUNKS_PARSER_NAME,
                source_artifact=source_artifact,
                failure=normalized_chunks,
            ),
        )

    session.flush()
    derived_artifacts: list[Artifact] = []
    output_checksums: dict[str, str] = {}
    for chunk in normalized_chunks["chunks"]:
        payload = {
            "source_artifact_id": source_artifact.id,
            "source_checksum_sha256": source_artifact.checksum_sha256,
            "parser_name": RETRIEVAL_CHUNKS_PARSER_NAME,
            "parser_version": parser_version,
            **chunk,
        }
        stored = write_bytes_to_storage(
            data=stable_json_bytes(payload),
            filename=derived_retrieval_chunk_filename(
                source_artifact, chunk["chunk_index"]
            ),
            project_slug=project.slug,
            storage_root=storage_root,
        )
        metadata = {
            "source_artifact_id": source_artifact.id,
            "source_checksum_sha256": source_artifact.checksum_sha256,
            "parser_name": RETRIEVAL_CHUNKS_PARSER_NAME,
            "parser_version": parser_version,
            **chunk,
            "checksum_sha256": stored["checksum_sha256"],
        }
        artifact = create_artifact(
            session,
            project=project,
            slug=derived_retrieval_chunk_slug(
                source_artifact, record.id, chunk["chunk_index"]
            ),
            name=derived_retrieval_chunk_name(source_artifact, chunk["chunk_index"]),
            artifact_type="retrieval_chunk",
            uri=stored["storage_uri"],
            input_mode=ArtifactInputMode.RETRIEVAL_CHUNKS,
            filename=stored["filename"],
            checksum_sha256=stored["checksum_sha256"],
            size_bytes=stored["size_bytes"],
            mime_type=stored["mime_type"],
            storage_uri=stored["storage_uri"],
            metadata=metadata,
        )
        derived_artifacts.append(artifact)
        session.flush()
        updated_metadata = {**metadata, "derived_artifact_id": artifact.id}
        artifact.metadata_json = updated_metadata
        artifact.snapshot = artifact.snapshot | {"metadata": updated_metadata}
        output_checksums[str(chunk["chunk_index"])] = artifact.checksum_sha256

    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=derived_artifacts,
        local_storage_uri=derived_artifacts[0].storage_uri if derived_artifacts else None,
        output_checksums={"retrieval_chunks": output_checksums},
    )


def preprocess_paper_card_artifact(
    session: Session,
    *,
    project: Project,
    source_artifact: Artifact,
    citation: Mapping[str, Any],
    sections: Sequence[Mapping[str, Any]] | None = None,
    storage_root: str | Path | None = None,
    parser_version: str = PAPER_CARD_PARSER_VERSION,
) -> ArtifactPreprocessingRun:
    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source_artifact,
        parser_name=PAPER_CARD_PARSER_NAME,
        parser_version=parser_version,
        storage_root=storage_root,
    )
    if record.status == "failed":
        return record

    source_text = read_source_text_artifact(source_artifact)
    if source_text["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=source_text["error_kind"],
            error_message=source_text["error_message"],
            error_metadata=preprocessing_failure_metadata(
                parser_name=PAPER_CARD_PARSER_NAME,
                source_artifact=source_artifact,
                failure=source_text,
            ),
        )
    citation_result = validate_mapping_payload(
        citation,
        error_kind="invalid_citation",
        error_message="Paper card citation must be a JSON object.",
        serialization_error_message=(
            "Paper card citation must contain only JSON-serializable values."
        ),
        type_key="citation_type",
        reason="citation must be an object",
        serialization_reason="citation must be JSON-serializable",
    )
    if citation_result["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=citation_result["error_kind"],
            error_message=citation_result["error_message"],
            error_metadata=preprocessing_failure_metadata(
                parser_name=PAPER_CARD_PARSER_NAME,
                source_artifact=source_artifact,
                failure=citation_result,
            ),
        )
    section_result = normalize_paper_card_sections(source_text["text"], sections)
    if section_result["status"] == "failed":
        return fail_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            error_kind=section_result["error_kind"],
            error_message=section_result["error_message"],
            error_metadata=preprocessing_failure_metadata(
                parser_name=PAPER_CARD_PARSER_NAME,
                source_artifact=source_artifact,
                failure=section_result,
            ),
        )

    session.flush()
    summary = deterministic_paper_card_summary(section_result["sections"])
    summary_checksum = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    payload = {
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": PAPER_CARD_PARSER_NAME,
        "parser_version": parser_version,
        "citation": citation_result["value"],
        "sections": section_result["sections"],
        "summary": summary,
        "summary_checksum_sha256": summary_checksum,
    }
    stored = write_bytes_to_storage(
        data=stable_json_bytes(payload),
        filename=derived_paper_card_filename(source_artifact),
        project_slug=project.slug,
        storage_root=storage_root,
    )
    metadata = {
        "source_artifact_id": source_artifact.id,
        "source_checksum_sha256": source_artifact.checksum_sha256,
        "parser_name": PAPER_CARD_PARSER_NAME,
        "parser_version": parser_version,
        "citation": citation_result["value"],
        "sections": section_result["sections"],
        "summary": summary,
        "summary_checksum_sha256": summary_checksum,
        "checksum_sha256": stored["checksum_sha256"],
    }
    paper_card = create_artifact(
        session,
        project=project,
        slug=derived_paper_card_slug(source_artifact, record.id),
        name=derived_paper_card_name(source_artifact),
        artifact_type="paper_card",
        uri=stored["storage_uri"],
        input_mode=ArtifactInputMode.PAPER_CARDS,
        filename=stored["filename"],
        checksum_sha256=stored["checksum_sha256"],
        size_bytes=stored["size_bytes"],
        mime_type=stored["mime_type"],
        storage_uri=stored["storage_uri"],
        metadata=metadata,
    )
    session.flush()
    updated_metadata = {**metadata, "derived_artifact_id": paper_card.id}
    paper_card.metadata_json = updated_metadata
    paper_card.snapshot = paper_card.snapshot | {"metadata": updated_metadata}
    return complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=[paper_card],
        local_storage_uri=paper_card.storage_uri,
        output_checksums={"paper_card": paper_card.checksum_sha256},
    )


def extract_pdf_text_pages(path: Path) -> dict[str, Any]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return pdf_text_extraction_failure(
                "encrypted_pdf",
                "Encrypted PDFs are not supported by the local text extractor.",
                {},
            )
        raw_pages = list(reader.pages)
    except (OSError, PdfReadError, ValueError) as exc:
        return pdf_text_extraction_failure(
            "unreadable_pdf",
            "PDF could not be read by the local text extractor.",
            {"exception_type": type(exc).__name__},
        )

    if not raw_pages:
        return pdf_text_extraction_failure(
            "empty_pdf",
            "PDF did not contain any pages for text extraction.",
            {"page_count": 0},
        )

    pages: list[dict[str, Any]] = []
    for index, page in enumerate(raw_pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            return pdf_text_extraction_failure(
                "unreadable_pdf_page",
                "PDF page text could not be read by the local text extractor.",
                {"page_number": index, "exception_type": type(exc).__name__},
            )
        if not text:
            return pdf_text_extraction_failure(
                "empty_pdf_page",
                "PDF page did not contain extractable text.",
                {"page_number": index, "page_count": len(raw_pages)},
            )
        pages.append(
            {
                "page_number": index,
                "text": text,
                "char_count": len(text),
                "checksum_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    return {"status": "completed", "pages": pages}


def render_pdf_page_images(
    path: Path, *, on_page: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    import fitz

    try:
        with fitz.open(str(path)) as document:
            if document.is_encrypted or document.needs_pass:
                return preprocessing_failure(
                    "encrypted_pdf",
                    "Encrypted PDFs are not supported by the local screenshot extractor.",
                    {},
                )
            if document.page_count == 0:
                return preprocessing_failure(
                    "empty_pdf",
                    "PDF did not contain any pages for screenshot extraction.",
                    {"page_count": 0},
                )

            pages: list[dict[str, Any]] = []
            zoom = PDF_SCREENSHOT_DPI / 72
            matrix = fitz.Matrix(zoom, zoom)
            for page_index in range(document.page_count):
                try:
                    page = document.load_page(page_index)
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    image_bytes = pixmap.tobytes("png")
                except (RuntimeError, ValueError) as exc:
                    return preprocessing_failure(
                        "unreadable_pdf_page",
                        "PDF page could not be rendered by the local screenshot extractor.",
                        {
                            "page_number": page_index + 1,
                            "exception_type": type(exc).__name__,
                        },
                    )
                rendered_page = {
                    "page_number": page_index + 1,
                    "width": pixmap.width,
                    "height": pixmap.height,
                    "image_bytes": image_bytes,
                    "checksum_sha256": hashlib.sha256(image_bytes).hexdigest(),
                }
                if on_page is None:
                    pages.append(rendered_page)
                else:
                    on_page(rendered_page)
    except (RuntimeError, ValueError, OSError) as exc:
        return preprocessing_failure(
            "unreadable_pdf",
            "PDF could not be read by the local screenshot extractor.",
            {"exception_type": type(exc).__name__},
        )
    return {"status": "completed", "pages": pages}


def normalize_image_file(path: Path) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            original = {
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "format": image.format,
            }
            has_transparency = "A" in image.getbands() or "transparency" in image.info
            normalized = image.convert("RGBA" if has_transparency else "RGB")
            output = io.BytesIO()
            normalized.save(output, format="PNG")
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        return preprocessing_failure(
            "unreadable_image",
            "Image could not be read by the local normalization extractor.",
            {"exception_type": type(exc).__name__},
        )
    return {
        "status": "completed",
        "original": original,
        "image_bytes": output.getvalue(),
    }


def extract_figure_region(
    path: Path, *, page_number: int, region: dict[str, Any] | None
) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError

    page_validation = validate_page_number(page_number)
    if page_validation is not None:
        return page_validation
    try:
        with Image.open(path) as image:
            source_size = (image.width, image.height)
            validated = validate_extraction_region(region, source_size=source_size)
            if validated["status"] == "failed":
                return validated
            normalized_region = validated["region"]
            crop_box = (
                normalized_region["x"],
                normalized_region["y"],
                normalized_region["x"] + normalized_region["width"],
                normalized_region["y"] + normalized_region["height"],
            )
            figure = image.crop(crop_box).convert("RGBA")
            output = io.BytesIO()
            figure.save(output, format="PNG")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return preprocessing_failure(
            "unreadable_source_image",
            "Source image could not be read for region extraction.",
            {"exception_type": type(exc).__name__},
        )
    return {
        "status": "completed",
        "region": normalized_region,
        "image_bytes": output.getvalue(),
    }


def selected_figure_from_bytes(
    image_bytes: bytes,
    *,
    page_number: int,
    region: dict[str, Any] | None,
    source_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError

    page_validation = validate_page_number(page_number)
    if page_validation is not None:
        return page_validation
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            validated = validate_extraction_region(region, source_size=source_size)
            if validated["status"] == "failed":
                return validated
            normalized_region = validated["region"]
            if source_size is None and (
                normalized_region["x"] + normalized_region["width"] > image.width
                or normalized_region["y"] + normalized_region["height"] > image.height
            ):
                normalized = image
            else:
                crop_box = (
                    normalized_region["x"],
                    normalized_region["y"],
                    normalized_region["x"] + normalized_region["width"],
                    normalized_region["y"] + normalized_region["height"],
                )
                normalized = image.crop(crop_box)
            normalized = normalized.convert(
                "RGBA" if "A" in image.getbands() else "RGB"
            )
            output = io.BytesIO()
            normalized.save(output, format="PNG")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        return preprocessing_failure(
            "unreadable_figure_image",
            "Selected figure image bytes could not be read.",
            {"exception_type": type(exc).__name__},
        )
    return {
        "status": "completed",
        "region": validated["region"],
        "image_bytes": output.getvalue(),
    }


def extraction_failure_metadata(
    *,
    parser_name: str,
    source_artifact: Artifact,
    page_number: int,
    failure: dict[str, Any],
) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "parser_name": parser_name,
        "source_artifact_id": source_artifact.id,
        **dict(failure.get("error_metadata") or {}),
    }


def preprocessing_failure_metadata(
    *, parser_name: str, source_artifact: Artifact, failure: dict[str, Any]
) -> dict[str, Any]:
    return {
        "filename": source_artifact.filename,
        "parser_name": parser_name,
        "source_artifact_id": source_artifact.id,
        **dict(failure.get("error_metadata") or {}),
    }


def validate_page_number(page_number: int) -> dict[str, Any] | None:
    if isinstance(page_number, int) and not isinstance(page_number, bool) and page_number > 0:
        return None
    return preprocessing_failure(
        "invalid_page_number",
        "Extraction page number must be a positive integer.",
        {"page_number": page_number, "reason": "page number must be positive"},
    )


def validate_extraction_region(
    region: Any, *, source_size: tuple[int, int] | None = None
) -> dict[str, Any]:
    if region is None:
        return preprocessing_failure(
            "missing_region",
            "Extraction region is required.",
            {"reason": "region is required"},
        )
    if not isinstance(region, Mapping):
        return preprocessing_failure(
            "invalid_region",
            "Extraction region must be an object.",
            {"region_type": type(region).__name__, "reason": "region must be an object"},
        )
    required = ("x", "y", "width", "height")
    if any(key not in region for key in required):
        return preprocessing_failure(
            "invalid_region",
            "Extraction region must include x, y, width, and height.",
            {
                "region": dict(region),
                "region_keys": sorted(region),
                "reason": "region must include x, y, width, and height",
            },
        )
    normalized: dict[str, int] = {}
    for key in required:
        value = region[key]
        if not isinstance(value, int) or isinstance(value, bool):
            return preprocessing_failure(
                "invalid_region",
                "Extraction region values must be integers.",
                {
                    "region": dict(region),
                    "field": key,
                    "value_type": type(value).__name__,
                    "reason": "region values must be integers",
                },
            )
        normalized[key] = value
    if normalized["x"] < 0 or normalized["y"] < 0:
        return preprocessing_failure(
            "invalid_region",
            "Extraction region origin must be non-negative.",
            {"region": normalized, "reason": "x and y must be non-negative"},
        )
    if normalized["width"] <= 0 or normalized["height"] <= 0:
        return preprocessing_failure(
            "invalid_region",
            "Extraction region width and height must be positive.",
            {"region": normalized, "reason": "width and height must be positive"},
        )
    if source_size is not None:
        source_width, source_height = source_size
        if (
            normalized["x"] + normalized["width"] > source_width
            or normalized["y"] + normalized["height"] > source_height
        ):
            return preprocessing_failure(
                "invalid_region",
                "Extraction region must fit within the source artifact bounds.",
                {
                    "region": normalized,
                    "source_width": source_width,
                    "source_height": source_height,
                    "reason": "region must fit within the source artifact bounds",
                },
            )
    return {"status": "completed", "region": normalized}


def validate_table_payload(table: Any) -> dict[str, Any]:
    if not isinstance(table, Mapping):
        return preprocessing_failure(
            "invalid_table",
            "Extracted table payload must be a JSON object.",
            {"table_type": type(table).__name__, "reason": "table must be an object"},
        )
    return {"status": "completed", "table": dict(table)}


def validate_mapping_payload(
    value: Any,
    *,
    error_kind: str,
    error_message: str,
    serialization_error_message: str,
    type_key: str,
    reason: str,
    serialization_reason: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return preprocessing_failure(
            error_kind,
            error_message,
            {type_key: type(value).__name__, "reason": reason},
        )
    normalized = dict(value)
    try:
        stable_json_bytes(normalized)
    except (TypeError, ValueError) as exc:
        return preprocessing_failure(
            error_kind,
            serialization_error_message,
            {
                type_key: type(value).__name__,
                "exception_type": type(exc).__name__,
                "reason": serialization_reason,
            },
        )
    return {"status": "completed", "value": normalized}


def read_source_text_artifact(source_artifact: Artifact) -> dict[str, Any]:
    source_path = local_storage_path(source_artifact.storage_uri or source_artifact.uri)
    if source_path is None:
        return preprocessing_failure(
            "missing_source",
            "Source file is not available in local artifact storage.",
            {"source_status": "non_file_uri"},
        )
    try:
        text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return preprocessing_failure(
            "unreadable_source_text",
            "Source text could not be decoded as UTF-8.",
            {"exception_type": type(exc).__name__},
        )
    except OSError as exc:
        return preprocessing_failure(
            "missing_source",
            "Source file is not available in local artifact storage.",
            {"source_status": "inaccessible_source", "exception_type": type(exc).__name__},
        )
    if not text.strip():
        return preprocessing_failure(
            "missing_source_text",
            "Source text is empty.",
            {"reason": "source text is empty"},
        )
    return {"status": "completed", "text": text}


def normalize_retrieval_chunks(
    source_text: str, chunks: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    raw_chunks = list(chunks) if chunks is not None else default_retrieval_chunks(source_text)
    if not raw_chunks:
        return preprocessing_failure(
            "missing_retrieval_chunks",
            "Retrieval chunk preprocessing requires at least one chunk.",
            {"reason": "no chunks were provided or derived"},
        )
    normalized: list[dict[str, Any]] = []
    for index, chunk in enumerate(raw_chunks):
        if not isinstance(chunk, Mapping):
            return preprocessing_failure(
                "invalid_retrieval_chunk",
                "Retrieval chunk must be a JSON object.",
                {
                    "chunk_index": index,
                    "chunk_type": type(chunk).__name__,
                    "reason": "chunk must be an object",
                },
            )
        chunk_text = chunk.get("chunk_text", chunk.get("text"))
        start_offset = chunk.get("start_offset")
        end_offset = chunk.get("end_offset")
        validated = validate_text_span(
            source_text,
            text=chunk_text,
            start_offset=start_offset,
            end_offset=end_offset,
            index_key="chunk_index",
            index=index,
            error_kind="invalid_retrieval_chunk",
            label="Retrieval chunk",
        )
        if validated["status"] == "failed":
            return validated
        normalized.append(
            {
                "chunk_index": index,
                "chunk_text": validated["text"],
                "start_offset": validated["start_offset"],
                "end_offset": validated["end_offset"],
                "char_count": len(validated["text"]),
                "text_checksum_sha256": hashlib.sha256(
                    validated["text"].encode("utf-8")
                ).hexdigest(),
            }
        )
    return {"status": "completed", "chunks": normalized}


def normalize_paper_card_sections(
    source_text: str, sections: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    raw_sections = (
        list(sections) if sections is not None else default_paper_card_sections(source_text)
    )
    if not raw_sections:
        return preprocessing_failure(
            "missing_paper_card_sections",
            "Paper card preprocessing requires at least one section.",
            {"reason": "no sections were provided or derived"},
        )
    normalized: list[dict[str, Any]] = []
    for index, section in enumerate(raw_sections):
        if not isinstance(section, Mapping):
            return preprocessing_failure(
                "invalid_paper_card_section",
                "Paper card section must be a JSON object.",
                {
                    "section_index": index,
                    "section_type": type(section).__name__,
                    "reason": "section must be an object",
                },
            )
        title = section.get("title")
        if not isinstance(title, str) or not title.strip():
            return preprocessing_failure(
                "invalid_paper_card_section",
                "Paper card section title is required.",
                {"section_index": index, "reason": "section title is required"},
            )
        validated = validate_text_span(
            source_text,
            text=section.get("text"),
            start_offset=section.get("start_offset"),
            end_offset=section.get("end_offset"),
            index_key="section_index",
            index=index,
            error_kind="invalid_paper_card_section",
            label="Paper card section",
            allow_missing_text=True,
        )
        if validated["status"] == "failed":
            return validated
        normalized.append(
            {
                "title": title.strip(),
                "start_offset": validated["start_offset"],
                "end_offset": validated["end_offset"],
                "char_count": len(validated["text"]),
                "text_checksum_sha256": hashlib.sha256(
                    validated["text"].encode("utf-8")
                ).hexdigest(),
                "text": validated["text"],
            }
        )
    return {"status": "completed", "sections": normalized}


def validate_text_span(
    source_text: str,
    *,
    text: Any,
    start_offset: Any,
    end_offset: Any,
    index_key: str,
    index: int,
    error_kind: str,
    label: str,
    allow_missing_text: bool = False,
) -> dict[str, Any]:
    if not isinstance(start_offset, int) or isinstance(start_offset, bool):
        return preprocessing_failure(
            error_kind,
            f"{label} start offset must be an integer.",
            {index_key: index, "field": "start_offset", "reason": "offsets must be integers"},
        )
    if not isinstance(end_offset, int) or isinstance(end_offset, bool):
        return preprocessing_failure(
            error_kind,
            f"{label} end offset must be an integer.",
            {index_key: index, "field": "end_offset", "reason": "offsets must be integers"},
        )
    if start_offset < 0 or end_offset <= start_offset or end_offset > len(source_text):
        return preprocessing_failure(
            error_kind,
            f"{label} offsets must describe a non-empty source text span.",
            {
                index_key: index,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "source_length": len(source_text),
                "reason": "offsets must describe a non-empty source text span",
            },
        )
    source_slice = source_text[start_offset:end_offset]
    if text is None and allow_missing_text:
        text = source_slice
    if not isinstance(text, str) or not text:
        return preprocessing_failure(
            error_kind,
            f"{label} text is required.",
            {index_key: index, "reason": "text is required"},
        )
    if text != source_slice:
        return preprocessing_failure(
            error_kind,
            f"{label} text must match the source offsets.",
            {
                index_key: index,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "reason": "text must match source offsets",
            },
        )
    return {
        "status": "completed",
        "text": text,
        "start_offset": start_offset,
        "end_offset": end_offset,
    }


def deterministic_paper_card_summary(sections: Sequence[Mapping[str, Any]]) -> str:
    section_text = " ".join(str(section["text"]).strip() for section in sections)
    return " ".join(section_text.split())


def default_retrieval_chunks(source_text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    cursor = 0
    for line in source_text.splitlines(keepends=True):
        line_start = cursor
        cursor += len(line)
        chunk_text = line.strip()
        if not chunk_text:
            continue
        start_offset = line_start + line.index(chunk_text)
        end_offset = start_offset + len(chunk_text)
        chunks.append(
            {
                "chunk_text": source_text[start_offset:end_offset],
                "start_offset": start_offset,
                "end_offset": end_offset,
            }
        )
    if chunks:
        return chunks

    stripped = source_text.strip()
    start_offset = source_text.index(stripped)
    return [
        {
            "chunk_text": stripped,
            "start_offset": start_offset,
            "end_offset": start_offset + len(stripped),
        }
    ]


def default_paper_card_sections(source_text: str) -> list[dict[str, Any]]:
    stripped = source_text.strip()
    start_offset = source_text.index(stripped)
    return [
        {
            "title": "Summary",
            "start_offset": start_offset,
            "end_offset": start_offset + len(stripped),
        }
    ]


def source_artifact_image_size(source_artifact: Artifact) -> tuple[int, int] | None:
    if source_artifact.image_width is None or source_artifact.image_height is None:
        return None
    return source_artifact.image_width, source_artifact.image_height


def stable_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def structured_table_metadata(
    table_data: dict[str, Any], *, checksum_sha256: str
) -> dict[str, Any]:
    columns = table_data.get("columns")
    rows = table_data.get("rows")
    column_count = len(columns) if isinstance(columns, list) else None
    row_count = len(rows) if isinstance(rows, list) else None
    metadata: dict[str, Any] = {
        "checksum_sha256": checksum_sha256,
        "format": "json",
    }
    if column_count is not None:
        metadata["column_count"] = column_count
        metadata["columns"] = columns
    if row_count is not None:
        metadata["row_count"] = row_count
    return metadata


def capture_ocr_text(
    image_path: Path | None, *, ocr_command: Sequence[str] | str | None = None
) -> dict[str, Any]:
    try:
        command = resolve_ocr_command(ocr_command)
    except ValueError:
        return ocr_unavailable("invalid_command")
    if command is None:
        return ocr_unavailable("not_configured")
    if image_path is None:
        return ocr_unavailable("missing_image")

    command_name = Path(command[0]).name
    try:
        completed = subprocess.run(
            [*command, str(image_path)],
            capture_output=True,
            check=False,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return ocr_unavailable("command_not_found", command_name=command_name)
    except OSError:
        return ocr_unavailable("command_launch_failed", command_name=command_name)
    except subprocess.TimeoutExpired:
        return ocr_unavailable("timeout", command_name=command_name)
    if completed.returncode != 0:
        return ocr_unavailable(
            "command_failed",
            command_name=command_name,
            returncode=completed.returncode,
        )

    stdout = completed.stdout
    if isinstance(stdout, bytes):
        text = stdout.decode("utf-8", errors="replace").strip()
    else:
        text = str(stdout or "").strip()
    return {
        "status": "captured",
        "backend": "command",
        "command": command_name,
        "text": text,
        "char_count": len(text),
        "checksum_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def resolve_ocr_command(ocr_command: Sequence[str] | str | None) -> list[str] | None:
    configured = os.getenv(OCR_COMMAND_ENV) if ocr_command is None else ocr_command
    if configured is None:
        return None
    command = shlex.split(configured) if isinstance(configured, str) else list(configured)
    return command or None


def ocr_unavailable(
    reason: str, *, command_name: str | None = None, returncode: int | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "ocr_unavailable",
        "reason": reason,
    }
    if command_name is not None:
        result["command"] = command_name
    if returncode is not None:
        result["returncode"] = returncode
    return result


def ocr_result_metadata(result: dict[str, Any]) -> dict[str, Any]:
    metadata = {key: value for key, value in result.items() if key != "text"}
    return dict(metadata)


def pdf_text_extraction_failure(
    error_kind: str, error_message: str, error_metadata: dict[str, Any]
) -> dict[str, Any]:
    return preprocessing_failure(error_kind, error_message, error_metadata)


def preprocessing_failure(
    error_kind: str, error_message: str, error_metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "status": "failed",
        "error_kind": error_kind,
        "error_message": error_message,
        "error_metadata": error_metadata,
    }


def derived_text_filename(source_artifact: Artifact) -> str:
    filename = source_artifact.filename or source_artifact.slug
    return f"{safe_storage_filename(Path(filename).stem)}-pdf-text.txt"


def derived_pdf_page_filename(source_artifact: Artifact, page_number: int) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    return f"{stem}-page-{page_number:04d}.png"


def derived_normalized_image_filename(source_artifact: Artifact) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    return f"{stem}-normalized.png"


def derived_ocr_text_filename(source_artifact: Artifact, page_number: int | None = None) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    if page_number is None:
        return f"{stem}-ocr.txt"
    return f"{stem}-page-{page_number:04d}-ocr.txt"


def derived_figure_filename(source_artifact: Artifact, page_number: int) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    return f"{stem}-page-{page_number:04d}-figure.png"


def derived_table_filename(source_artifact: Artifact, page_number: int) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    return f"{stem}-page-{page_number:04d}-table.json"


def derived_retrieval_chunk_filename(source_artifact: Artifact, chunk_index: int) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    return f"{stem}-chunk-{chunk_index:04d}.json"


def derived_paper_card_filename(source_artifact: Artifact) -> str:
    filename = source_artifact.filename or source_artifact.slug
    stem = safe_storage_filename(Path(filename).stem)
    return f"{stem}-paper-card.json"


def derived_pdf_text_slug(source_artifact: Artifact, preprocessing_run_id: int) -> str:
    suffix = f"{PDF_TEXT_SLUG_SUFFIX}_{preprocessing_run_id}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_pdf_text_name(source_artifact: Artifact) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        PDF_TEXT_NAME_SUFFIX,
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_pdf_page_slug(
    source_artifact: Artifact, preprocessing_run_id: int, page_number: int
) -> str:
    suffix = f"{PDF_PAGE_SLUG_SUFFIX}_{preprocessing_run_id}_{page_number}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_pdf_page_name(source_artifact: Artifact, page_number: int) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        f" page {page_number} screenshot",
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_normalized_image_slug(source_artifact: Artifact, preprocessing_run_id: int) -> str:
    suffix = f"{IMAGE_NORMALIZED_SLUG_SUFFIX}_{preprocessing_run_id}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_normalized_image_name(source_artifact: Artifact) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        IMAGE_NORMALIZED_NAME_SUFFIX,
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_ocr_text_slug(
    source_artifact: Artifact, preprocessing_run_id: int, page_number: int | None = None
) -> str:
    page_suffix = "" if page_number is None else f"_{page_number}"
    suffix = f"{OCR_TEXT_SLUG_SUFFIX}_{preprocessing_run_id}{page_suffix}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_ocr_text_name(source_artifact: Artifact, page_number: int | None = None) -> str:
    page_suffix = "" if page_number is None else f" page {page_number}"
    return append_bounded_suffix(
        source_artifact.name,
        f"{page_suffix}{OCR_TEXT_NAME_SUFFIX}",
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_figure_slug(source_artifact: Artifact, preprocessing_run_id: int) -> str:
    suffix = f"{FIGURE_SLUG_SUFFIX}_{preprocessing_run_id}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_figure_name(source_artifact: Artifact) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        FIGURE_NAME_SUFFIX,
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_table_slug(source_artifact: Artifact, preprocessing_run_id: int) -> str:
    suffix = f"{TABLE_SLUG_SUFFIX}_{preprocessing_run_id}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_table_name(source_artifact: Artifact) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        TABLE_NAME_SUFFIX,
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_retrieval_chunk_slug(
    source_artifact: Artifact, preprocessing_run_id: int, chunk_index: int
) -> str:
    suffix = f"{RETRIEVAL_CHUNK_SLUG_SUFFIX}_{preprocessing_run_id}_{chunk_index}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_retrieval_chunk_name(source_artifact: Artifact, chunk_index: int) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        f"{RETRIEVAL_CHUNK_NAME_SUFFIX} {chunk_index + 1}",
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def derived_paper_card_slug(source_artifact: Artifact, preprocessing_run_id: int) -> str:
    suffix = f"{PAPER_CARD_SLUG_SUFFIX}_{preprocessing_run_id}"
    return append_bounded_suffix(
        source_artifact.slug,
        suffix,
        max_length=ARTIFACT_SLUG_MAX_LENGTH,
    )


def derived_paper_card_name(source_artifact: Artifact) -> str:
    return append_bounded_suffix(
        source_artifact.name,
        PAPER_CARD_NAME_SUFFIX,
        max_length=ARTIFACT_NAME_MAX_LENGTH,
    )


def append_bounded_suffix(base: str, suffix: str, *, max_length: int) -> str:
    if len(base) + len(suffix) <= max_length:
        return f"{base}{suffix}"
    if len(suffix) >= max_length:
        return suffix[-max_length:]
    return f"{base[: max_length - len(suffix)]}{suffix}"


def file_metadata(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Artifact file does not exist: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    checksum, size_bytes = hash_file(path)
    metadata: dict[str, Any] = {
        "filename": path.name,
        "checksum_sha256": checksum,
        "size_bytes": size_bytes,
        "mime_type": mime_type,
    }
    dimensions = image_dimensions_from_file(path, mime_type)
    if dimensions is not None:
        metadata["width"] = dimensions[0]
        metadata["height"] = dimensions[1]
    return metadata


def local_storage_path(uri: str | None) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    if parsed.netloc and not is_local_file_authority(parsed.netloc):
        return None
    return Path(url2pathname(parsed.path))


def local_artifact_source_failure_status(
    source_path: Path | None, *, storage_root: str | Path | None = None
) -> str | None:
    if source_path is None:
        return "non_file_uri"
    try:
        storage_root_path = get_artifact_storage_root(storage_root)
        resolved_source = source_path.expanduser().resolve(strict=False)
        try:
            resolved_source.relative_to(storage_root_path)
        except ValueError:
            return "outside_artifact_storage"
        if not source_path.is_file():
            return "missing_source"
        if not os.access(source_path, os.R_OK):
            return "unreadable_source"
    except OSError:
        return "inaccessible_source"
    return None


def is_local_file_authority(authority: str) -> bool:
    normalized_authority = authority.lower()
    local_authorities = {"localhost", "127.0.0.1", "[::1]", "::1"}
    for hostname in {socket.gethostname(), socket.getfqdn()}:
        if hostname:
            local_authorities.add(hostname.lower())
    return normalized_authority in local_authorities


def write_bytes_to_storage(
    *,
    data: bytes,
    filename: str,
    project_slug: str,
    storage_root: str | Path | None = None,
) -> dict[str, Any]:
    root = get_artifact_storage_root(storage_root)
    checksum = hashlib.sha256(data).hexdigest()
    target_dir = safe_artifact_project_dir(root, project_slug) / checksum[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / checksum
    if not target.exists():
        target.write_bytes(data)
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    metadata: dict[str, Any] = {
        "filename": filename,
        "checksum_sha256": checksum,
        "size_bytes": len(data),
        "mime_type": mime_type,
        "storage_uri": target.as_uri(),
    }
    dimensions = image_dimensions(data, mime_type)
    if dimensions is not None:
        metadata["width"] = dimensions[0]
        metadata["height"] = dimensions[1]
    return metadata


def copy_file_to_storage(
    *,
    path: Path,
    project_slug: str,
    storage_root: str | Path | None = None,
) -> dict[str, Any]:
    metadata = file_metadata(path)
    root = get_artifact_storage_root(storage_root)
    target_dir = safe_artifact_project_dir(root, project_slug) / metadata["checksum_sha256"][:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / metadata["checksum_sha256"]
    if not target.exists():
        copy_file_buffered(path, target)
    metadata["storage_uri"] = target.as_uri()
    return metadata


def safe_storage_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    return cleaned or "artifact"


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size_bytes


def copy_file_buffered(source: Path, target: Path) -> None:
    with source.open("rb") as input_file, target.open("wb") as output_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            output_file.write(chunk)


def safe_artifact_project_dir(root: Path, project_slug: str) -> Path:
    safe_slug = safe_storage_filename(project_slug)
    if safe_slug != project_slug or safe_slug in {".", ".."}:
        raise ValueError("Project slug is not safe for artifact storage paths.")
    project_dir = (root / safe_slug).resolve()
    if project_dir != root and root not in project_dir.parents:
        raise ValueError("Artifact storage path escapes the configured root.")
    return project_dir


def mode_for_file(metadata: dict[str, Any]) -> ArtifactInputMode:
    mime_type = metadata.get("mime_type")
    if isinstance(mime_type, str) and mime_type.startswith("image/"):
        return ArtifactInputMode.IMAGE_DIRECT
    return ArtifactInputMode.DIRECT_FILE


def artifact_type_for_mime(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("text/"):
        return "text"
    return "file"


def image_dimensions_from_file(path: Path, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png":
        with path.open("rb") as file:
            return png_dimensions(file.read(24))
    if mime_type == "image/gif":
        with path.open("rb") as file:
            return gif_dimensions(file.read(10))
    if mime_type == "image/jpeg":
        return jpeg_dimensions_from_file(path)
    return None


def image_dimensions(data: bytes, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png":
        return png_dimensions(data)
    if mime_type == "image/gif":
        return gif_dimensions(data)
    if mime_type == "image/jpeg":
        return jpeg_dimensions(data)
    return None


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    return None


def gif_dimensions(data: bytes) -> tuple[int, int] | None:
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return width, height
    return None


def jpeg_dimensions_from_file(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as file:
        return jpeg_dimensions_from_reader(file)


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    return jpeg_dimensions_from_reader(memoryview(data))


def jpeg_dimensions_from_reader(reader: Any) -> tuple[int, int] | None:
    if isinstance(reader, memoryview):
        return _jpeg_dimensions_from_bytes(reader.tobytes())
    first_two = reader.read(2)
    if first_two != b"\xff\xd8":
        return None
    while True:
        marker_start = reader.read(1)
        if not marker_start:
            return None
        if marker_start != b"\xff":
            continue
        marker_byte = reader.read(1)
        if not marker_byte:
            return None
        marker = marker_byte[0]
        if marker in {0xD8, 0xD9}:
            continue
        length_bytes = reader.read(2)
        if len(length_bytes) != 2:
            return None
        segment_length = struct.unpack(">H", length_bytes)[0]
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            payload = reader.read(5)
            if len(payload) != 5:
                return None
            height, width = struct.unpack(">HH", payload[1:5])
            return width, height
        reader.seek(segment_length - 2, os.SEEK_CUR)


def _jpeg_dimensions_from_bytes(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if index + 7 > len(data):
                return None
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            return width, height
        index += segment_length
    return None
