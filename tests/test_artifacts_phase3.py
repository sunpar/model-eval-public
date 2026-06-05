import base64
import hashlib
import io
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
import model_eval_api.artifacts as artifacts_module
from model_eval_api.artifact_types import ArtifactInputMode, MIXED_DERIVED_BUNDLE_INPUT_MODE
from model_eval_api.artifacts import (
    create_preprocessing_run_for_artifact,
    get_artifact_storage_root,
    ingest_text_artifact,
    local_storage_path,
    preprocess_image_visual_artifact,
    preprocess_paper_card_artifact,
    preprocess_pdf_text_artifact,
    preprocess_pdf_visual_artifact,
    preprocess_retrieval_chunks_artifact,
    preprocess_selected_figure_artifact,
    preprocess_table_artifact,
    register_file_artifact,
)
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Artifact, Base, Run
from model_eval_api.persistence.repositories import (
    complete_artifact_preprocessing_run,
    create_artifact,
    create_artifact_preprocessing_run,
    create_case,
    create_conversation_warmer,
    create_experiment_from_manifest,
    create_model_config,
    create_project,
    create_system_prompt,
    create_workspace,
    fail_artifact_preprocessing_run,
    list_artifact_preprocessing_runs,
    snapshot_artifact_preprocessing_run,
)
from pypdf import PdfWriter
from PIL import Image


FIXTURES_DIR = Path(__file__).parent / "fixtures"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAEUlEQVR4nGP8zwACTGASRgEAF"
    "EABBVDVLjgAAAAASUVORK5CYII="
)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        yield db


@pytest.fixture()
def client(session):
    def override_session():
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        yield TestClient(api_module.app)
    finally:
        api_module.app.dependency_overrides.clear()


def _project(session):
    workspace = create_workspace(session, slug="default", name="Default")
    return create_project(session, workspace=workspace, slug="research", name="Research")


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    assert parsed.scheme == "file"
    return Path(unquote(parsed.path))


def _register_pdf_fixture(
    session,
    *,
    project,
    source_path: Path,
    storage_root: Path,
    slug: str = "paper",
    name: str = "Paper",
) -> Artifact:
    return register_file_artifact(
        session,
        project=project,
        slug=slug,
        name=name,
        source_path=source_path,
        storage_root=storage_root,
        artifact_type="pdf",
    )


def test_pdf_text_extraction_records_unexpected_page_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenPage:
        def extract_text(self):
            raise AssertionError("corrupt page tree")

    class FakeReader:
        is_encrypted = False
        pages = [BrokenPage()]

        def __init__(self, _path: str) -> None:
            pass

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)

    result = artifacts_module.extract_pdf_text_pages(tmp_path / "broken.pdf")

    assert result["status"] == "failed"
    assert result["error_kind"] == "unreadable_pdf_page"
    assert result["error_metadata"] == {"page_number": 1, "exception_type": "AssertionError"}


def test_pdf_screenshot_extraction_can_stream_rendered_pages() -> None:
    rendered_pages: list[dict[str, object]] = []

    result = artifacts_module.render_pdf_page_images(
        FIXTURES_DIR / "tiny_pdf_text.pdf",
        on_page=rendered_pages.append,
    )

    assert result["status"] == "completed"
    assert result["pages"] == []
    assert [page["page_number"] for page in rendered_pages] == [1, 2]
    assert all(page["image_bytes"] for page in rendered_pages)


def test_image_normalization_preserves_palette_transparency(tmp_path: Path) -> None:
    source_path = tmp_path / "transparent-palette.png"
    image = Image.new("P", (1, 1), 0)
    image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    image.info["transparency"] = 0
    image.save(source_path)

    result = artifacts_module.normalize_image_file(source_path)

    assert result["status"] == "completed"
    with Image.open(io.BytesIO(result["image_bytes"])) as normalized:
        assert normalized.mode == "RGBA"


def test_image_normalization_reports_decompression_bombs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def raise_decompression_bomb(_path):
        raise Image.DecompressionBombError("too large")

    monkeypatch.setattr("PIL.Image.open", raise_decompression_bomb)

    result = artifacts_module.normalize_image_file(tmp_path / "too-large.png")

    assert result["status"] == "failed"
    assert result["error_kind"] == "unreadable_image"
    assert result["error_metadata"]["exception_type"] == "DecompressionBombError"


def test_jpeg_dimension_parsing_rejects_invalid_zero_length_segments() -> None:
    invalid_jpeg = io.BytesIO(b"\xff\xd8\xff\xe0\x00\x00\xff\xd9")

    assert artifacts_module.jpeg_dimensions_from_reader(invalid_jpeg) is None


def test_jpeg_dimension_bytes_parser_does_not_loop_on_zero_length_segments() -> None:
    def timeout_handler(_signum, _frame):
        raise TimeoutError("JPEG parser did not advance past an invalid zero-length segment")

    previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, 0.25)
    try:
        assert artifacts_module.jpeg_dimensions(b"\xff\xd8\xff\xe0\x00\x00" + b"\x00" * 20) is None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def test_ocr_capture_handles_launch_errors_invalid_commands_and_non_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(TINY_PNG)

    def raise_permission_error(*_args, **_kwargs):
        raise PermissionError("not executable")

    monkeypatch.setattr(artifacts_module.subprocess, "run", raise_permission_error)
    launch_failure = artifacts_module.capture_ocr_text(image_path, ocr_command=["ocr"])

    assert launch_failure["status"] == "ocr_unavailable"
    assert launch_failure["reason"] == "command_launch_failed"

    invalid_command = artifacts_module.capture_ocr_text(image_path, ocr_command='"unterminated')

    assert invalid_command["status"] == "ocr_unavailable"
    assert invalid_command["reason"] == "invalid_command"

    class Completed:
        returncode = 0
        stdout = b"\xffdetected text\n"

    monkeypatch.setattr(artifacts_module.subprocess, "run", lambda *_args, **_kwargs: Completed())
    captured = artifacts_module.capture_ocr_text(image_path, ocr_command=["ocr"])

    assert captured["status"] == "captured"
    assert captured["text"].endswith("detected text")


def _write_png_fixture(path: Path) -> None:
    path.write_bytes(TINY_PNG)


def _tiny_jpeg() -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (2, 3), color=(224, 32, 32)).save(output, format="JPEG")
    return output.getvalue()


