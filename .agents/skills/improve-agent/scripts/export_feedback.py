#!/usr/bin/env python3
"""Export optimizer-visible feedback while keeping the holdout split sealed.

A prompt optimizer improves by reading why a case failed. Any case whose
diagnostic text reaches the optimizer stops being an honest estimate of
generalization. This module is the only supported way to turn AIK evidence into
optimizer input, and it never emits a `holdout` case: there is no flag to
include one.

This is a discipline mechanism, not a sandbox. AIK cannot stop a process from
reading `test_cases.jsonl` directly. What it can do is refuse to produce the
holdout as feedback, record every export, and seal the holdout so a later
change to it is detectable.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, fingerprint, index_by, read_json, read_jsonl, write_json


HOLDOUT_SPLIT = "holdout"
OPTIMIZER_VISIBLE_SPLITS = ("development", "regression", "challenge")
LEDGER_NAME = "optimizer-log.jsonl"


def _result_path(directory: Path, stage: str) -> Path | None:
    for name in (f"{stage}.evaluated.jsonl", f"{stage}.jsonl"):
        path = directory / name
        if path.exists() and path.stat().st_size:
            return path
    return None


def holdout_seal(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Fingerprint the holdout so a change to it during optimization is detectable."""
    holdout = sorted(
        (case for case in cases if case.get("split") == HOLDOUT_SPLIT),
        key=lambda case: str(case["id"]),
    )
    return {
        "count": len(holdout),
        "case_ids": [str(case["id"]) for case in holdout],
        "fingerprint": fingerprint(holdout),
    }


def _failed_evaluations(result: dict[str, Any]) -> list[dict[str, Any]]:
    evaluations = result.get("evaluations")
    if not isinstance(evaluations, list):
        return []
    return [
        item
        for item in evaluations
        if isinstance(item, dict) and item.get("status") in {"fail", "error"}
    ]


def _feedback_text(case: dict[str, Any], result: dict[str, Any] | None) -> str:
    case_id = str(case["id"])
    risk = str(case.get("risk", "medium"))
    description = str(case.get("description", "")).strip()
    if result is None:
        return f"Case {case_id} ({risk} risk): no result for this stage. No feedback available."

    passed = result.get("passed")
    verdict = str(result.get("verdict") or ("pass" if passed is True else "fail" if passed is False else "unknown"))
    lines = [f"Case {case_id} ({risk} risk) -> {verdict.upper()}."]
    if description:
        lines.append(f"Expected behavior: {description}")

    failures = _failed_evaluations(result)
    for failure in failures:
        evaluator_id = str(failure.get("evaluator_id", "unknown"))
        status = str(failure.get("status", "fail"))
        reason = str(failure.get("reason", "")).strip() or "no reason recorded"
        lines.append(f"[{evaluator_id}] {status}: {reason}")
    if not failures and passed is False:
        lines.append("Marked as failed without a per-evaluator reason; inspect the raw output.")
    return "\n".join(lines)


