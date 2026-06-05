from __future__ import annotations

from enum import StrEnum


class ArtifactInputMode(StrEnum):
    DIRECT_FILE = "direct_file"
    IMAGE_DIRECT = "image_direct"
    PDF_TEXT = "pdf_text"
    PDF_PAGE_SCREENSHOTS = "pdf_page_screenshots"
    OCR_TEXT = "ocr_text"
    SELECTED_FIGURES = "selected_figures"
    TABLE_EXTRACTION = "table_extraction"
    RETRIEVAL_CHUNKS = "retrieval_chunks"
    PAPER_CARDS = "paper_cards"
    NONE = "none"


MIXED_DERIVED_BUNDLE_INPUT_MODE = "mixed_derived_bundle"
ARTIFACT_INPUT_MODE_VALUES = {mode.value for mode in ArtifactInputMode}
DERIVED_ARTIFACT_INPUT_MODES = {
    ArtifactInputMode.PDF_TEXT,
    ArtifactInputMode.PDF_PAGE_SCREENSHOTS,
    ArtifactInputMode.OCR_TEXT,
    ArtifactInputMode.SELECTED_FIGURES,
    ArtifactInputMode.TABLE_EXTRACTION,
    ArtifactInputMode.RETRIEVAL_CHUNKS,
    ArtifactInputMode.PAPER_CARDS,
}
DERIVED_ARTIFACT_INPUT_MODE_VALUES = {mode.value for mode in DERIVED_ARTIFACT_INPUT_MODES}
