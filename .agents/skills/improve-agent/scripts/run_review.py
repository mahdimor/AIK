#!/usr/bin/env python3
"""Validate, compare, and render one AIK improvement case."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import write_json
from compare_runs import STATUSES, compare
from render_review import render
from validate_case import validate


def run(case_dir: str | Path) -> tuple[Path, Path, dict]:
    directory = Path(case_dir).resolve()
    errors, warnings = validate(directory)
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        raise ValueError("; ".join(errors))

    result = compare(directory / "test_cases.jsonl", directory / "baseline.jsonl", directory / "candidate.jsonl")
    # Keep generated artifacts reproducible and portable across machines.
    result["inputs"] = {
        "test_cases": "test_cases.jsonl",
        "baseline": "baseline.jsonl",
        "candidate": "candidate.jsonl",
    }
    comparison_path = write_json(directory / "comparison.json", result)
    review_path = render(comparison_path, directory / "case.yaml", directory / "review.html")
    return comparison_path, review_path, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an AIK before/after review")
    parser.add_argument("case_dir")
    args = parser.parse_args()
    try:
        comparison_path, review_path, result = run(args.case_dir)
    except ValueError as exc:
        parser.error(str(exc))
    summary = result["summary"]
    print(" ".join(f"{status}={summary[status]}" for status in STATUSES))
    print(comparison_path)
    print(review_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