def test_local_storage_path_decodes_local_file_uris(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source file.txt"
    source.write_text("source", encoding="utf-8")
    parsed = urlparse(source.as_uri())
    monkeypatch.setattr(artifacts_module.socket, "gethostname", lambda: "local-box")
    monkeypatch.setattr(artifacts_module.socket, "getfqdn", lambda: "local-box.example.test")

    assert local_storage_path(source.as_uri()) == source
    assert local_storage_path(f"file://localhost{parsed.path}") == source
    assert local_storage_path(f"file://local-box{parsed.path}") == source
    assert local_storage_path(f"file://local-box.example.test{parsed.path}") == source
    assert local_storage_path(f"file://127.0.0.1{parsed.path}") == source
    assert local_storage_path(f"file://[::1]{parsed.path}") == source
    assert local_storage_path(f"file://other-host{parsed.path}") is None
    assert local_storage_path("https://example.test/source.txt") is None


def test_default_artifact_storage_root_is_outside_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_EVAL_ARTIFACT_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("ARTIFACT_STORAGE_ROOT", raising=False)

    root = get_artifact_storage_root()

    repo_root = Path(__file__).resolve().parents[1]
    assert root.is_absolute()
    assert root != repo_root
    assert repo_root not in root.parents


def test_file_artifact_registration_copies_file_and_records_metadata(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = tmp_path / "source-excerpt.txt"
    source.write_text("source excerpt for checksum", encoding="utf-8")

    artifact = register_file_artifact(
        session,
        project=project,
        slug="source_excerpt",
        name="Source excerpt",
        source_path=source,
        storage_root=tmp_path / "artifact-store",
        input_mode=ArtifactInputMode.DIRECT_FILE,
    )
    session.commit()
    session.refresh(artifact)

    expected_checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    stored_path = _file_uri_path(artifact.storage_uri)
    assert stored_path.exists()
    assert stored_path.read_bytes() == source.read_bytes()
    assert artifact.filename == "source-excerpt.txt"
    assert artifact.checksum_sha256 == expected_checksum
    assert artifact.size_bytes == source.stat().st_size
    assert artifact.mime_type == "text/plain"
    assert artifact.input_mode == ArtifactInputMode.DIRECT_FILE.value
    assert artifact.created_at is not None
    assert artifact.snapshot["checksum_sha256"] == expected_checksum
    assert artifact.snapshot["storage_uri"] == artifact.storage_uri
    assert artifact.snapshot["created_at"] is not None


def test_file_artifact_checksum_is_stable_across_registrations(session, tmp_path: Path) -> None:
    project = _project(session)
    source = tmp_path / "memo.txt"
    source.write_text("same bytes produce same checksum", encoding="utf-8")
    storage_root = tmp_path / "artifact-store"

    first = register_file_artifact(
        session,
        project=project,
        slug="memo_one",
        name="Memo one",
        source_path=source,
        storage_root=storage_root,
    )
    second = register_file_artifact(
        session,
        project=project,
        slug="memo_two",
        name="Memo two",
        source_path=source,
        storage_root=storage_root,
    )
    session.commit()

    assert first.checksum_sha256 == second.checksum_sha256
    assert _file_uri_path(first.storage_uri).read_bytes() == _file_uri_path(
        second.storage_uri
    ).read_bytes()


def test_file_artifact_registration_rejects_missing_files(session, tmp_path: Path) -> None:
    project = _project(session)

    with pytest.raises(FileNotFoundError):
        register_file_artifact(
            session,
            project=project,
            slug="missing",
            name="Missing",
            source_path=tmp_path / "missing.txt",
            storage_root=tmp_path / "artifact-store",
        )


def test_artifact_storage_rejects_path_traversal_project_slugs(session, tmp_path: Path) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="../escape", name="Unsafe")
    source = tmp_path / "memo.txt"
    source.write_text("memo", encoding="utf-8")

    with pytest.raises(ValueError, match="Project slug is not safe"):
        register_file_artifact(
            session,
            project=project,
            slug="memo",
            name="Memo",
            source_path=source,
            storage_root=tmp_path / "artifact-store",
        )


def test_image_artifact_registration_records_dimensions_and_mime_type(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = tmp_path / "figure.png"
    _write_png_fixture(source)

    artifact = register_file_artifact(
        session,
        project=project,
        slug="figure",
        name="Figure",
        source_path=source,
        storage_root=tmp_path / "artifact-store",
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
    )
    session.commit()
    session.refresh(artifact)

    assert artifact.mime_type == "image/png"
    assert artifact.image_width == 2
    assert artifact.image_height == 3
    assert artifact.snapshot["image_width"] == 2
    assert artifact.snapshot["image_height"] == 3


def test_image_artifact_creation_infers_direct_image_input_mode(session) -> None:
    project = _project(session)

    artifact = create_artifact(
        session,
        project=project,
        slug="inline_image",
        name="Inline image",
        uri="file://artifact.png",
        mime_type="image/png",
        input_mode=None,
    )

    assert artifact.input_mode == ArtifactInputMode.IMAGE_DIRECT.value
    assert artifact.snapshot["input_mode"] == ArtifactInputMode.IMAGE_DIRECT.value


def test_pdf_visual_preprocessing_creates_page_screenshots_with_ocr_unavailable(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )

    first = preprocess_pdf_visual_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    second = preprocess_pdf_visual_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    assert first.status == second.status == "completed"
    assert len(first.derived_artifact_ids) == 2
    first_artifacts = [session.get(Artifact, artifact_id) for artifact_id in first.derived_artifact_ids]
    assert all(artifact is not None for artifact in first_artifacts)
    pages = first.checksums["output"]["pages"]
    assert pages == second.checksums["output"]["pages"]
    assert first.checksums["output"]["ocr"] == {}
    for artifact in first_artifacts:
        assert artifact is not None
        assert artifact.input_mode == ArtifactInputMode.PDF_PAGE_SCREENSHOTS.value
        assert artifact.mime_type == "image/png"
        assert artifact.image_width and artifact.image_width > 0
        assert artifact.image_height and artifact.image_height > 0
        assert artifact.metadata_json["derived_artifact_id"] == artifact.id
        assert artifact.metadata_json["ocr"] == {
            "status": "ocr_unavailable",
            "reason": "not_configured",
        }
        stored_path = _file_uri_path(artifact.storage_uri)
        assert storage_root in stored_path.parents
        assert stored_path.exists()
        assert hashlib.sha256(stored_path.read_bytes()).hexdigest() == artifact.checksum_sha256

    snapshot_pages = [
        item["metadata"]
        for item in snapshot_artifact_preprocessing_run(first)["derived_artifacts"]
    ]
    assert [page["page_number"] for page in snapshot_pages] == [1, 2]
    assert [page["checksum_sha256"] for page in snapshot_pages] == list(pages.values())
    assert [page["derived_artifact_id"] for page in snapshot_pages] == first.derived_artifact_ids


def test_image_visual_preprocessing_normalizes_image_metadata(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source_path = tmp_path / "figure.png"
    _write_png_fixture(source_path)
    storage_root = tmp_path / "artifact-store"
    source = register_file_artifact(
        session,
        project=project,
        slug="figure",
        name="Figure",
        source_path=source_path,
        storage_root=storage_root,
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
    )

    record = preprocess_image_visual_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "completed"
    assert len(record.derived_artifact_ids) == 1
    derived = session.get(Artifact, record.derived_artifact_ids[0])
    assert derived is not None
    assert derived.mime_type == "image/png"
    assert derived.image_width == 2
    assert derived.image_height == 3
    assert derived.metadata_json["original"]["width"] == 2
    assert derived.metadata_json["original"]["height"] == 3
    assert derived.metadata_json["normalized"] == {
        "width": 2,
        "height": 3,
        "mime_type": "image/png",
        "checksum_sha256": derived.checksum_sha256,
    }
    assert derived.metadata_json["ocr"] == {
        "status": "ocr_unavailable",
        "reason": "not_configured",
    }
    assert record.checksums["output"]["normalized_image"] == derived.checksum_sha256


def test_image_visual_preprocessing_captures_configured_ocr_text(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source_path = tmp_path / "figure.png"
    _write_png_fixture(source_path)
    ocr_backend = tmp_path / "ocr_backend.py"
    ocr_backend.write_text("import sys\nprint('detected text')\n", encoding="utf-8")
    storage_root = tmp_path / "artifact-store"
    source = register_file_artifact(
        session,
        project=project,
        slug="figure",
        name="Figure",
        source_path=source_path,
        storage_root=storage_root,
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
    )

    record = preprocess_image_visual_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
        ocr_command=[sys.executable, str(ocr_backend)],
    )
    session.commit()

    assert record.status == "completed"
    assert len(record.derived_artifact_ids) == 2
    normalized = session.get(Artifact, record.derived_artifact_ids[0])
    ocr_text = session.get(Artifact, record.derived_artifact_ids[1])
    assert normalized is not None
    assert ocr_text is not None
    assert normalized.metadata_json["ocr"] == {
        "status": "captured",
        "backend": "command",
        "command": Path(sys.executable).name,
        "char_count": len("detected text"),
        "checksum_sha256": hashlib.sha256(b"detected text").hexdigest(),
        "derived_artifact_id": ocr_text.id,
    }
    assert ocr_text.metadata_json["ocr"] == {
        "status": "captured",
        "backend": "command",
        "command": Path(sys.executable).name,
        "char_count": len("detected text"),
        "checksum_sha256": hashlib.sha256(b"detected text").hexdigest(),
    }
    assert _file_uri_path(ocr_text.storage_uri).read_text(encoding="utf-8") == "detected text"
    assert record.checksums["output"]["ocr"] == hashlib.sha256(b"detected text").hexdigest()


def test_selected_figure_preprocessing_records_region_and_snapshot(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )
    region = {"x": 12, "y": 24, "width": 120, "height": 80}

    first = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region=region,
        image_bytes=TINY_PNG,
        storage_root=storage_root,
    )
    second = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region=region,
        image_bytes=TINY_PNG,
        storage_root=storage_root,
    )
    session.commit()

    assert first.status == second.status == "completed"
    assert len(first.derived_artifact_ids) == 1
    derived = session.get(Artifact, first.derived_artifact_ids[0])
    assert derived is not None
    assert derived.input_mode == ArtifactInputMode.SELECTED_FIGURES.value
    assert derived.mime_type == "image/png"
    assert derived.image_width == 2
    assert derived.image_height == 3
    assert derived.metadata_json["source_artifact_id"] == source.id
    assert derived.metadata_json["source_checksum_sha256"] == source.checksum_sha256
    assert derived.metadata_json["parser_name"] == "selected_figure"
    assert derived.metadata_json["parser_version"] == "1.0.0"
    assert derived.metadata_json["page_number"] == 1
    assert derived.metadata_json["region"] == region
    assert derived.metadata_json["derived_artifact_id"] == derived.id
    assert first.checksums["output"]["figures"] == second.checksums["output"]["figures"]
    assert first.checksums["output"]["figures"]["1"] == derived.checksum_sha256

    stored_path = _file_uri_path(derived.storage_uri)
    assert storage_root in stored_path.parents
    assert stored_path.exists()
    assert hashlib.sha256(stored_path.read_bytes()).hexdigest() == derived.checksum_sha256
    snapshot = snapshot_artifact_preprocessing_run(first)
    assert snapshot["derived_artifacts"][0]["metadata"]["region"] == region
    assert snapshot["derived_artifacts"][0]["metadata"]["derived_artifact_id"] == derived.id
    second_snapshot = snapshot_artifact_preprocessing_run(second)
    first_metadata = dict(snapshot["derived_artifacts"][0]["metadata"])
    second_metadata = dict(second_snapshot["derived_artifacts"][0]["metadata"])
    first_metadata.pop("derived_artifact_id")
    second_metadata.pop("derived_artifact_id")
    assert first_metadata == second_metadata


def test_selected_figure_preprocessing_normalizes_preview_bytes_to_png(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )

    record = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region={"x": 0, "y": 0, "width": 2, "height": 3},
        image_bytes=_tiny_jpeg(),
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "completed"
    derived = session.get(Artifact, record.derived_artifact_ids[0])
    assert derived is not None
    assert derived.mime_type == "image/png"
    stored_bytes = _file_uri_path(derived.storage_uri).read_bytes()
    assert stored_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(stored_bytes).hexdigest() == derived.checksum_sha256


def test_table_preprocessing_records_structured_metadata_and_snapshot(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )
    region = {"x": 4, "y": 8, "width": 220, "height": 96}
    table = {
        "columns": ["metric", "value"],
        "rows": [
            {"metric": "Revenue", "value": "10"},
            {"metric": "Cost", "value": "7"},
        ],
    }

    first = preprocess_table_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=2,
        region=region,
        table=table,
        storage_root=storage_root,
    )
    second = preprocess_table_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=2,
        region=region,
        table=table,
        storage_root=storage_root,
    )
    session.commit()

    assert first.status == second.status == "completed"
    assert len(first.derived_artifact_ids) == 1
    derived = session.get(Artifact, first.derived_artifact_ids[0])
    assert derived is not None
    assert derived.input_mode == ArtifactInputMode.TABLE_EXTRACTION.value
    assert derived.mime_type == "application/json"
    assert derived.metadata_json["source_artifact_id"] == source.id
    assert derived.metadata_json["source_checksum_sha256"] == source.checksum_sha256
    assert derived.metadata_json["parser_name"] == "table_extraction"
    assert derived.metadata_json["parser_version"] == "1.0.0"
    assert derived.metadata_json["page_number"] == 2
    assert derived.metadata_json["region"] == region
    assert derived.metadata_json["table"]["columns"] == ["metric", "value"]
    assert derived.metadata_json["table"]["row_count"] == 2
    assert derived.metadata_json["table"]["column_count"] == 2
    assert derived.metadata_json["derived_artifact_id"] == derived.id
    assert first.checksums["output"]["tables"] == second.checksums["output"]["tables"]
    assert first.checksums["output"]["tables"]["2"] == derived.checksum_sha256

    stored_payload = _file_uri_path(derived.storage_uri).read_text(encoding="utf-8")
    assert json.loads(stored_payload)["table"] == table
    snapshot = snapshot_artifact_preprocessing_run(first)
    assert snapshot["derived_artifacts"][0]["metadata"]["table"]["row_count"] == 2
    assert snapshot["derived_artifacts"][0]["metadata"]["derived_artifact_id"] == derived.id
    second_snapshot = snapshot_artifact_preprocessing_run(second)
    first_metadata = dict(snapshot["derived_artifacts"][0]["metadata"])
    second_metadata = dict(second_snapshot["derived_artifacts"][0]["metadata"])
    first_metadata.pop("derived_artifact_id")
    second_metadata.pop("derived_artifact_id")
    assert first_metadata == second_metadata


