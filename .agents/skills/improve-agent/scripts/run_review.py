#!/usr/bin/env python3
"""Validate, compare, and render one AIK improvement case."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import read_json, write_json
from compare_runs import STATUSES, compare
from reliability import reliability_config_fingerprint
from render_review import render
from validate_case import validate


def _load_reliability(directory: Path, suite: dict | None) -> tuple[dict | None, bool, str | None]:
    """Statistics are truly optional: without a `reliability` section in
    evaluation.json, any reliability.json on disk is ignored for the
    decision (only flagged as unused). With a section, the report becomes
    mandatory and must match the current config exactly, or the decision
    is forced toward manual_review rather than silently falling back."""
    reliability_config = suite.get("reliability") if isinstance(suite, dict) else None
    reliability_path = directory / "reliability.json"

    if reliability_config is None:
        if reliability_path.exists():
            return None, False, (
                "reliability.json exists but no 'reliability' section is configured in evaluation.json; "
                "it is not used for this decision"
            )
        return None, False, None

    if not reliability_path.exists():
        return None, True, "A reliability section is configured but reliability.json is missing; run 'aik reliability' first"

    archived = read_json(reliability_path)
    expected_fingerprint = reliability_config_fingerprint(reliability_config)
    if archived.get("config_fingerprint") != expected_fingerprint:
        return None, True, "reliability.json configuration does not match evaluation.json; recompute with 'aik reliability'"
    return archived, True, None


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

    reliability, reliability_required, reliability_status_message = _load_reliability(directory, suite)
    if reliability_status_message:
        print(f"WARNING: {reliability_status_message}")

    result = compare(
        directory / "test_cases.jsonl",
        baseline,
        candidate,
        suite,
        reliability,
        reliability_required=reliability_required,
    )
    # Keep generated artifacts reproducible and portable across machines.
    result["inputs"] = {
        "test_cases": "test_cases.jsonl",
        "baseline": baseline.name,
        "candidate": candidate.name,
    }
    comparison_path = write_json(directory / "comparison.json", result)
    review_path = render(
        comparison_path,
        directory / "case.yaml",
        directory / "review.html",
        reliability,
        reliability_status_message=reliability_status_message,
    )
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
