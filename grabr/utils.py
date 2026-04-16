"""Utility helpers for grabr."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence


INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTISPACE_RE = re.compile(r"\s+")


def expand_output_dir(output_dir: str) -> Path:
    path = Path(output_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    clean = INVALID_CHARS_RE.sub("_", name).strip(" .")
    clean = MULTISPACE_RE.sub(" ", clean)
    return clean or "unknown"


def file_exists(path: Path) -> bool:
    return path.exists() and path.is_file()


def has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def with_retry(
    func: Callable[[], object],
    retries: int = 1,
    backoff_seconds: float = 1.5,
) -> object:
    attempt = 0
    last_error: Exception | None = None
    while attempt <= retries:
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == retries:
                break
            time.sleep(backoff_seconds * (2**attempt))
            attempt += 1
    assert last_error is not None
    raise last_error


def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def run_command_stream(
    command: Sequence[str],
    on_line: Callable[[str], None] | None = None,
) -> int:
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    for line in process.stdout:
        if on_line:
            on_line(line.rstrip("\n"))
    return process.wait()


def chunked(items: Iterable[str], size: int) -> list[list[str]]:
    batch: list[str] = []
    chunks: list[list[str]] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            chunks.append(batch)
            batch = []
    if batch:
        chunks.append(batch)
    return chunks
