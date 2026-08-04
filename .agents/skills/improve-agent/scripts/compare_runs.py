#!/usr/bin/env python3
"""Compare baseline and candidate AI-agent run artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, fingerprint, index_by, read_jsonl, write_json
from reliability import combine_recommendations, reliability_acceptance


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


RISK_LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _evaluation_errors(result: dict[str, Any] | None) -> list[str]:
    if not result or not isinstance(result.get("evaluations"), list):
        return []
    return [str(item.get("evaluator_id", "unknown")) for item in result["evaluations"] if isinstance(item, dict) and item.get("status") == "error"]


def _acceptance_policy(
    items: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    reliability: dict[str, Any] | None = None,
    *,
    reliability_required: bool = False,
) -> dict[str, Any]:
    reasons: list[str] = []
    manual_reasons: list[str] = []
    if policy.get("reject_on_evaluator_error", True):
        for item in items:
            errors = _evaluation_errors(item.get("candidate"))
            if errors:
                manual_reasons.append(f"{item['case_id']}: evaluator error ({', '.join(errors)})")

    protected_level = RISK_LEVELS.get(str(policy.get("no_regression_at_or_above_risk", "high")), 2)
    regressions = sum(item["status"] == "regressed" for item in items)
    maximum_regressions = int(policy.get("maximum_regressions", 0))
    if regressions > maximum_regressions:
        reasons.append(f"Detected {regressions} regressions; maximum is {maximum_regressions}")
    for item in items:
        case = cases.get(item["case_id"], {})
        risk = str(case.get("risk", "medium"))
        candidate_result = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        if item["status"] == "regressed" and RISK_LEVELS.get(risk, 1) >= protected_level:
            reasons.append(f"Protected {risk}-risk regression: {item['case_id']}")
        if policy.get("critical_cases_must_pass", True) and risk == "critical" and candidate_result.get("passed") is not True:
            reasons.append(f"Critical case does not pass: {item['case_id']}")

    candidate_values = [(item.get("candidate") or {}).get("passed") for item in items]
    candidate_boolean = [value for value in candidate_values if isinstance(value, bool)]
    baseline_boolean = [(item.get("baseline") or {}).get("passed") for item in items if isinstance((item.get("baseline") or {}).get("passed"), bool)]
    candidate_rate = sum(candidate_boolean) / len(candidate_boolean) if candidate_boolean else None
    baseline_rate = sum(baseline_boolean) / len(baseline_boolean) if baseline_boolean else None
    delta = candidate_rate - baseline_rate if candidate_rate is not None and baseline_rate is not None else None
    minimum_rate = float(policy.get("minimum_candidate_pass_rate", 0.0))
    minimum_delta = float(policy.get("minimum_delta", 0.0))
    if candidate_rate is None:
        manual_reasons.append("No comparable candidate verdicts")
    elif candidate_rate < minimum_rate:
        reasons.append(f"Candidate pass rate {candidate_rate:.3f} is below {minimum_rate:.3f}")
    if delta is None:
        manual_reasons.append("Pass-rate delta cannot be calculated")
    elif delta < minimum_delta:
        reasons.append(f"Pass-rate delta {delta:+.3f} is below {minimum_delta:+.3f}")

    if reasons:
        recommendation = "rejected"
    elif manual_reasons:
        recommendation = "manual_review"
    else:
        recommendation = "eligible"
    result = {
        "recommendation": recommendation,
        "reasons": reasons or manual_reasons or ["All configured acceptance gates passed"],
        "metrics": {"baseline_pass_rate": baseline_rate, "candidate_pass_rate": candidate_rate, "delta": delta},
        "policy": policy,
    }

    # A statistical result never overrides this deterministic policy: it can only
    # confirm it or make it more conservative (see reliability.combine_recommendations).
    # Without a `reliability` section in evaluation.json (reliability_required is
    # False and no report is supplied), the statistical layer is not consulted at
    # all — Sprint 4/5 behavior is unchanged.
    reject_on_evaluator_error = bool(policy.get("reject_on_evaluator_error", True))
    if reliability is not None:
        statistical = reliability_acceptance(reliability, reject_on_evaluator_error=reject_on_evaluator_error)
        combined = combine_recommendations(recommendation, statistical["recommendation"])
        result["deterministic_recommendation"] = recommendation
        result["recommendation"] = combined
        result["reasons"] = list(result["reasons"]) + list(statistical["reasons"])
        result["statistical_reliability"] = {
            "recommendation": statistical["recommendation"],
            "reasons": statistical["reasons"],
            "paired_cases": reliability["comparison"]["paired_cases"],
            "paired_observations": reliability["comparison"].get("paired_observations"),
            "probability_of_improvement": reliability["comparison"]["probability_of_improvement"],
            "pass_rate_delta": reliability["comparison"]["pass_rate_delta"],
            "bootstrap_ci": [reliability["comparison"]["bootstrap"]["ci_low"], reliability["comparison"]["bootstrap"]["ci_high"]],
            "flaky_cases": reliability["comparison"]["flaky_cases"],
        }
    elif reliability_required:
        # A `reliability` section is configured but no valid, fresh report is
        # available for this candidate: never fall back to the deterministic
        # decision alone, and never fabricate statistics — force manual_review.
        combined = combine_recommendations(recommendation, "manual_review")
        result["deterministic_recommendation"] = recommendation
        result["recommendation"] = combined
        result["reasons"] = list(result["reasons"]) + [
            "A reliability section is configured but no valid, matching reliability.json is available for this candidate"
        ]
        result["statistical_reliability"] = {
            "recommendation": "manual_review",
            "reasons": ["reliability.json is missing or does not match the configured reliability section"],
            "paired_cases": None,
            "paired_observations": None,
            "probability_of_improvement": None,
            "pass_rate_delta": None,
            "bootstrap_ci": [None, None],
            "flaky_cases": [],
        }
    return result


def compare(
    test_cases_path: str | Path,
    baseline_path: str | Path,
    candidate_path: str | Path,
    suite: dict[str, Any] | None = None,
    reliability: dict[str, Any] | None = None,
    *,
    reliability_required: bool = False,
) -> dict[str, Any]:
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
                "risk": (case or {}).get("risk", "medium"),
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
    dataset_fingerprints = {
        result.get("evaluation_metadata", {}).get("dataset_fingerprint")
        for result in [*baseline.values(), *candidate.values()]
        if isinstance(result.get("evaluation_metadata"), dict) and result.get("evaluation_metadata", {}).get("dataset_fingerprint")
    }
    evaluator_fingerprints = {
        result.get("evaluation_metadata", {}).get("evaluator_fingerprint")
        for result in [*baseline.values(), *candidate.values()]
        if isinstance(result.get("evaluation_metadata"), dict) and result.get("evaluation_metadata", {}).get("evaluator_fingerprint")
    }
    compatibility_warnings: list[str] = []
    if len(dataset_fingerprints) > 1:
        compatibility_warnings.append("Baseline and candidate use different dataset fingerprints")
    if len(evaluator_fingerprints) > 1:
        compatibility_warnings.append("Baseline and candidate use different evaluator fingerprints")
    acceptance = _acceptance_policy(
        items, cases, (suite or {}).get("acceptance", {}), reliability, reliability_required=reliability_required
    )
    if compatibility_warnings:
        acceptance = {**acceptance, "recommendation": "manual_review", "reasons": compatibility_warnings}
    return {
        "format_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "test_cases": str(Path(test_cases_path)),
            "baseline": str(Path(baseline_path)),
            "candidate": str(Path(candidate_path)),
        },
        "summary": summary,
        "dataset_fingerprint": next(iter(dataset_fingerprints), fingerprint(list(cases.values()))),
        "evaluator_fingerprint": next(iter(evaluator_fingerprints), None),
        "compatibility_warnings": compatibility_warnings,
        "acceptance": acceptance,
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
