#!/usr/bin/env python3
"""Compare baseline and candidate AI-agent run artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, index_by, read_jsonl, write_json


STATUSES = ("improved", "regressed", "unchanged", "not_comparable")


def _numeric_score_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float]:
    before_scores = before.get("scores") if isinstance(before.get("scores"), dict) else {}
    after_scores = after.get("scores") if isinstance(after.get("scores"), dict) else {}
    deltas: dict[str, float] = {}
    for metric in sorted(set(before_scores) & set(after_scores)):
        old = before_scores[metric]
        new = after_scores[metric]
        if (
            isinstance(old, (int, float))
            and not isinstance(old, bool)
            and isinstance(new, (int, float))
            and not isinstance(new, bool)
        ):
            deltas[metric] = round(float(new) - float(old), 6)
    return deltas


def _contract_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    old_contracts = before.get("contracts") if isinstance(before.get("contracts"), dict) else {}
    new_contracts = after.get("contracts") if isinstance(after.get("contracts"), dict) else {}
    return {
        contract: {"before": old_contracts.get(contract), "after": new_contracts.get(contract)}
        for contract in sorted(set(old_contracts) | set(new_contracts))
        if old_contracts.get(contract) != new_contracts.get(contract)
    }


def compare(test_cases_path: str | Path, baseline_path: str | Path, candidate_path: str | Path) -> dict[str, Any]:
    cases = index_by(read_jsonl(test_cases_path, "id"), "id")
    baseline = index_by(read_jsonl(baseline_path, "case_id"), "case_id")
    candidate = index_by(read_jsonl(candidate_path, "case_id"), "case_id")

    items: list[dict[str, Any]] = []
    all_ids = list(cases)
    all_ids.extend(sorted((set(baseline) | set(candidate)) - set(cases)))

    for case_id in all_ids:
        case = cases.get(case_id)
        before = baseline.get(case_id)
        after = candidate.get(case_id)
        warnings: list[str] = []
        if case is None:
            warnings.append("Result has no matching test case")
        if before is None:
            warnings.append("Missing baseline result")
        if after is None:
            warnings.append("Missing candidate result")

        if case is None or before is None or after is None:
            status = "not_comparable"
        else:
            old_pass = before.get("passed")
            new_pass = after.get("passed")
            if not isinstance(old_pass, bool) or not isinstance(new_pass, bool):
                status = "not_comparable"
                warnings.append("Both results require a boolean 'passed' field")
            elif not old_pass and new_pass:
                status = "improved"
            elif old_pass and not new_pass:
                status = "regressed"
            else:
                status = "unchanged"

        items.append(
            {
                "case_id": case_id,
                "description": (case or {}).get("description", "Unknown test case"),
                "tags": (case or {}).get("tags", []),
                "status": status,
                "warnings": warnings,
                "score_deltas": _numeric_score_deltas(before or {}, after or {}),
                "contract_changes": _contract_changes(before or {}, after or {}),
                "baseline": before,
                "candidate": after,
            }
        )

    summary = {status: sum(item["status"] == status for item in items) for status in STATUSES}
    summary["total"] = len(items)
    return {
        "format_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "test_cases": str(Path(test_cases_path)),
            "baseline": str(Path(baseline_path)),
            "candidate": str(Path(candidate_path)),
        },
        "summary": summary,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare AIK baseline and candidate runs")
    parser.add_argument("test_cases")
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--output", "-o", default="comparison.json")
    args = parser.parse_args()
    try:
        result = compare(args.test_cases, args.baseline, args.candidate)
        write_json(args.output, result)
    except DataError as exc:
        parser.error(str(exc))
    summary = result["summary"]
    print(" ".join(f"{status}={summary[status]}" for status in STATUSES))
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
