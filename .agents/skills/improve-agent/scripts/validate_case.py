#!/usr/bin/env python3
"""Validate an AIK improvement case before comparison."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import DataError, read_jsonl


REQUIRED_FILES = (
    "case.yaml",
    "decision.yaml",
    "test_cases.jsonl",
    "baseline.jsonl",
    "candidate.jsonl",
)


def validate(case_dir: str | Path) -> tuple[list[str], list[str]]:
    directory = Path(case_dir)
    errors: list[str] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return [f"Case directory not found: {directory}"], warnings

    for filename in REQUIRED_FILES:
        if not (directory / filename).is_file():
            errors.append(f"Missing required file: {filename}")
    if errors:
        return errors, warnings

    try:
        cases = read_jsonl(directory / "test_cases.jsonl", "id")
        baseline = read_jsonl(directory / "baseline.jsonl", "case_id")
        candidate = read_jsonl(directory / "candidate.jsonl", "case_id")
    except DataError as exc:
        return [str(exc)], warnings

    if not cases:
        errors.append("test_cases.jsonl has no test cases")
    if not baseline:
        errors.append("baseline.jsonl has no results")
    if not candidate:
        errors.append("candidate.jsonl has no results")

    case_ids = {case["id"] for case in cases}
    baseline_ids = {item["case_id"] for item in baseline}
    candidate_ids = {item["case_id"] for item in candidate}

    for case in cases:
        for field in ("description", "input", "expected"):
            if field not in case:
                errors.append(f"Test case {case['id']} is missing '{field}'")
        if "tags" not in case:
            warnings.append(f"Test case {case['id']} has no tags")

    for label, results in (("baseline", baseline), ("candidate", candidate)):
        for result in results:
            if not isinstance(result.get("passed"), bool):
                errors.append(f"{label} result {result['case_id']} requires boolean 'passed'")
            if "output" not in result:
                warnings.append(f"{label} result {result['case_id']} has no output")

    for unknown in sorted((baseline_ids | candidate_ids) - case_ids):
        errors.append(f"Result references unknown test case: {unknown}")
    for missing in sorted(case_ids - baseline_ids):
        warnings.append(f"Missing baseline result: {missing}")
    for missing in sorted(case_ids - candidate_ids):
        warnings.append(f"Missing candidate result: {missing}")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an AIK improvement case")
    parser.add_argument("case_dir")
    args = parser.parse_args()
    errors, warnings = validate(args.case_dir)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