def test_figure_and_table_preprocessing_fail_for_invalid_regions(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )
    invalid_region = {"x": 1, "y": 2, "width": 0, "height": 10}

    figure = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region=invalid_region,
        image_bytes=TINY_PNG,
        storage_root=storage_root,
    )
    table = preprocess_table_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region=invalid_region,
        table={"columns": ["metric"], "rows": [{"metric": "Revenue"}]},
        storage_root=storage_root,
    )
    session.commit()

    assert figure.status == table.status == "failed"
    assert figure.error_kind == table.error_kind == "invalid_region"
    assert figure.derived_artifact_ids == []
    assert table.derived_artifact_ids == []
    assert figure.error_metadata == {
        "page_number": 1,
        "parser_name": "selected_figure",
        "source_artifact_id": source.id,
        "region": invalid_region,
        "reason": "width and height must be positive",
    }
    assert table.error_metadata == {
        "page_number": 1,
        "parser_name": "table_extraction",
        "source_artifact_id": source.id,
        "region": invalid_region,
        "reason": "width and height must be positive",
    }


def test_figure_and_table_preprocessing_fail_for_malformed_regions(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )

    figure = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region="not-a-region",
        image_bytes=TINY_PNG,
        storage_root=storage_root,
    )
    table = preprocess_table_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region={"x": True, "y": 2, "width": 10, "height": 10},
        table={"columns": ["metric"], "rows": [{"metric": "Revenue"}]},
        storage_root=storage_root,
    )
    session.commit()

    assert figure.status == table.status == "failed"
    assert figure.error_kind == table.error_kind == "invalid_region"
    assert figure.derived_artifact_ids == []
    assert table.derived_artifact_ids == []
    assert figure.error_metadata == {
        "page_number": 1,
        "parser_name": "selected_figure",
        "source_artifact_id": source.id,
        "region_type": "str",
        "reason": "region must be an object",
    }
    assert table.error_metadata == {
        "page_number": 1,
        "parser_name": "table_extraction",
        "source_artifact_id": source.id,
        "region": {"x": True, "y": 2, "width": 10, "height": 10},
        "field": "x",
        "value_type": "bool",
        "reason": "region values must be integers",
    }


def test_figure_and_table_preprocessing_fail_for_boolean_page_numbers(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )

    figure = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=True,
        region={"x": 1, "y": 2, "width": 10, "height": 10},
        image_bytes=TINY_PNG,
        storage_root=storage_root,
    )
    table = preprocess_table_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=True,
        region={"x": 1, "y": 2, "width": 10, "height": 10},
        table={"columns": ["metric"], "rows": [{"metric": "Revenue"}]},
        storage_root=storage_root,
    )
    session.commit()

    assert figure.status == table.status == "failed"
    assert figure.error_kind == table.error_kind == "invalid_page_number"
    assert figure.derived_artifact_ids == []
    assert table.derived_artifact_ids == []
    assert figure.error_metadata == {
        "page_number": True,
        "parser_name": "selected_figure",
        "source_artifact_id": source.id,
        "reason": "page number must be positive",
    }
    assert table.error_metadata == {
        "page_number": True,
        "parser_name": "table_extraction",
        "source_artifact_id": source.id,
        "reason": "page number must be positive",
    }


def test_table_preprocessing_fails_for_malformed_table_payload(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )

    record = preprocess_table_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region={"x": 1, "y": 2, "width": 10, "height": 10},
        table="not-a-table",
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "invalid_table"
    assert record.derived_artifact_ids == []
    assert record.error_metadata == {
        "page_number": 1,
        "parser_name": "table_extraction",
        "source_artifact_id": source.id,
        "table_type": "str",
        "reason": "table must be an object",
    }


