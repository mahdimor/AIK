#!/usr/bin/env python3
"""Build a constrained quality/cost/latency Pareto front over evaluated variants.

An unconstrained front happily surfaces a variant that is very cheap and fails a
critical case. That is the failure this module exists to prevent: feasibility is
decided first, by the same deterministic acceptance policy the review already
uses, and only feasible variants are ranked against each other.

A variant is on the front when no other feasible variant is at least as good on
every objective and strictly better on one.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from common import DataError, read_json, read_jsonl, write_json
from compare_runs import compare
from objectives import check_objective_gates, run_objectives, validate_objectives_config


# (metrics group, field, direction)
FRONT_OBJECTIVES = (
    ("quality", "pass_rate", "maximize"),
    ("cost", "per_case_usd", "minimize"),
    ("latency", "p95_ms", "minimize"),
)

BASELINE_NAME = "baseline"


def _objective_values(metrics: dict[str, Any]) -> list[float] | None:
    values: list[float] = []
    for group, field, _direction in FRONT_OBJECTIVES:
        value = metrics[group][field]
        if value is None:
            return None
        values.append(float(value))
    return values


def dominates(left: list[float], right: list[float]) -> bool:
    """True when `left` is at least as good everywhere and strictly better once."""
    at_least_as_good = True
    strictly_better = False
    for index, (_group, _field, direction) in enumerate(FRONT_OBJECTIVES):
        a, b = left[index], right[index]
        if direction == "maximize":
            better, worse = a > b, a < b
        else:
            better, worse = a < b, a > b
        if worse:
            at_least_as_good = False
            break
        if better:
            strictly_better = True
    return at_least_as_good and strictly_better


def parse_variant(argument: str) -> tuple[str, str]:
    name, separator, path = argument.partition("=")
    if not separator or not name.strip() or not path.strip():
        raise DataError(f"Variant must be NAME=PATH, received: {argument}")
    return name.strip(), path.strip()


def _dataset_fingerprint(results: list[dict[str, Any]]) -> str | None:
    seen = {
        record["evaluation_metadata"]["dataset_fingerprint"]
        for record in results
        if isinstance(record.get("evaluation_metadata"), dict)
        and record["evaluation_metadata"].get("dataset_fingerprint")
    }
    if len(seen) > 1:
        raise DataError("One variant mixes results from different dataset fingerprints")
    return next(iter(seen), None)


def build_pareto(
    case_dir: str | Path,
    variants: Sequence[tuple[str, str | Path]],
    *,
    baseline: str | Path | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(case_dir)
    if not directory.is_dir():
        raise DataError(f"Case directory not found: {directory}")
    if not variants:
        raise DataError("At least one --variant NAME=PATH is required")
    names = [name for name, _path in variants]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise DataError(f"Duplicate variant name(s): {', '.join(duplicates)}")

    test_cases_path = directory / "test_cases.jsonl"
    suite = read_json(directory / "evaluation.json") if (directory / "evaluation.json").exists() else {}
    objectives_config = validate_objectives_config(suite.get("objectives"))

    baseline_path = Path(baseline) if baseline else directory / "baseline.evaluated.jsonl"
    warnings: list[str] = []
    has_baseline = baseline_path.exists() and baseline_path.stat().st_size > 0
    if not has_baseline:
        warnings.append(
            f"No baseline at {baseline_path}: feasibility uses the absolute objective gates only, "
            "so regressions against a reference run are not checked"
        )

    entries: list[dict[str, Any]] = []
    fingerprints: dict[str, str | None] = {}
    for name, path in variants:
        variant_path = Path(path)
        if not variant_path.exists():
            raise DataError(f"Variant '{name}' not found: {variant_path}")
        results = read_jsonl(variant_path, "case_id")
        metrics = run_objectives(results)
        fingerprints[name] = _dataset_fingerprint(results)

        blocking: list[str] = []
        review: list[str] = []
        acceptance: dict[str, Any] | None = None
        if has_baseline:
            comparison = compare(test_cases_path, baseline_path, variant_path, suite)
            acceptance = comparison["acceptance"]
            if acceptance["recommendation"] == "rejected":
                blocking.extend(acceptance["reasons"])
            elif acceptance["recommendation"] == "manual_review":
                review.extend(acceptance["reasons"])
        blocking.extend(check_objective_gates(metrics, objectives_config))

        entries.append(
            {
                "name": name,
                "results": str(variant_path),
                "metrics": metrics,
                "acceptance": None
                if acceptance is None
                else {"recommendation": acceptance["recommendation"], "reasons": acceptance["reasons"]},
                "blocking_reasons": blocking,
                "review_reasons": review,
            }
        )

    reference = next((value for value in fingerprints.values() if value), None)
    for entry in entries:
        actual = fingerprints[entry["name"]]
        if reference and actual and actual != reference:
            entry["blocking_reasons"].append(
                "Evaluated on a different dataset fingerprint than the other variants"
            )

    # Feasibility first, ranking second. A rejected or unreviewed variant never
    # reaches the front, however cheap it is.
    for entry in entries:
        if entry["blocking_reasons"]:
            entry["status"] = "rejected"
        elif entry["review_reasons"]:
            entry["status"] = "manual_review"
        else:
            entry["status"] = "feasible"
        entry["objective_values"] = _objective_values(entry["metrics"])
        if entry["status"] == "feasible" and entry["objective_values"] is None:
            entry["status"] = "not_comparable"
            missing = [
                f"{group}.{field}"
                for group, field, _direction in FRONT_OBJECTIVES
                if entry["metrics"][group][field] is None
            ]
            entry["review_reasons"].append(
                "Not ranked: " + ", ".join(missing) + " was not measured for every case"
            )

    rankable = [entry for entry in entries if entry["status"] == "feasible"]
    for entry in rankable:
        entry["dominated_by"] = sorted(
            other["name"]
            for other in rankable
            if other is not entry and dominates(other["objective_values"], entry["objective_values"])
        )
        entry["status"] = "dominated" if entry["dominated_by"] else "on_front"

    front = sorted(
        (entry["name"] for entry in entries if entry["status"] == "on_front"),
        key=lambda name: next(
            (entry["metrics"]["cost"]["per_case_usd"] for entry in entries if entry["name"] == name), 0.0
        ),
    )
    cheapest = front[0] if front else None

    for entry in entries:
        entry.pop("objective_values", None)

    report = {
        "format_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_dir": str(directory),
        "baseline": str(baseline_path) if has_baseline else None,
        "objectives": [
            {"metric": f"{group}.{field}", "direction": direction} for group, field, direction in FRONT_OBJECTIVES
        ],
        "objective_gates": objectives_config,
        "summary": {
            "variants": len(entries),
            "on_front": len(front),
            "dominated": sum(entry["status"] == "dominated" for entry in entries),
            "rejected": sum(entry["status"] == "rejected" for entry in entries),
            "manual_review": sum(entry["status"] == "manual_review" for entry in entries),
            "not_comparable": sum(entry["status"] == "not_comparable" for entry in entries),
        },
        "front": front,
        # The cheapest variant that survived every gate. Still a recommendation for a
        # human, never an automatic migration decision.
        "cheapest_feasible": cheapest,
        "warnings": warnings,
        "variants": entries,
    }

    destination = Path(output) if output else directory / "pareto.json"
    write_json(destination, report)
    report["destination"] = str(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a constrained quality/cost/latency Pareto front over evaluated variants"
    )
    parser.add_argument("case_dir")
    parser.add_argument("--variant", action="append", dest="variants", default=[], metavar="NAME=PATH")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    try:
        variants = [parse_variant(item) for item in args.variants]
        report = build_pareto(args.case_dir, variants, baseline=args.baseline, output=args.output)
    except DataError as exc:
        parser.error(str(exc))
    for entry in report["variants"]:
        metrics = entry["metrics"]
        print(
            f"{entry['name']:<20} {entry['status']:<15} "
            f"pass_rate={metrics['quality']['pass_rate']} "
            f"cost_per_case={metrics['cost']['per_case_usd']} "
            f"p95_ms={metrics['latency']['p95_ms']}"
        )
    print(f"front={','.join(report['front']) or 'none'}")
    print(f"cheapest_feasible={report['cheapest_feasible']}")
    print(f"pareto={report['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
