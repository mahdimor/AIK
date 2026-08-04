#!/usr/bin/env python3
"""Validate, compare, and render one AIK improvement case."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import read_json, write_json
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

    baseline = directory / "baseline.evaluated.jsonl"
    candidate = directory / "candidate.evaluated.jsonl"
    if not baseline.exists() or not baseline.stat().st_size:
        baseline = directory / "baseline.jsonl"
    if not candidate.exists() or not candidate.stat().st_size:
        candidate = directory / "candidate.jsonl"
    suite = read_json(directory / "evaluation.json") if (directory / "evaluation.json").exists() else None
    reliability_path = directory / "reliability.json"
    reliability = read_json(reliability_path) if reliability_path.exists() else None
    result = compare(directory / "test_cases.jsonl", baseline, candidate, suite, reliability)
    # Keep generated artifacts reproducible and portable across machines.
    result["inputs"] = {
        "test_cases": "test_cases.jsonl",
        "baseline": baseline.name,
        "candidate": candidate.name,
    }
    comparison_path = write_json(directory / "comparison.json", result)
    review_path = render(comparison_path, directory / "case.yaml", directory / "review.html", reliability)
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