def test_selected_figure_preprocessing_rejects_out_of_bounds_region_for_image_source(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source_path = tmp_path / "figure.png"
    _write_png_fixture(source_path)
    storage_root = tmp_path / "artifact-store"
    source = register_file_artifact(
        session,
        project=project,
        slug="figure",
        name="Figure",
        source_path=source_path,
        storage_root=storage_root,
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
    )

    record = preprocess_selected_figure_artifact(
        session,
        project=project,
        source_artifact=source,
        page_number=1,
        region={"x": 0, "y": 0, "width": 10, "height": 3},
        image_bytes=TINY_PNG,
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "invalid_region"
    assert record.derived_artifact_ids == []
    assert record.error_metadata == {
        "page_number": 1,
        "parser_name": "selected_figure",
        "source_artifact_id": source.id,
        "region": {"x": 0, "y": 0, "width": 10, "height": 3},
        "source_width": 2,
        "source_height": 3,
        "reason": "region must fit within the source artifact bounds",
    }


def test_retrieval_chunk_preprocessing_records_chunks_and_stable_snapshots(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="Alpha beta\nGamma delta",
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )
    chunks = [
        {"chunk_text": "Alpha beta", "start_offset": 0, "end_offset": 10},
        {"chunk_text": "Gamma delta", "start_offset": 11, "end_offset": 22},
    ]

    first = preprocess_retrieval_chunks_artifact(
        session,
        project=project,
        source_artifact=source,
        chunks=chunks,
        storage_root=tmp_path / "artifact-store",
    )
    second = preprocess_retrieval_chunks_artifact(
        session,
        project=project,
        source_artifact=source,
        chunks=chunks,
        storage_root=tmp_path / "artifact-store",
    )
    session.commit()

    assert first.status == second.status == "completed"
    assert len(first.derived_artifact_ids) == 2
    assert first.checksums["output"]["retrieval_chunks"] == second.checksums["output"][
        "retrieval_chunks"
    ]
    derived = [session.get(Artifact, artifact_id) for artifact_id in first.derived_artifact_ids]
    assert all(artifact is not None for artifact in derived)
    for index, artifact in enumerate(derived):
        assert artifact is not None
        chunk = chunks[index]
        assert artifact.input_mode == ArtifactInputMode.RETRIEVAL_CHUNKS.value
        assert artifact.mime_type == "application/json"
        assert artifact.metadata_json["source_artifact_id"] == source.id
        assert artifact.metadata_json["source_checksum_sha256"] == source.checksum_sha256
        assert artifact.metadata_json["parser_name"] == "retrieval_chunks"
        assert artifact.metadata_json["parser_version"] == "1.0.0"
        assert artifact.metadata_json["chunk_index"] == index
        assert artifact.metadata_json["chunk_text"] == chunk["chunk_text"]
        assert artifact.metadata_json["start_offset"] == chunk["start_offset"]
        assert artifact.metadata_json["end_offset"] == chunk["end_offset"]
        assert artifact.metadata_json["derived_artifact_id"] == artifact.id
        payload = json.loads(_file_uri_path(artifact.storage_uri).read_text(encoding="utf-8"))
        assert payload["chunk_text"] == chunk["chunk_text"]

    first_metadata = [
        dict(item["metadata"])
        for item in snapshot_artifact_preprocessing_run(first)["derived_artifacts"]
    ]
    second_metadata = [
        dict(item["metadata"])
        for item in snapshot_artifact_preprocessing_run(second)["derived_artifacts"]
    ]
    for item in [*first_metadata, *second_metadata]:
        item.pop("derived_artifact_id")
    assert first_metadata == second_metadata


def test_paper_card_preprocessing_records_citation_sections_and_immutable_snapshot(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    text = "Overview\nRevenue improved.\nRisks\nCosts rose."
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text=text,
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )
    sections = [
        {
            "title": "Overview",
            "start_offset": text.index("Revenue"),
            "end_offset": text.index("\nRisks"),
        },
        {
            "title": "Risks",
            "start_offset": text.index("Costs"),
            "end_offset": len(text),
        },
    ]
    citation = {"title": "Copper memo", "authors": ["Analyst"], "year": 2026}

    first = preprocess_paper_card_artifact(
        session,
        project=project,
        source_artifact=source,
        citation=citation,
        sections=sections,
        storage_root=tmp_path / "artifact-store",
    )
    second = preprocess_paper_card_artifact(
        session,
        project=project,
        source_artifact=source,
        citation=citation,
        sections=sections,
        storage_root=tmp_path / "artifact-store",
    )
    session.commit()

    assert first.status == second.status == "completed"
    assert len(first.derived_artifact_ids) == 1
    assert first.checksums["output"] == second.checksums["output"]
    derived = session.get(Artifact, first.derived_artifact_ids[0])
    assert derived is not None
    assert derived.input_mode == ArtifactInputMode.PAPER_CARDS.value
    assert derived.mime_type == "application/json"
    assert derived.metadata_json["source_artifact_id"] == source.id
    assert derived.metadata_json["source_checksum_sha256"] == source.checksum_sha256
    assert derived.metadata_json["parser_name"] == "paper_card"
    assert derived.metadata_json["parser_version"] == "1.0.0"
    assert derived.metadata_json["citation"] == citation
    assert derived.metadata_json["sections"][0]["title"] == "Overview"
    assert derived.metadata_json["sections"][0]["start_offset"] == sections[0]["start_offset"]
    assert derived.metadata_json["sections"][0]["end_offset"] == sections[0]["end_offset"]
    assert derived.metadata_json["derived_artifact_id"] == derived.id
    payload = json.loads(_file_uri_path(derived.storage_uri).read_text(encoding="utf-8"))
    assert payload["citation"] == citation
    assert payload["summary"] == "Revenue improved. Costs rose."

    original_snapshots = list(first.derived_artifact_snapshots)
    derived.metadata_json = {"mutated": True}
    session.flush()
    assert snapshot_artifact_preprocessing_run(first)["derived_artifacts"] == original_snapshots


def test_retrieval_and_paper_card_preprocessing_fail_on_missing_source_text(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="",
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )

    retrieval = preprocess_retrieval_chunks_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=tmp_path / "artifact-store",
    )
    paper_card = preprocess_paper_card_artifact(
        session,
        project=project,
        source_artifact=source,
        citation={"title": "Empty"},
        storage_root=tmp_path / "artifact-store",
    )
    session.commit()

    assert retrieval.status == paper_card.status == "failed"
    assert retrieval.error_kind == paper_card.error_kind == "missing_source_text"
    assert retrieval.derived_artifact_ids == []
    assert paper_card.derived_artifact_ids == []
    assert retrieval.error_metadata == {
        "filename": "paper.txt",
        "parser_name": "retrieval_chunks",
        "source_artifact_id": source.id,
        "reason": "source text is empty",
    }
    assert paper_card.error_metadata == {
        "filename": "paper.txt",
        "parser_name": "paper_card",
        "source_artifact_id": source.id,
        "reason": "source text is empty",
    }


def test_paper_card_preprocessing_fails_for_non_json_citation(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="Alpha beta",
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )

    record = preprocess_paper_card_artifact(
        session,
        project=project,
        source_artifact=source,
        citation={"title": {"not", "json"}},
        storage_root=tmp_path / "artifact-store",
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "invalid_citation"
    assert record.error_message == (
        "Paper card citation must contain only JSON-serializable values."
    )
    assert record.derived_artifact_ids == []
    assert record.error_metadata == {
        "filename": "paper.txt",
        "parser_name": "paper_card",
        "source_artifact_id": source.id,
        "citation_type": "dict",
        "exception_type": "TypeError",
        "reason": "citation must be JSON-serializable",
    }


def test_text_artifact_ingestion_writes_local_text_file_and_records_metadata(
    session, tmp_path: Path
) -> None:
    project = _project(session)

    artifact = ingest_text_artifact(
        session,
        project=project,
        slug="paper_text",
        name="Paper text",
        text="copied paper text",
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )
    session.commit()
    session.refresh(artifact)

    stored_path = _file_uri_path(artifact.storage_uri)
    assert stored_path.read_text(encoding="utf-8") == "copied paper text"
    assert artifact.artifact_type == "text"
    assert artifact.filename == "paper.txt"
    assert artifact.mime_type == "text/plain"
    assert artifact.input_mode == ArtifactInputMode.PDF_TEXT.value
    assert artifact.size_bytes == len("copied paper text".encode("utf-8"))
    assert artifact.metadata_json["encoding"] == "utf-8"


def test_pdf_text_preprocessing_creates_stable_page_metadata(session, tmp_path: Path) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )

    first = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    second = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    first_derived = session.get(Artifact, first.derived_artifact_ids[0])
    second_derived = session.get(Artifact, second.derived_artifact_ids[0])
    assert first_derived is not None
    assert second_derived is not None
    assert first.status == second.status == "completed"
    assert first_derived.input_mode == ArtifactInputMode.PDF_TEXT.value
    assert first_derived.metadata_json["page_count"] == 2
    assert first_derived.metadata_json["pages"] == [
        {
            "page_number": 1,
            "char_count": len("Alpha beta"),
            "checksum_sha256": hashlib.sha256(b"Alpha beta").hexdigest(),
        },
        {
            "page_number": 2,
            "char_count": len("Gamma delta"),
            "checksum_sha256": hashlib.sha256(b"Gamma delta").hexdigest(),
        },
    ]
    assert first_derived.metadata_json["pages"] == second_derived.metadata_json["pages"]
    assert first.checksums["output"]["pages"] == second.checksums["output"]["pages"]
    assert first_derived.checksum_sha256 == second_derived.checksum_sha256
    assert first_derived.metadata_json["source_checksum_sha256"] == source.checksum_sha256
    assert first_derived.metadata_json["parser_name"] == "pdf_text"
    assert first_derived.metadata_json["parser_version"] == "1.0.0"

    derived_path = _file_uri_path(first_derived.storage_uri)
    assert storage_root in derived_path.parents
    assert derived_path.read_text(encoding="utf-8") == "Alpha beta\n\nGamma delta"
    assert snapshot_artifact_preprocessing_run(first)["derived_artifacts"][0]["metadata"][
        "pages"
    ] == first_derived.metadata_json["pages"]


def test_pdf_text_preprocessing_fails_on_empty_page(session, tmp_path: Path) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_empty_page.pdf",
        storage_root=storage_root,
    )

    record = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "empty_pdf_page"
    assert record.error_metadata == {
        "filename": "tiny_pdf_empty_page.pdf",
        "parser_name": "pdf_text",
        "source_artifact_id": source.id,
        "page_number": 1,
        "page_count": 1,
    }
    assert record.derived_artifact_ids == []


