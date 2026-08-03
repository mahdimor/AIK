#!/usr/bin/env python3
"""Initialize a portable AIK improvement case."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path


def normalize_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not name:
        raise ValueError("Case name must contain at least one letter or number")
    return name


def initialize(case_name: str, root: str | Path = "improvements") -> Path:
    normalized = normalize_name(case_name)
    case_dir = Path(root).resolve() / normalized
    if case_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing case: {case_dir}")
    case_dir.mkdir(parents=True)

    case_yaml = (
        f"name: {normalized}\n"
        f"created: {date.today().isoformat()}\n"
        "status: draft\n"
        "problem: TODO\n"
        "severity: medium\n"
        "hypothesis: TODO\n"
        "change: TODO\n"
        "risks:\n"
        "  - TODO\n"
    )
    decision_yaml = (
        "status: pending\n"
        "reviewer: null\n"
        "date: null\n"
        "justification: null\n"
        "promoted_case_ids: []\n"
    )
    (case_dir / "case.yaml").write_text(case_yaml, encoding="utf-8", newline="\n")
    (case_dir / "decision.yaml").write_text(decision_yaml, encoding="utf-8", newline="\n")
    dataset = {
        "format_version": "2.0",
        "dataset_id": normalized,
        "version": "1.0.0",
        "cases": "test_cases.jsonl",
    }
    evaluation = {
        "format_version": "1.0",
        "suite_id": f"{normalized}-evaluation",
        "version": "1.0.0",
        "evaluators": [
            {"id": "execution-ok", "type": "no_execution_error", "required": True},
            {"id": "legacy-runner-verdict", "type": "runner_verdict", "required": False},
        ],
        "acceptance": {
            "reject_on_evaluator_error": True,
            "no_regression_at_or_above_risk": "high",
            "critical_cases_must_pass": True,
            "maximum_regressions": 0,
            "minimum_candidate_pass_rate": 0.0,
            "minimum_delta": 0.0,
        },
    }
    (case_dir / "dataset.json").write_text(json.dumps(dataset, indent=2) + "\n", encoding="utf-8", newline="\n")
    (case_dir / "evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n", encoding="utf-8", newline="\n")
    for filename in ("test_cases.jsonl", "baseline.raw.jsonl", "baseline.evaluated.jsonl", "candidate.raw.jsonl", "candidate.evaluated.jsonl"):
        (case_dir / filename).touch()
    return case_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize an AIK improvement case")
    parser.add_argument("name", help="Case name, normalized to lower-case hyphen format")
    parser.add_argument("--root", default="improvements", help="Improvement cases directory")
    args = parser.parse_args()
    try:
        case_dir = initialize(args.name, args.root)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(case_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
