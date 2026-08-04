#!/usr/bin/env python3
"""Shared helpers for AIK's dependency-free scripts."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Iterable


class DataError(ValueError):
    """Raised when an AIK artifact is invalid."""


def read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise DataError(f"File not found: {file_path}")
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(f"Invalid JSON in {file_path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise DataError(f"Expected an object in {file_path}")
    return value


def fingerprint(value: Any) -> str:
    """Return a stable content fingerprint for JSON-compatible data."""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_file(path: str | Path) -> str:
    """Return the sha256 content hash of a regular file's raw bytes."""
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def read_jsonl(path: str | Path, id_field: str) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        raise DataError(f"File not found: {file_path}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with file_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataError(f"Invalid JSON in {file_path}:{line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise DataError(f"Expected an object in {file_path}:{line_number}")
            record_id = record.get(id_field)
            if not isinstance(record_id, str) or not record_id.strip():
                raise DataError(f"Missing non-empty '{id_field}' in {file_path}:{line_number}")
            if record_id in seen:
                raise DataError(f"Duplicate {id_field} '{record_id}' in {file_path}")
            seen.add(record_id)
            records.append(record)
    return records


def index_by(records: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(record[key]): record for record in records}


def write_json(path: str | Path, data: Any) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(file_path)
    return file_path


def write_jsonl(path: str | Path, records: Iterable[dict[str, Any]]) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    temporary.replace(file_path)
    return file_path