def test_pdf_text_preprocessing_bounds_default_derived_identity(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
        slug="s" * 160,
        name="N" * 255,
    )

    record = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    derived = session.get(Artifact, record.derived_artifact_ids[0])
    assert derived is not None
    assert record.status == "completed"
    assert len(derived.slug) <= 160
    assert derived.slug.endswith(f"_pdf_text_{record.id}")
    assert len(derived.name) <= 255
    assert derived.name.endswith(" extracted text")


def test_pdf_text_preprocessing_fails_on_unreadable_pdf(session, tmp_path: Path) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    invalid_pdf = tmp_path / "invalid.pdf"
    invalid_pdf.write_text("not a pdf", encoding="utf-8")
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=invalid_pdf,
        storage_root=storage_root,
    )

    record = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "unreadable_pdf"
    assert record.error_metadata["filename"] == "invalid.pdf"
    assert record.error_metadata["parser_name"] == "pdf_text"
    assert record.error_metadata["source_artifact_id"] == source.id
    assert "source_path" not in record.error_metadata
    assert "storage_uri" not in record.error_metadata


def test_pdf_text_preprocessing_fails_on_encrypted_pdf(session, tmp_path: Path) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    encrypted_pdf = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret")
    with encrypted_pdf.open("wb") as file:
        writer.write(file)
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=encrypted_pdf,
        storage_root=storage_root,
    )

    record = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "encrypted_pdf"
    assert record.error_metadata == {
        "filename": "encrypted.pdf",
        "parser_name": "pdf_text",
        "source_artifact_id": source.id,
    }
    assert record.derived_artifact_ids == []


def test_artifact_preprocessing_run_records_immutable_derived_snapshots(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="source text",
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )
    derived = ingest_text_artifact(
        session,
        project=project,
        slug="paper_pages",
        name="Paper pages",
        text="derived text",
        filename="paper-pages.txt",
        storage_root=tmp_path / "artifact-store",
        metadata={"page_count": 1},
    )
    derived_v2 = ingest_text_artifact(
        session,
        project=project,
        slug="paper_pages",
        name="Paper pages v2",
        text="derived text v2",
        filename="paper-pages-v2.txt",
        storage_root=tmp_path / "artifact-store",
        metadata={"page_count": 2},
        version=2,
    )
    record = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
        local_storage_uri=source.storage_uri,
    )
    complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=[derived, derived_v2],
        local_storage_uri=derived.storage_uri,
        output_checksums={"text": derived.checksum_sha256},
    )
    session.commit()

    assert record.status == "completed"
    assert record.source_artifact_id == source.id
    assert record.parser_name == "pdf_text"
    assert record.parser_version == "1.0.0"
    assert record.source_checksum_sha256 == source.checksum_sha256
    assert record.derived_artifact_ids == [derived.id, derived_v2.id]
    assert record.derived_artifact_snapshots[0]["checksum_sha256"] == derived.checksum_sha256
    assert record.checksums["derived"] == {
        "paper_pages@v1": derived.checksum_sha256,
        "paper_pages@v2": derived_v2.checksum_sha256,
    }
    assert record.checksums["output"]["text"] == derived.checksum_sha256
    assert record.extracted_at is not None

    original_snapshots = [dict(snapshot) for snapshot in record.derived_artifact_snapshots]
    derived.checksum_sha256 = "f" * 64
    derived.metadata_json["page_count"] = 99
    session.commit()
    session.refresh(record)

    assert record.derived_artifact_snapshots == original_snapshots
    assert snapshot_artifact_preprocessing_run(record)["derived_artifacts"] == original_snapshots


def test_artifact_preprocessing_completion_replaces_stale_failure_timestamp(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="source text",
        filename="paper.txt",
        storage_root=tmp_path / "artifact-store",
    )
    derived = ingest_text_artifact(
        session,
        project=project,
        slug="paper_pages",
        name="Paper pages",
        text="derived text",
        filename="paper-pages.txt",
        storage_root=tmp_path / "artifact-store",
    )
    record = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
        local_storage_uri=source.storage_uri,
    )
    stale_completed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fail_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        error_kind="parser_error",
        error_message="Parser failed.",
        completed_at=stale_completed_at,
    )

    complete_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        derived_artifacts=[derived],
        local_storage_uri=derived.storage_uri,
    )

    assert record.status == "completed"
    assert record.completed_at is not None
    assert record.completed_at != stale_completed_at
    assert record.completed_at > stale_completed_at
    assert record.error_kind is None


def test_artifact_preprocessing_failure_sanitizes_error_metadata(
    session,
) -> None:
    project = _project(session)
    source = create_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        artifact_type="pdf",
        filename="paper.pdf",
        checksum_sha256="a" * 64,
        storage_uri="file:///private/tmp/secret/paper.pdf",
    )
    record = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )
    fail_artifact_preprocessing_run(
        session,
        preprocessing_run=record,
        error_kind="missing_source",
        error_message="Source file was not found.",
        error_metadata={
            "source_path": "/private/tmp/secret/paper.pdf",
            "source_url": "file:///private/tmp/secret/paper.pdf",
            "storage_uri": "file:///private/tmp/secret/paper.pdf",
            "download_url": "file:///private/tmp/secret/paper.pdf",
            "output_path": "/private/tmp/secret/output.txt",
            "api_key": "should-not-survive",
            "context": {
                "api_key": "nested-secret",
                "duration_ms": 7,
                "source_path": "/private/tmp/secret/context.txt",
            },
            "duration_ms": 12,
            "filename": "paper.pdf",
            "security_context": "sandboxed",
        },
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_metadata["api_key"] == "[redacted]"
    assert "source_path" not in record.error_metadata
    assert "source_url" not in record.error_metadata
    assert "storage_uri" not in record.error_metadata
    assert "download_url" not in record.error_metadata
    assert "output_path" not in record.error_metadata
    assert record.error_metadata["duration_ms"] == 12
    assert record.error_metadata["context"] == {
        "api_key": "[redacted]",
        "duration_ms": 7,
    }
    assert record.error_metadata["filename"] == "paper.pdf"
    assert record.error_metadata["security_context"] == "sandboxed"
    assert record.completed_at is not None


def test_artifact_preprocessing_model_hook_sanitizes_direct_error_metadata(session) -> None:
    project = _project(session)
    source = create_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        artifact_type="pdf",
        filename="paper.pdf",
        checksum_sha256="a" * 64,
        storage_uri="file:///private/tmp/secret/paper.pdf",
    )
    record = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )
    record.error_metadata = {
        "source_path": "/private/tmp/secret/paper.pdf",
        "storage_uri": "file:///private/tmp/secret/paper.pdf",
        "context": {
            "api_key": "nested-secret",
            "duration_ms": 5,
            "source_uri": "file:///private/tmp/secret/context.txt",
        },
    }
    session.commit()
    session.refresh(record)

    assert record.error_metadata == {
        "context": {
            "api_key": "[redacted]",
            "duration_ms": 5,
        }
    }


