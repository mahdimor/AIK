#!/usr/bin/env python3
"""Validate legacy and Dataset v2 AIK improvement cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import DataError, fingerprint, read_json, read_jsonl
from evaluate_results import _validate_suite
from judge import validate_judge_config
from objectives import validate_objectives_config
from optimize import validate_optimization_config
from reliability import resolve_config


BASE_FILES = ("case.yaml", "decision.yaml", "test_cases.jsonl")
RISK_VALUES = {"low", "medium", "high", "critical"}
SPLIT_VALUES = {"development", "regression", "holdout", "challenge"}
STATUS_VALUES = {"proposed", "accepted", "retired"}


def _result_path(directory: Path, stage: str) -> Path | None:
    for name in (f"{stage}.evaluated.jsonl", f"{stage}.jsonl"):
        path = directory / name
        if path.exists() and path.stat().st_size:
            return path
    return None


def validate(case_dir: str | Path) -> tuple[list[str], list[str]]:
    directory = Path(case_dir)
    errors: list[str] = []
    warnings: list[str] = []
    if not directory.is_dir():
        return [f"Case directory not found: {directory}"], warnings
    for filename in BASE_FILES:
        if not (directory / filename).is_file():
            errors.append(f"Missing required file: {filename}")
    if errors:
        return errors, warnings

    is_v2 = (directory / "dataset.json").exists() or (directory / "evaluation.json").exists()
    if is_v2:
        for filename in ("dataset.json", "evaluation.json"):
            if not (directory / filename).is_file():
                errors.append(f"Dataset v2 requires {filename}")
        if errors:
            return errors, warnings

    try:
        cases = read_jsonl(directory / "test_cases.jsonl", "id")
        baseline_path = _result_path(directory, "baseline")
        candidate_path = _result_path(directory, "candidate")
        baseline = read_jsonl(baseline_path, "case_id") if baseline_path else []
        candidate = read_jsonl(candidate_path, "case_id") if candidate_path else []
        manifest = read_json(directory / "dataset.json") if is_v2 else {}
        suite = read_json(directory / "evaluation.json") if is_v2 else {}
        if is_v2:
            _validate_suite(suite)
    except DataError as exc:
        return [str(exc)], warnings

    if is_v2 and isinstance(suite.get("reliability"), dict):
        try:
            resolve_config(suite["reliability"])
        except DataError as exc:
            errors.append(f"evaluation.json 'reliability' section is invalid: {exc}")

    if is_v2 and "objectives" in suite:
        try:
            validate_objectives_config(suite["objectives"])
        except DataError as exc:
            errors.append(f"evaluation.json 'objectives' section is invalid: {exc}")

    if is_v2 and "judge" in suite:
        try:
            validate_judge_config(
                suite["judge"],
                {str(item["id"]) for item in suite.get("evaluators", []) if isinstance(item, dict) and item.get("id")},
            )
        except DataError as exc:
            errors.append(f"evaluation.json 'judge' section is invalid: {exc}")

    if is_v2 and "optimization" in suite:
        try:
            validate_optimization_config(suite["optimization"])
        except DataError as exc:
            errors.append(f"evaluation.json 'optimization' section is invalid: {exc}")

    if not cases:
        errors.append("test_cases.jsonl has no test cases")
    if not baseline:
        errors.append("No non-empty baseline.evaluated.jsonl or legacy baseline.jsonl")
    if not candidate:
        errors.append("No non-empty candidate.evaluated.jsonl or legacy candidate.jsonl")

    if is_v2:
        for field in ("format_version", "dataset_id", "version", "cases"):
            if not manifest.get(field):
                errors.append(f"dataset.json is missing '{field}'")
        if manifest.get("cases") != "test_cases.jsonl":
            errors.append("dataset.json 'cases' must be test_cases.jsonl")
        declared = manifest.get("fingerprint")
        actual = fingerprint(cases)
        if declared and declared != actual:
            errors.append("dataset.json fingerprint does not match test_cases.jsonl")

    seen_inputs: dict[str, str] = {}
    case_ids = {case["id"] for case in cases}
    for case in cases:
        case_id = case["id"]
        for field in ("description", "input", "expected"):
            if field not in case:
                errors.append(f"Test case {case_id} is missing '{field}'")
        if "tags" not in case:
            warnings.append(f"Test case {case_id} has no tags")
        if not isinstance(case.get("expected"), dict) or not case.get("expected"):
            warnings.append(f"Test case {case_id} has empty expectations")
        canonical_input = json.dumps(case.get("input"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical_input in seen_inputs:
            warnings.append(f"Test cases {seen_inputs[canonical_input]} and {case_id} have duplicate inputs")
        seen_inputs[canonical_input] = case_id
        if is_v2:
            for field in ("tags", "risk", "source", "split", "status"):
                if field not in case:
                    errors.append(f"Dataset v2 case {case_id} is missing '{field}'")
            if case.get("risk") not in RISK_VALUES:
                errors.append(f"Dataset v2 case {case_id} has invalid risk")
            if case.get("split") not in SPLIT_VALUES:
                errors.append(f"Dataset v2 case {case_id} has invalid split")
            if case.get("status") not in STATUS_VALUES:
                errors.append(f"Dataset v2 case {case_id} has invalid status")
            if not isinstance(case.get("source"), str) or not case.get("source"):
                errors.append(f"Dataset v2 case {case_id} requires a non-empty source")

    for label, results in (("baseline", baseline), ("candidate", candidate)):
        for result in results:
            if result.get("passed") is not None and not isinstance(result.get("passed"), bool):
                errors.append(f"{label} result {result['case_id']} has invalid 'passed'")
            if not is_v2 and not isinstance(result.get("passed"), bool):
                errors.append(f"Legacy {label} result {result['case_id']} requires boolean 'passed'")
            if "output" not in result:
                warnings.append(f"{label} result {result['case_id']} has no output")

    baseline_ids = {item["case_id"] for item in baseline}
    candidate_ids = {item["case_id"] for item in candidate}
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
