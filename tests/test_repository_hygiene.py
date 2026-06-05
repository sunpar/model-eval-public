from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TOKEN = "dock" + "er"
RUNTIME_FILE_TOKEN = RUNTIME_TOKEN + "file"
COMPOSE_FILE_TOKEN = RUNTIME_TOKEN + "-compose"
ALT_RUNTIME_TOKEN = "pod" + "man"
CONTAINER_TOKEN = "con" + "tainer"
CONTAINER_FILE_TOKEN = CONTAINER_TOKEN + "file"
COMPOSE_ENV_PREFIX = "COM" + "POSE"
CONTAINER_DAEMON_TOKEN = CONTAINER_TOKEN + "d"
KUBE_TOKEN = "kuber" + "netes"
KUBECTL_TOKEN = "kube" + "ctl"
BUILD_TOOL_TOKEN = "build" + "ah"
NERDCTL_TOKEN = "nerd" + "ctl"
K8S_TOKEN = "k" + "8s"
OCI_TOKEN = "OC" + "I"
FORBIDDEN_FILE_PATTERNS = [
    re.compile(rf"(^|/){RUNTIME_FILE_TOKEN}(?:\..*)?$", re.IGNORECASE),
    re.compile(rf"(^|/){COMPOSE_FILE_TOKEN}(?:\.[^/]+)*\.(?:ya?ml)$", re.IGNORECASE),
    re.compile(r"(^|/)compose(?:\.[^/]+)*\.(?:ya?ml)$", re.IGNORECASE),
    re.compile(
        rf"(^|/){ALT_RUNTIME_TOKEN}-compose(?:\.[^/]+)*\.(?:ya?ml)$",
        re.IGNORECASE,
    ),
    re.compile(rf"(^|/){CONTAINER_FILE_TOKEN}(?:\..*)?$", re.IGNORECASE),
    re.compile(rf"(^|/)\.{RUNTIME_TOKEN}ignore$", re.IGNORECASE),
    re.compile(rf"(^|/)\.{CONTAINER_TOKEN}ignore$", re.IGNORECASE),
]
FORBIDDEN_TEXT_PATTERNS = [
    re.compile(rf"\b{RUNTIME_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{COMPOSE_FILE_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{RUNTIME_FILE_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{RUNTIME_TOKEN}\s+compose\b", re.IGNORECASE),
    re.compile(
        rf"\b(?:{RUNTIME_TOKEN.upper()}_HOST|{RUNTIME_TOKEN.upper()}_BUILDKIT|"
        rf"{RUNTIME_TOKEN.upper()}_CONFIG)\b"
    ),
    re.compile(
        rf"\b(?:{COMPOSE_ENV_PREFIX}_PROJECT_NAME|{COMPOSE_ENV_PREFIX}_FILE|"
        rf"{COMPOSE_ENV_PREFIX}_PROFILES)\b"
    ),
    re.compile(rf"\b{ALT_RUNTIME_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{ALT_RUNTIME_TOKEN}-compose\b", re.IGNORECASE),
    re.compile(rf"\b{CONTAINER_FILE_TOKEN}\b", re.IGNORECASE),
    re.compile(rf"\b{CONTAINER_TOKEN}[\s-]+(?:runtime|image|engine|service|setup)s?\b", re.IGNORECASE),
    re.compile(r"\bOC" + r"I\s+image\b", re.IGNORECASE),
    re.compile(
        rf"\b(?:{KUBE_TOKEN}|{KUBECTL_TOKEN}|{K8S_TOKEN}|{BUILD_TOOL_TOKEN}|"
        rf"{NERDCTL_TOKEN}|{CONTAINER_DAEMON_TOKEN})\b",
        re.IGNORECASE,
    ),
]


def _matches_any(patterns: list[re.Pattern[str]], value: str) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def _repo_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(raw_path.decode("utf-8")) for raw_path in result.stdout.split(b"\0") if raw_path]


def _is_forbidden_path(path: Path) -> bool:
    return _matches_any(FORBIDDEN_FILE_PATTERNS, path.as_posix())


def _is_forbidden_line(line: str) -> bool:
    return _matches_any(FORBIDDEN_TEXT_PATTERNS, line)


def test_repository_has_no_container_runtime_specific_files() -> None:
    matches = [
        str(path)
        for path in _repo_files()
        if _is_forbidden_path(path)
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
            if _is_forbidden_line(line):
                matches.append(f"{relative_path}:{line_number}: {line.strip()}")

    assert matches == []


def test_repo_file_list_uses_tracked_files_only(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"README.md\0docs/guide.md\0")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _repo_files() == [Path("README.md"), Path("docs/guide.md")]
    assert calls == [["git", "ls-files", "-z"]]


def test_forbidden_file_patterns_cover_common_container_runtime_files() -> None:
    examples = [
        Path(RUNTIME_FILE_TOKEN),
        Path(RUNTIME_FILE_TOKEN + ".local"),
        Path(COMPOSE_FILE_TOKEN + ".yml"),
        Path(COMPOSE_FILE_TOKEN + ".override.yml"),
        Path("compose.yaml"),
        Path("compose.override.yaml"),
        Path(ALT_RUNTIME_TOKEN + "-compose.yml"),
        Path(ALT_RUNTIME_TOKEN + "-compose.override.yml"),
        Path(CONTAINER_FILE_TOKEN),
        Path("." + RUNTIME_TOKEN + "ignore"),
        Path("." + CONTAINER_TOKEN + "ignore"),
    ]

    assert all(_is_forbidden_path(path) for path in examples)


def test_default_sqlite_database_file_is_gitignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "model_eval.sqlite3"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0


def test_forbidden_text_patterns_cover_common_container_runtime_references() -> None:
    examples = [
        RUNTIME_TOKEN + " build .",
        RUNTIME_TOKEN + " compose up",
        RUNTIME_TOKEN.upper() + "_HOST=tcp://localhost:2375",
        COMPOSE_ENV_PREFIX + "_PROJECT_NAME=model_eval",
        ALT_RUNTIME_TOKEN + " run postgres",
        ALT_RUNTIME_TOKEN + "-compose up",
        CONTAINER_FILE_TOKEN + " build context",
        "host " + CONTAINER_TOKEN + " runtime smoke check",
        OCI_TOKEN + " image build",
    ]

    assert all(_is_forbidden_line(line) for line in examples)
