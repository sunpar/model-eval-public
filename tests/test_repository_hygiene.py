from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRS = {".git", ".pytest_cache", ".ruff_cache", ".venv", "node_modules", "__pycache__"}
RUNTIME_TOKEN = "dock" + "er"
RUNTIME_FILE_TOKEN = RUNTIME_TOKEN + "file"
COMPOSE_FILE_TOKEN = RUNTIME_TOKEN + "-compose"
FORBIDDEN_FILE_PATTERNS = [
    re.compile(rf"(^|/){RUNTIME_FILE_TOKEN}(?:\..*)?$", re.IGNORECASE),
    re.compile(rf"(^|/){COMPOSE_FILE_TOKEN}\.(?:ya?ml)$", re.IGNORECASE),
    re.compile(r"(^|/)compose\.(?:ya?ml)$", re.IGNORECASE),
    re.compile(rf"(^|/)\.{RUNTIME_TOKEN}ignore$", re.IGNORECASE),
]
FORBIDDEN_TEXT_PATTERNS = [
    re.compile(rf"\b{RUNTIME_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{COMPOSE_FILE_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{RUNTIME_FILE_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{RUNTIME_TOKEN}\s+compose\b", re.IGNORECASE),
]


def _repo_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if any(part in SKIPPED_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        if not path.is_file():
            continue
        files.append(path.relative_to(REPO_ROOT))
    return files


def test_repository_has_no_container_runtime_specific_files() -> None:
    matches = [
        str(path)
        for path in _repo_files()
        if any(pattern.search(path.as_posix()) for pattern in FORBIDDEN_FILE_PATTERNS)
    ]

    assert matches == []


def test_repository_has_no_container_runtime_specific_text_references() -> None:
    matches: list[str] = []
    for relative_path in _repo_files():
        path = REPO_ROOT / relative_path
        try:
            contents = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(contents.splitlines(), start=1):
            if any(pattern.search(line) for pattern in FORBIDDEN_TEXT_PATTERNS):
                matches.append(f"{relative_path}:{line_number}: {line.strip()}")

    assert matches == []
