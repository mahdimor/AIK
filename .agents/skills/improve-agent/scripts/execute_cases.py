#!/usr/bin/env python3
"""Execute AIK cases through a framework-agnostic subprocess runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from common import DataError, index_by, read_jsonl, write_jsonl


STAGES = ("baseline", "candidate")


def _execution_error(case_id: str, kind: str, message: str, duration_ms: int, exit_code: int | None = None) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "passed": False,
        "output": "",
        "error": {"type": kind, "message": message},
        "execution": {"duration_ms": duration_ms, "exit_code": exit_code},
    }


def _run_one(
    case: dict[str, Any],
    stage: str,
    command: Sequence[str],
    timeout_seconds: float,
    cwd: Path,
) -> tuple[dict[str, Any], bool]:
    case_id = case["id"]
    payload = {"protocol_version": "1.0", "stage": stage, "case": case}
    environment = os.environ.copy()
    environment["AIK_STAGE"] = stage
    environment["AIK_CASE_ID"] = case_id
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            cwd=cwd,
            env=environment,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        duration = round((time.monotonic() - started) * 1000)
        return _execution_error(case_id, "timeout", f"Runner exceeded {timeout_seconds:g} seconds", duration), True
    except OSError as exc:
        duration = round((time.monotonic() - started) * 1000)
        return _execution_error(case_id, "runner_start", str(exc), duration), True

    duration = round((time.monotonic() - started) * 1000)
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        detail = stderr[-4000:] or f"Runner exited with code {completed.returncode}"
        result = _execution_error(case_id, "runner_exit", detail, duration, completed.returncode)
        result["output"] = completed.stdout.strip()
        return result, True

    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _execution_error(case_id, "invalid_json", f"Runner stdout is not one JSON object: {exc.msg}", duration, 0), True
    if not isinstance(response, dict):
        return _execution_error(case_id, "invalid_response", "Runner response must be a JSON object", duration, 0), True
    if response.get("case_id") not in (None, case_id):
        return _execution_error(case_id, "case_id_mismatch", "Runner returned a different case_id", duration, 0), True
    if not isinstance(response.get("passed"), bool):
        return _execution_error(case_id, "invalid_response", "Runner response requires boolean 'passed'", duration, 0), True

    result = dict(response)
    result["case_id"] = case_id
    result.setdefault("output", "")
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    result["execution"] = {**execution, "duration_ms": duration, "exit_code": 0}
    return result, False


def execute(
    case_dir: str | Path,
    stage: str,
    command: Sequence[str],
    *,
    timeout_seconds: float = 60,
    selected_ids: set[str] | None = None,
    selected_tags: set[str] | None = None,
    resume: bool = False,
    overwrite: bool = False,
    runner_cwd: str | Path | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise DataError(f"Stage must be one of: {', '.join(STAGES)}")
    if not command:
        raise DataError("Runner command is required after '--'")
    if timeout_seconds <= 0:
        raise DataError("Timeout must be greater than zero")
    if resume and overwrite:
        raise DataError("Use either resume or overwrite, not both")

    directory = Path(case_dir).resolve()
    cases = read_jsonl(directory / "test_cases.jsonl", "id")
    known_ids = {case["id"] for case in cases}
    unknown_ids = (selected_ids or set()) - known_ids
    if unknown_ids:
        raise DataError(f"Unknown selected case IDs: {', '.join(sorted(unknown_ids))}")

    selected: list[dict[str, Any]] = []
    for case in cases:
        if selected_ids and case["id"] not in selected_ids:
            continue
        tags = set(case.get("tags", [])) if isinstance(case.get("tags"), list) else set()
        if selected_tags and not (tags & selected_tags):
            continue
        selected.append(case)
    if not selected:
        raise DataError("No test cases matched the selected IDs and tags")

    working_directory = Path(runner_cwd or Path.cwd()).resolve()
    if not working_directory.is_dir():
        raise DataError(f"Runner working directory not found: {working_directory}")

    destination = directory / f"{stage}.jsonl"
    existing = read_jsonl(destination, "case_id") if destination.exists() else []
    if existing and not resume and not overwrite:
        raise DataError(f"{destination.name} already contains results; use --resume or --overwrite")
    results = [] if overwrite else list(existing)
    result_index = index_by(results, "case_id")

    executed = skipped = execution_errors = 0
    for case in selected:
        case_id = case["id"]
        if resume and case_id in result_index:
            skipped += 1
            continue
        result, had_execution_error = _run_one(case, stage, command, timeout_seconds, working_directory)
        result_index[case_id] = result
        results = [result_index[key] for key in sorted(result_index)]
        write_jsonl(destination, results)
        executed += 1
        execution_errors += int(had_execution_error)
        state = "ERROR" if had_execution_error else ("PASS" if result["passed"] else "FAIL")
        print(f"{case_id}: {state}")

    return {
        "stage": stage,
        "destination": destination,
        "selected": len(selected),
        "executed": executed,
        "skipped": skipped,
        "execution_errors": execution_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute AIK cases through an external command",
        epilog="Place the runner command after --, for example: -- python my_runner.py",
    )
    parser.add_argument("case_dir")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--id", action="append", dest="ids")
    parser.add_argument("--tag", action="append", dest="tags")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--overwrite", action="store_true")
    parser.add_argument("--cwd")
    raw_arguments = sys.argv[1:]
    command: list[str] = []
    if "--" in raw_arguments:
        separator = raw_arguments.index("--")
        command = raw_arguments[separator + 1 :]
        raw_arguments = raw_arguments[:separator]
    args = parser.parse_args(raw_arguments)
    try:
        summary = execute(
            args.case_dir,
            args.stage,
            command,
            timeout_seconds=args.timeout,
            selected_ids=set(args.ids) if args.ids else None,
            selected_tags=set(args.tags) if args.tags else None,
            resume=args.resume,
            overwrite=args.overwrite,
            runner_cwd=args.cwd,
        )
    except DataError as exc:
        parser.error(str(exc))
    print(" ".join(f"{key}={summary[key]}" for key in ("executed", "skipped", "execution_errors")))
    print(summary["destination"])
    return 1 if summary["execution_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
