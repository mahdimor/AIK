#!/usr/bin/env python3
"""Promote accepted improvement cases into a regression dataset."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from common import DataError, index_by, read_jsonl, write_json, write_jsonl


ACCEPTED_DECISIONS = {"accepted", "accepted_with_conditions"}


def _decision_status(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    return None


def promote(
    case_dir: str | Path,
    destination: str | Path,
    statuses: set[str],
    explicit_ids: set[str] | None = None,
) -> list[str]:
    directory = Path(case_dir).resolve()
    decision = _decision_status(directory / "decision.yaml")
    if decision not in ACCEPTED_DECISIONS:
        raise DataError(f"Decision must be accepted before promotion; current status is '{decision}'")

    cases = index_by(read_jsonl(directory / "test_cases.jsonl", "id"), "id")
    comparison = json.loads((directory / "comparison.json").read_text(encoding="utf-8"))
    eligible = {item["case_id"] for item in comparison["items"] if item["status"] in statuses}
    selected = eligible if explicit_ids is None else eligible & explicit_ids
    missing = (explicit_ids or set()) - eligible
    if missing:
        raise DataError(f"Requested IDs are not eligible for the selected status: {', '.join(sorted(missing))}")

    destination_path = Path(destination)
    existing_records = read_jsonl(destination_path, "id") if destination_path.exists() else []
    merged = index_by(existing_records, "id")
    promoted: list[str] = []
    for case_id in sorted(selected):
        if case_id not in cases:
            raise DataError(f"Comparison references missing test case: {case_id}")
        if case_id not in merged:
            merged[case_id] = cases[case_id]
            promoted.append(case_id)
    write_jsonl(destination_path, merged.values())
    write_json(
        directory / "promotion.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "destination": str(destination_path),
            "statuses": sorted(statuses),
            "promoted_case_ids": promoted,
        },
    )
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote accepted AIK cases to regression tests")
    parser.add_argument("case_dir")
    parser.add_argument("destination")
    parser.add_argument("--status", action="append", choices=("improved", "regressed", "unchanged"), default=None)
    parser.add_argument("--ids", nargs="*")
    args = parser.parse_args()
    try:
        promoted = promote(
            args.case_dir,
            args.destination,
            set(args.status or ["improved"]),
            set(args.ids) if args.ids else None,
        )
    except (DataError, FileNotFoundError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"promoted={len(promoted)}")
    for case_id in promoted:
        print(case_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