def test_missing_local_source_file_creates_failed_preprocessing_record(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    missing = storage_root / "missing.pdf"
    source = create_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        artifact_type="pdf",
        filename="paper.pdf",
        checksum_sha256="b" * 64,
        storage_uri=missing.as_uri(),
    )

    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_kind == "missing_source"
    assert record.error_metadata == {
        "filename": "paper.pdf",
        "parser_name": "pdf_text",
        "source_artifact_id": source.id,
        "source_status": "missing_source",
    }


def test_preprocessing_rejects_source_files_outside_artifact_storage(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    outside_source = tmp_path / "outside.pdf"
    outside_source.write_text("outside", encoding="utf-8")
    source = create_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        artifact_type="pdf",
        filename="paper.pdf",
        checksum_sha256="b" * 64,
        storage_uri=outside_source.as_uri(),
    )

    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_metadata == {
        "filename": "paper.pdf",
        "parser_name": "pdf_text",
        "source_artifact_id": source.id,
        "source_status": "outside_artifact_storage",
    }


def test_inaccessible_local_source_file_creates_failed_preprocessing_record(
    monkeypatch: pytest.MonkeyPatch, session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source_path = storage_root / "source.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("source", encoding="utf-8")
    source = create_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        artifact_type="pdf",
        filename="paper.pdf",
        checksum_sha256="b" * 64,
        storage_uri=source_path.as_uri(),
    )
    original_is_file = Path.is_file

    def raise_permission_error(path: Path) -> bool:
        if path == source_path:
            raise PermissionError("blocked")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", raise_permission_error)

    record = create_preprocessing_run_for_artifact(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
        storage_root=storage_root,
    )
    session.commit()

    assert record.status == "failed"
    assert record.error_metadata == {
        "filename": "paper.pdf",
        "parser_name": "pdf_text",
        "source_artifact_id": source.id,
        "source_status": "inaccessible_source",
    }


def test_list_artifact_preprocessing_runs_filters_by_source_and_status(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="source text",
        storage_root=tmp_path / "artifact-store",
    )
    other_source = ingest_text_artifact(
        session,
        project=project,
        slug="other",
        name="Other",
        text="other text",
        storage_root=tmp_path / "artifact-store",
    )
    completed = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )
    complete_artifact_preprocessing_run(
        session,
        preprocessing_run=completed,
        derived_artifacts=[],
        output_checksums={},
    )
    create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=other_source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )
    session.commit()

    assert list_artifact_preprocessing_runs(session, project=project, status="completed") == [
        completed
    ]
    assert list_artifact_preprocessing_runs(
        session, project=project, source_artifact=source
    ) == [completed]


def test_artifact_preprocessing_source_checksums_are_stable_across_runs(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="repeatable source text",
        storage_root=tmp_path / "artifact-store",
    )

    first = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )
    second = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )
    session.commit()

    assert first.source_checksum_sha256 == second.source_checksum_sha256
    assert first.checksums["source"] == second.checksums["source"] == source.checksum_sha256


def test_artifact_preprocessing_rejects_cross_project_artifacts(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    other_project = create_project(
        session,
        workspace=project.workspace,
        slug="other",
        name="Other",
    )
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        text="source text",
        storage_root=tmp_path / "artifact-store",
    )
    derived = ingest_text_artifact(
        session,
        project=other_project,
        slug="derived",
        name="Derived",
        text="derived text",
        storage_root=tmp_path / "artifact-store",
    )
    record = create_artifact_preprocessing_run(
        session,
        project=project,
        source_artifact=source,
        parser_name="pdf_text",
        parser_version="1.0.0",
    )

    with pytest.raises(ValueError, match="Derived artifacts must belong"):
        complete_artifact_preprocessing_run(
            session,
            preprocessing_run=record,
            derived_artifacts=[derived],
        )