def case_feedback(case: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "case_id": str(case["id"]),
        "split": case.get("split"),
        "risk": case.get("risk", "medium"),
        "tags": case.get("tags", []),
        "description": case.get("description"),
        "input": case.get("input"),
        "expected": case.get("expected"),
        "passed": (result or {}).get("passed"),
        "verdict": (result or {}).get("verdict"),
        "output": (result or {}).get("output"),
        "failed_evaluations": _failed_evaluations(result or {}),
        "feedback": _feedback_text(case, result),
    }


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise DataError(f"Refusing non-regular optimizer ledger: {path}")
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise DataError(f"Invalid JSON in {path}: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise DataError(f"Expected an object in optimizer ledger: {path}")
        records.append(record)
    return records


def _append_ledger(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise DataError(f"Refusing non-regular optimizer ledger: {path}")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def export_feedback(
    case_dir: str | Path,
    stage: str = "candidate",
    output: str | Path | None = None,
    *,
    record_export: bool = True,
) -> dict[str, Any]:
    """Build optimizer-visible feedback for one stage, with the holdout withheld."""
    directory = Path(case_dir)
    if not directory.is_dir():
        raise DataError(f"Case directory not found: {directory}")
    if stage not in {"baseline", "candidate"}:
        raise DataError(f"Unknown stage: {stage}")

    if not (directory / "dataset.json").exists():
        raise DataError(
            "Optimizer feedback requires a Dataset v2 case: dataset.json is missing. "
            "A dataset without splits cannot hold anything out, so no honest estimate "
            "can be produced after optimization."
        )

    cases = read_jsonl(directory / "test_cases.jsonl", "id")
    missing_split = [str(case["id"]) for case in cases if not case.get("split")]
    if missing_split:
        raise DataError(
            "Every case needs a split before exporting optimizer feedback. Missing on: "
            + ", ".join(sorted(missing_split))
        )

    seal = holdout_seal(cases)
    if not seal["count"]:
        raise DataError(
            "Refusing to export optimizer feedback: this dataset has no holdout cases. "
            "Without a holdout the optimized result is unfalsifiable. Mark at least one "
            "representative case with \"split\": \"holdout\" first."
        )

    results_path = _result_path(directory, stage)
    if results_path is None:
        raise DataError(f"No non-empty {stage}.evaluated.jsonl or legacy {stage}.jsonl")
    results = index_by(read_jsonl(results_path, "case_id"), "case_id")

    visible = [case for case in cases if case.get("split") != HOLDOUT_SPLIT]
    unknown_splits = sorted(
        {
            str(case.get("split"))
            for case in visible
            if case.get("split") not in OPTIMIZER_VISIBLE_SPLITS
        }
    )
    if unknown_splits:
        raise DataError(
            "Unknown split(s) cannot be classified as optimizer-visible: "
            + ", ".join(unknown_splits)
        )

    items = [case_feedback(case, results.get(str(case["id"]))) for case in visible]
    without_result = [item["case_id"] for item in items if item["passed"] is None]

    manifest = read_json(directory / "dataset.json")
    ledger_path = directory / LEDGER_NAME
    previous = _read_ledger(ledger_path)
    warnings: list[str] = []
    if previous:
        first_holdout = previous[0].get("holdout")
        first_seal = first_holdout.get("fingerprint") if isinstance(first_holdout, dict) else None
        if not isinstance(first_seal, str) or not first_seal:
            raise DataError("Optimizer ledger anchor is missing its holdout fingerprint")
        if first_seal != seal["fingerprint"]:
            warnings.append(
                "The holdout changed since the first optimizer export. Any generalization "
                "claim measured against it is void; rebuild the holdout and restart."
            )
    if without_result:
        warnings.append(
            f"{len(without_result)} optimizer-visible case(s) have no {stage} result and carry no feedback"
        )

    export_number = len(previous) + 1
    report = {
        "format_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_dir": str(directory),
        "stage": stage,
        "dataset_id": manifest.get("dataset_id"),
        "dataset_version": manifest.get("version"),
        # This artifact is optimizer-visible, so its digest covers only the same
        # visible records. The full dataset and holdout seals stay in the local ledger.
        "dataset_fingerprint": fingerprint(visible),
        "export_number": export_number,
        "visible_splits": list(OPTIMIZER_VISIBLE_SPLITS),
        "summary": {
            "visible_cases": len(items),
            "visible_failed": sum(item["passed"] is False for item in items),
            "visible_passed": sum(item["passed"] is True for item in items),
            "withheld_holdout_cases": seal["count"],
        },
        # The holdout is counted but never described or hashed into this visible artifact.
        "holdout": {"count": seal["count"]},
        "warnings": warnings,
        "items": items,
    }

    if record_export:
        _append_ledger(
            ledger_path,
            {
                "exported_at": report["generated_at"],
                "export_number": export_number,
                "stage": stage,
                "visible_cases": len(items),
                "dataset_fingerprint": report["dataset_fingerprint"],
                "holdout": {"count": seal["count"], "fingerprint": seal["fingerprint"]},
            },
        )

    destination = Path(output) if output else directory / "feedback.json"
    write_json(destination, report)
    report["destination"] = str(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export optimizer-visible feedback with the holdout withheld"
    )
    parser.add_argument("case_dir")
    parser.add_argument("--stage", default="candidate", choices=("baseline", "candidate"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    try:
        report = export_feedback(args.case_dir, args.stage, args.output)
    except DataError as exc:
        parser.error(str(exc))
    summary = report["summary"]
    print(" ".join(f"{key}={summary[key]}" for key in sorted(summary)))
    print(f"export_number={report['export_number']}")
    print(f"feedback={report['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