def test_run_model_input_snapshot_records_final_messages_and_artifact_mode(
    session,
) -> None:
    project = _project(session)
    create_artifact(
        session,
        project=project,
        slug="source_text",
        name="Source text",
        artifact_type="text",
        filename="source.txt",
        checksum_sha256="abc123",
        size_bytes=12,
        mime_type="text/plain",
        storage_uri="file:///tmp/source.txt",
        input_mode=ArtifactInputMode.DIRECT_FILE,
    )
    manifest = parse_manifest(
        {
            "name": "input_snapshot",
            "cases": [{"id": "case_a", "prompt": "Final task"}],
            "artifacts": ["source_text"],
            "models": [
                {
                    "id": "model_a",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {},
                }
            ],
            "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
            "warmers": [
                {
                    "id": "warmer_a",
                    "messages": [{"role": "user", "content": "Prior turn"}],
                }
            ],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    assert run is not None
    assert run.model_input_snapshot["artifact_input_mode"] == ArtifactInputMode.DIRECT_FILE.value
    assert run.model_input_snapshot["final_messages"] == [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Prior turn"},
        {"role": "user", "content": "Final task"},
    ]
    assert run.model_input_snapshot["artifact_inputs"] == [
        {
            "id": "source_text",
            "input_mode": ArtifactInputMode.DIRECT_FILE.value,
            "storage_uri": "file:///tmp/source.txt",
            "mime_type": "text/plain",
            "checksum_sha256": "abc123",
        }
    ]
    assert run.run_snapshot["model_input_snapshot"] == run.model_input_snapshot


def test_run_model_input_snapshot_records_none_when_no_artifacts(session) -> None:
    project = _project(session)
    manifest = parse_manifest(
        {
            "name": "no_artifacts",
            "cases": [{"id": "case_a", "prompt": "Final task"}],
            "models": [
                {
                    "id": "model_a",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {},
                }
            ],
            "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
            "warmers": [{"id": "warmer_a", "prompt": "No prior conversation"}],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    assert run is not None
    assert run.model_input_snapshot["artifact_input_mode"] == ArtifactInputMode.NONE.value
    assert run.model_input_snapshot["artifact_inputs"] == []
    assert run.model_input_snapshot["final_messages"] == [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "No prior conversation"},
        {"role": "user", "content": "Final task"},
    ]


@pytest.mark.parametrize(
    "input_mode",
    [
        ArtifactInputMode.DIRECT_FILE,
        ArtifactInputMode.IMAGE_DIRECT,
        ArtifactInputMode.PDF_TEXT,
        ArtifactInputMode.PDF_PAGE_SCREENSHOTS,
        ArtifactInputMode.SELECTED_FIGURES,
        ArtifactInputMode.TABLE_EXTRACTION,
        ArtifactInputMode.OCR_TEXT,
        ArtifactInputMode.RETRIEVAL_CHUNKS,
        ArtifactInputMode.PAPER_CARDS,
    ],
)
def test_run_model_input_snapshot_records_supported_artifact_input_modes(
    session, input_mode: ArtifactInputMode
) -> None:
    project = _project(session)
    metadata = {}
    if input_mode in {
        ArtifactInputMode.PDF_TEXT,
        ArtifactInputMode.PDF_PAGE_SCREENSHOTS,
        ArtifactInputMode.SELECTED_FIGURES,
        ArtifactInputMode.TABLE_EXTRACTION,
        ArtifactInputMode.OCR_TEXT,
        ArtifactInputMode.RETRIEVAL_CHUNKS,
        ArtifactInputMode.PAPER_CARDS,
    }:
        metadata = {
            "source_artifact_id": 42,
            "source_checksum_sha256": "f" * 64,
            "parser_name": f"{input_mode.value}_parser",
            "parser_version": "1.0.0",
            "derived_artifact_id": 101,
        }
    create_artifact(
        session,
        project=project,
        slug="artifact",
        name="Artifact",
        artifact_type="artifact",
        filename="artifact.bin",
        checksum_sha256="a" * 64,
        size_bytes=12,
        mime_type="application/octet-stream",
        storage_uri="file:///tmp/artifact.bin",
        input_mode=input_mode,
        metadata=metadata,
    )
    manifest = parse_manifest(
        {
            "name": f"input_snapshot_{input_mode.value}",
            "cases": [{"id": "case_a", "prompt": "Final task"}],
            "artifacts": ["artifact"],
            "models": [
                {
                    "id": "model_a",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {},
                }
            ],
            "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
            "warmers": [{"id": "warmer_a", "prompt": "Prior"}],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    assert run is not None
    assert run.model_input_snapshot["artifact_input_mode"] == input_mode.value
    assert run.model_input_snapshot["artifact_inputs"][0]["input_mode"] == input_mode.value
    if metadata:
        artifact_input = run.model_input_snapshot["artifact_inputs"][0]
        assert artifact_input["source_checksum_sha256"] == "f" * 64
        assert artifact_input["parser_version"] == "1.0.0"
        assert artifact_input["derived_artifact_id"] == 101
        assert run.model_input_snapshot["derived_bundle"]["derived_artifact_ids"] == [101]
        assert run.model_input_snapshot["derived_bundle_checksum_sha256"] is not None
    else:
        assert run.model_input_snapshot["derived_bundle"] is None
        assert run.model_input_snapshot["derived_bundle_checksum_sha256"] is None


def test_run_model_input_snapshot_records_stable_mixed_derived_bundle_checksum(
    session,
) -> None:
    project = _project(session)
    for slug, input_mode, derived_id in [
        ("paper_text", ArtifactInputMode.PDF_TEXT, 201),
        ("table", ArtifactInputMode.TABLE_EXTRACTION, 202),
    ]:
        create_artifact(
            session,
            project=project,
            slug=slug,
            name=slug,
            artifact_type="artifact",
            storage_uri=f"file:///tmp/{slug}.json",
            checksum_sha256=f"{derived_id}" * 16,
            input_mode=input_mode,
            metadata={
                "source_artifact_id": 99,
                "source_checksum_sha256": "c" * 64,
                "parser_name": input_mode.value,
                "parser_version": "1.0.0",
                "derived_artifact_id": derived_id,
            },
        )

    def build(name: str, artifact_ids: list[str]):
        manifest = parse_manifest(
            {
                "name": name,
                "cases": [{"id": "case_a", "prompt": "Final task"}],
                "artifacts": artifact_ids,
                "models": [
                    {
                        "id": "model_a",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "params": {},
                    }
                ],
                "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
                "warmers": [{"id": "warmer_a", "prompt": "Prior"}],
                "design": {"type": "full_factorial", "replicates": 1},
                "evaluation": {"evaluators": []},
            }
        )
        experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
        session.flush()
        return session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    first = build("mixed_derived_a", ["paper_text", "table"])
    second = build("mixed_derived_b", ["table", "paper_text"])

    assert first is not None
    assert second is not None
    assert first.model_input_snapshot["artifact_input_mode"] == MIXED_DERIVED_BUNDLE_INPUT_MODE
    assert first.model_input_snapshot["derived_bundle"]["derived_artifact_ids"] == [201, 202]
    assert (
        first.model_input_snapshot["derived_bundle_checksum_sha256"]
        == second.model_input_snapshot["derived_bundle_checksum_sha256"]
    )


def test_run_model_input_snapshot_uses_real_preprocessed_derived_ids(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )
    preprocessing = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.flush()
    derived = session.get(Artifact, preprocessing.derived_artifact_ids[0])
    assert derived is not None

    manifest = parse_manifest(
        {
            "name": "real_pdf_text_snapshot",
            "cases": [{"id": "case_a", "prompt": "Final task"}],
            "artifacts": [derived.slug],
            "models": [
                {
                    "id": "model_a",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {},
                }
            ],
            "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
            "warmers": [{"id": "warmer_a", "prompt": "Prior"}],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    assert run is not None
    assert run.model_input_snapshot["artifact_inputs"][0]["derived_artifact_id"] == derived.id
    assert run.model_input_snapshot["derived_bundle"]["derived_artifact_ids"] == [derived.id]


def test_run_model_input_snapshot_records_real_ocr_text_derived_ids(
    session, tmp_path: Path
) -> None:
    project = _project(session)
    source_path = tmp_path / "figure.png"
    _write_png_fixture(source_path)
    ocr_backend = tmp_path / "ocr_backend.py"
    ocr_backend.write_text("import sys\nprint('detected text')\n", encoding="utf-8")
    storage_root = tmp_path / "artifact-store"
    source = register_file_artifact(
        session,
        project=project,
        slug="figure",
        name="Figure",
        source_path=source_path,
        storage_root=storage_root,
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
    )
    preprocessing = preprocess_image_visual_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
        ocr_command=[sys.executable, str(ocr_backend)],
    )
    session.flush()
    ocr_text = session.get(Artifact, preprocessing.derived_artifact_ids[1])
    assert ocr_text is not None

    manifest = parse_manifest(
        {
            "name": "real_ocr_text_snapshot",
            "cases": [{"id": "case_a", "prompt": "Final task"}],
            "artifacts": [ocr_text.slug],
            "models": [
                {
                    "id": "model_a",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {},
                }
            ],
            "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
            "warmers": [{"id": "warmer_a", "prompt": "Prior"}],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    assert run is not None
    assert run.model_input_snapshot["artifact_input_mode"] == ArtifactInputMode.OCR_TEXT.value
    assert run.model_input_snapshot["artifact_inputs"][0]["derived_artifact_id"] == ocr_text.id
    assert run.model_input_snapshot["derived_bundle"]["derived_artifact_ids"] == [ocr_text.id]


def test_run_model_input_snapshot_rejects_mixed_artifact_input_modes(session) -> None:
    project = _project(session)
    create_artifact(
        session,
        project=project,
        slug="paper_text",
        name="Paper text",
        artifact_type="text",
        storage_uri="file:///tmp/paper.txt",
        input_mode=ArtifactInputMode.PDF_TEXT,
        metadata={
            "source_artifact_id": 42,
            "source_checksum_sha256": "f" * 64,
            "parser_name": "pdf_text",
            "parser_version": "1.0.0",
            "derived_artifact_id": 101,
        },
    )
    create_artifact(
        session,
        project=project,
        slug="figure",
        name="Figure",
        artifact_type="image",
        storage_uri="file:///tmp/figure.png",
        input_mode=ArtifactInputMode.IMAGE_DIRECT,
    )
    manifest = parse_manifest(
        {
            "name": "mixed_artifacts",
            "cases": [{"id": "case_a", "prompt": "Final task"}],
            "artifacts": ["paper_text", "figure"],
            "models": [
                {
                    "id": "model_a",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "params": {},
                }
            ],
            "system_prompts": [{"id": "sys_a", "prompt": "System prompt"}],
            "warmers": [{"id": "warmer_a", "messages": [{"role": "user", "content": "Prior"}]}],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    with pytest.raises(ValueError, match="cannot mix artifact input modes"):
        create_experiment_from_manifest(session, project=project, manifest=manifest)


def test_artifact_creation_rejects_mixed_derived_bundle_as_artifact_mode(session) -> None:
    project = _project(session)

    with pytest.raises(ValueError, match=MIXED_DERIVED_BUNDLE_INPUT_MODE):
        create_artifact(
            session,
            project=project,
            slug="mixed_bundle",
            name="Mixed bundle",
            artifact_type="bundle",
            storage_uri="file:///tmp/mixed-bundle.json",
            input_mode=MIXED_DERIVED_BUNDLE_INPUT_MODE,
        )


def test_preprocessing_api_starts_pdf_text_and_lists_sanitized_outputs(
    client, session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    monkeypatch.setenv("MODEL_EVAL_ARTIFACT_STORAGE_ROOT", str(storage_root))
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
    )
    session.commit()

    created = client.post(
        f"/projects/{project.slug}/library/artifacts/{source.slug}/preprocessing-runs",
        json={"parser_name": "pdf_text"},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["status"] == "completed"
    assert payload["parser_name"] == "pdf_text"
    assert payload["source_artifact"]["id"] == source.id
    assert payload["derived_artifacts"][0]["input_mode"] == ArtifactInputMode.PDF_TEXT.value
    assert payload["derived_artifacts"][0]["metadata"]["source_artifact_id"] == source.id
    assert "storage_uri" not in payload["derived_artifacts"][0]
    assert "uri" not in payload["derived_artifacts"][0]

    runs = client.get(
        f"/projects/{project.slug}/library/artifacts/{source.slug}/preprocessing-runs"
    )
    assert runs.status_code == 200
    assert runs.json()[0]["id"] == payload["id"]

    derived = client.get(f"/projects/{project.slug}/library/artifacts/{source.slug}/derived-artifacts")
    assert derived.status_code == 200
    assert derived.json()[0]["id"] == payload["derived_artifacts"][0]["id"]
    assert "Alpha beta" not in json.dumps(derived.json())


def test_preprocessing_api_hides_retrieval_chunk_text_by_default(
    client, session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    monkeypatch.setenv("MODEL_EVAL_ARTIFACT_STORAGE_ROOT", str(storage_root))
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper_text",
        name="Paper text",
        text="Alpha beta\nGamma delta",
        filename="paper.txt",
        storage_root=storage_root,
    )
    session.commit()

    created = client.post(
        f"/projects/{project.slug}/library/artifacts/{source.slug}/preprocessing-runs",
        json={"parser_name": "retrieval_chunks"},
    )

    assert created.status_code == 201
    assert created.json()["status"] == "completed"
    assert "chunk_text" not in created.json()["derived_artifacts"][0]["metadata"]
    assert "Alpha beta" not in json.dumps(created.json())

    derived = client.get(
        f"/projects/{project.slug}/library/artifacts/{source.slug}/derived-artifacts"
    )
    assert derived.status_code == 200
    assert "chunk_text" not in derived.json()[0]["metadata"]
    assert "Alpha beta" not in json.dumps(derived.json())


def test_preprocessing_api_sanitizes_paper_card_citation_preview(
    client, session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    monkeypatch.setenv("MODEL_EVAL_ARTIFACT_STORAGE_ROOT", str(storage_root))
    source = ingest_text_artifact(
        session,
        project=project,
        slug="paper_text",
        name="Paper text",
        text="Alpha beta\nGamma delta",
        filename="paper.txt",
        storage_root=storage_root,
    )
    session.commit()

    created = client.post(
        f"/projects/{project.slug}/library/artifacts/{source.slug}/preprocessing-runs",
        json={
            "parser_name": "paper_card",
            "citation": {
                "title": "Visible title",
                "source_uri": "file:///private/example/paper.pdf",
                "private_notes": "Alpha beta",
            },
            "sections": [
                {
                    "title": "Intro",
                    "text": "Alpha beta",
                    "start_offset": 0,
                    "end_offset": 10,
                }
            ],
        },
    )

    assert created.status_code == 201
    payload = created.json()
    metadata = payload["derived_artifacts"][0]["metadata"]
    serialized = json.dumps(payload)
    assert metadata["citation"] == {"title": "Visible title"}
    assert metadata["sections"] == [{"title": "Intro", "start_offset": 0, "end_offset": 10}]
    assert "source_uri" not in serialized
    assert "private_notes" not in serialized
    assert "/private/example" not in serialized
    assert "Alpha beta" not in serialized


def test_preprocessing_api_reports_missing_local_file_without_private_path(
    client, session
) -> None:
    project = _project(session)
    source = create_artifact(
        session,
        project=project,
        slug="missing_pdf",
        name="Missing PDF",
        artifact_type="pdf",
        uri="file:///private/example/missing.pdf",
        storage_uri="file:///private/example/missing.pdf",
        filename="missing.pdf",
        checksum_sha256="a" * 64,
        input_mode=ArtifactInputMode.DIRECT_FILE,
    )
    session.commit()

    created = client.post(
        f"/projects/{project.slug}/library/artifacts/{source.slug}/preprocessing-runs",
        json={"parser_name": "pdf_text"},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["status"] == "failed"
    assert payload["error_kind"] == "missing_source"
    assert payload["error_metadata"]["source_status"] == "outside_artifact_storage"
    assert "/private/example" not in json.dumps(payload)


def test_artifact_input_mode_api_and_manifest_override_bind_derived_artifact(
    client, session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(session)
    storage_root = tmp_path / "artifact-store"
    monkeypatch.setenv("MODEL_EVAL_ARTIFACT_STORAGE_ROOT", str(storage_root))
    source = _register_pdf_fixture(
        session,
        project=project,
        source_path=FIXTURES_DIR / "tiny_pdf_text.pdf",
        storage_root=storage_root,
        slug="paper",
    )
    preprocessing = preprocess_pdf_text_artifact(
        session,
        project=project,
        source_artifact=source,
        storage_root=storage_root,
    )
    session.flush()
    derived = session.get(Artifact, preprocessing.derived_artifact_ids[0])
    assert derived is not None
    create_case(session, project=project, slug="case_a", name="Case A", prompt="Final task")
    create_model_config(
        session,
        project=project,
        slug="model_a",
        name="Model A",
        provider="openai",
        model="gpt-5.5",
    )
    create_system_prompt(session, project=project, slug="sys_a", name="Sys A", prompt="System")
    create_conversation_warmer(
        session,
        project=project,
        slug="warmer_a",
        name="Warmer A",
        intent="Prior",
    )
    session.commit()

    selected = client.patch(
        f"/projects/{project.slug}/library/artifacts/paper/input-mode",
        json={"input_mode": ArtifactInputMode.PDF_TEXT.value},
    )

    assert selected.status_code == 200
    assert selected.json()["input_mode"] == ArtifactInputMode.PDF_TEXT.value

    manifest = parse_manifest(
        {
            "name": "artifact_input_mode_selection_from_patch",
            "cases": ["case_a"],
            "artifacts": ["paper"],
            "models": ["model_a"],
            "system_prompts": ["sys_a"],
            "warmers": ["warmer_a"],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )
    experiment = create_experiment_from_manifest(session, project=project, manifest=manifest)
    session.commit()
    run = session.scalar(select(Run).where(Run.experiment_id == experiment.id))

    assert run is not None
    assert run.model_input_snapshot["artifact_input_mode"] == ArtifactInputMode.PDF_TEXT.value
    assert run.model_input_snapshot["artifact_inputs"][0]["derived_artifact_id"] == derived.id
    assert run.model_input_snapshot["derived_bundle"]["derived_artifact_ids"] == [derived.id]

    manifest_override = parse_manifest(
        {
            "name": "artifact_input_mode_selection_from_manifest",
            "cases": ["case_a"],
            "artifacts": [{"id": "paper", "input_mode": ArtifactInputMode.PDF_TEXT.value}],
            "models": ["model_a"],
            "system_prompts": ["sys_a"],
            "warmers": ["warmer_a"],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )
    experiment = create_experiment_from_manifest(
        session, project=project, manifest=manifest_override
    )
    session.commit()
    override_run = session.scalar(
        select(Run).where(Run.experiment_id == experiment.id)
    )

    assert override_run is not None
    assert (
        override_run.model_input_snapshot["artifact_inputs"][0]["derived_artifact_id"]
        == derived.id
    )


def test_artifact_input_mode_manifest_override_requires_derived_artifact(session) -> None:
    project = _project(session)
    create_artifact(
        session,
        project=project,
        slug="paper",
        name="Paper",
        artifact_type="pdf",
        input_mode=ArtifactInputMode.DIRECT_FILE,
    )
    create_case(session, project=project, slug="case_a", name="Case A", prompt="Final task")
    create_model_config(
        session,
        project=project,
        slug="model_a",
        name="Model A",
        provider="openai",
        model="gpt-5.5",
    )
    create_system_prompt(session, project=project, slug="sys_a", name="Sys A", prompt="System")
    create_conversation_warmer(
        session,
        project=project,
        slug="warmer_a",
        name="Warmer A",
        intent="Prior",
    )
    manifest = parse_manifest(
        {
            "name": "artifact_input_mode_selection_missing_preprocessing",
            "cases": ["case_a"],
            "artifacts": [{"id": "paper", "input_mode": ArtifactInputMode.PDF_TEXT.value}],
            "models": ["model_a"],
            "system_prompts": ["sys_a"],
            "warmers": ["warmer_a"],
            "design": {"type": "full_factorial", "replicates": 1},
            "evaluation": {"evaluators": []},
        }
    )

    with pytest.raises(ValueError, match="no completed derived artifact exists"):
        create_experiment_from_manifest(session, project=project, manifest=manifest)
