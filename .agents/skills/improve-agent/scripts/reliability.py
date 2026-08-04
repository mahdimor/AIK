#!/usr/bin/env python3
"""Deterministic statistical reliability analysis for repeated AIK trials.

Pure, standard-library-only functions that answer: is an observed
baseline/candidate improvement repeatable, or could it be agent or LLM-judge
variability? Never converts a missing or errored observation into a zero
score, and never lets a statistical result override a deterministic
rejection (see `combine_recommendations`).
"""

from __future__ import annotations

import argparse
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, read_json, read_jsonl, write_json


FORMAT_VERSION = "1.0"
METHOD_VERSION = "aik-reliability-wilson-bootstrap-1.0"
DEFAULT_SEED = 1337
DEFAULT_CONFIG: dict[str, Any] = {
    "minimum_repetitions": 1,
    "confidence_level": 0.95,
    "bootstrap_samples": 5000,
    "minimum_probability_of_improvement": 0.0,
    "maximum_flaky_case_rate": 1.0,
}
_RECOMMENDATION_PRIORITY = {"rejected": 0, "manual_review": 1, "eligible": 2}


# ---------------------------------------------------------------------------
# Core statistics
# ---------------------------------------------------------------------------

def z_score_for_confidence(confidence_level: float) -> float:
    """Two-sided z critical value, e.g. ~1.96 for 95% confidence."""
    if not 0 < confidence_level < 1:
        raise DataError("confidence_level must be between 0 and 1")
    return statistics.NormalDist().inv_cdf(1 - (1 - confidence_level) / 2)


def wilson_interval(successes: int, n: int, confidence_level: float = 0.95) -> tuple[float, float] | None:
    """Wilson score confidence interval for a boolean proportion.

    Returns None when there is no boolean evidence (n == 0). Never raises on
    p == 0 or p == 1, unlike the normal approximation.
    """
    if n <= 0:
        return None
    if successes < 0 or successes > n:
        raise DataError("successes must be between 0 and n")
    z = z_score_for_confidence(confidence_level)
    z2 = z * z
    p_hat = successes / n
    denominator = 1 + z2 / n
    center = p_hat + z2 / (2 * n)
    margin = z * ((p_hat * (1 - p_hat) / n + z2 / (4 * n * n)) ** 0.5)
    lower = max(0.0, (center - margin) / denominator)
    upper = min(1.0, (center + margin) / denominator)
    return (round(lower, 6), round(upper, 6))


def mean_and_stdev(scores: list[float]) -> tuple[float | None, float | None]:
    """Mean of numeric scores, and sample standard deviation when >= 2 exist."""
    if not scores:
        return None, None
    mean = round(sum(scores) / len(scores), 6)
    stdev = round(statistics.stdev(scores), 6) if len(scores) >= 2 else None
    return mean, stdev


def bootstrap_delta(
    paired_deltas: list[float],
    *,
    samples: int,
    confidence_level: float,
    seed: int,
) -> dict[str, Any]:
    """Deterministic, seeded, paired bootstrap over per-case pass-rate deltas.

    `paired_deltas` must already be in a stable order (callers sort by case
    ID) so that identical evidence always produces identical draws and
    identical statistics, regardless of upstream iteration order.
    """
    n = len(paired_deltas)
    if n == 0:
        return {"delta_mean": None, "ci_low": None, "ci_high": None, "probability_of_improvement": None, "paired_cases": 0}

    observed_mean = sum(paired_deltas) / n
    rng = random.Random(seed)
    bootstrap_means: list[float] = []
    for _ in range(samples):
        resample_sum = 0.0
        for _ in range(n):
            resample_sum += paired_deltas[rng.randrange(n)]
        bootstrap_means.append(resample_sum / n)
    bootstrap_means.sort()

    alpha = 1 - confidence_level
    lower_index = max(0, min(samples - 1, round(alpha / 2 * (samples - 1))))
    upper_index = max(0, min(samples - 1, round((1 - alpha / 2) * (samples - 1))))
    probability_of_improvement = sum(1 for value in bootstrap_means if value > 0) / samples

    return {
        "delta_mean": round(observed_mean, 6),
        "ci_low": round(bootstrap_means[lower_index], 6),
        "ci_high": round(bootstrap_means[upper_index], 6),
        "probability_of_improvement": round(probability_of_improvement, 6),
        "paired_cases": n,
    }


# ---------------------------------------------------------------------------
# Aggregation of repeated observations
# ---------------------------------------------------------------------------

def _evaluator_key(evaluator_id: str, evaluator_version: str) -> str:
    return f"{evaluator_id}@{evaluator_version}"


def evaluator_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per (evaluator_id, evaluator_version) pass/fail/error/n-a counts and score stats.

    Two evaluators are only ever aggregated together when both the ID and
    the version match; a version change starts a brand new series.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        for evaluation in record.get("evaluations", []) or []:
            if not isinstance(evaluation, dict):
                continue
            evaluator_id = evaluation.get("evaluator_id")
            if not evaluator_id:
                continue
            evaluator_version = str(evaluation.get("evaluator_version", "1"))
            key = (str(evaluator_id), evaluator_version)
            bucket = buckets.setdefault(
                key, {"kind": evaluation.get("kind"), "pass": 0, "fail": 0, "error": 0, "not_applicable": 0, "scores": []}
            )
            status = evaluation.get("status")
            if status in {"pass", "fail", "error", "not_applicable"}:
                bucket[status] += 1
            score = evaluation.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                bucket["scores"].append(float(score))

    result: dict[str, dict[str, Any]] = {}
    for (evaluator_id, evaluator_version) in sorted(buckets):
        bucket = buckets[(evaluator_id, evaluator_version)]
        scores = bucket.pop("scores")
        mean_score, stdev = mean_and_stdev(scores)
        result[_evaluator_key(evaluator_id, evaluator_version)] = {
            **bucket,
            "evaluator_id": evaluator_id,
            "evaluator_version": evaluator_version,
            "observations_scored": len(scores),
            "mean_score": mean_score,
            "stdev": stdev,
        }
    return result


def aggregate_case(case_id: str, records: list[dict[str, Any]], *, confidence_level: float) -> dict[str, Any]:
    """Aggregate every repeated observation of one case within one stage."""
    passed = sum(1 for record in records if record.get("passed") is True)
    failed = sum(1 for record in records if record.get("passed") is False)
    manual = sum(1 for record in records if record.get("passed") is None)
    boolean_total = passed + failed
    pass_rate = round(passed / boolean_total, 6) if boolean_total else None
    interval = wilson_interval(passed, boolean_total, confidence_level) if boolean_total else None
    return {
        "case_id": case_id,
        "observations": len(records),
        "pass": passed,
        "fail": failed,
        "manual_review": manual,
        "pass_rate": pass_rate,
        "wilson_interval": list(interval) if interval else None,
        "flaky": passed > 0 and failed > 0,
        "evaluators": evaluator_stats(records),
    }


def aggregate_stage(records: list[dict[str, Any]], *, confidence_level: float = 0.95) -> dict[str, Any]:
    """Aggregate every repeated observation of a whole stage (all cases)."""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise DataError("Trial evaluated record is missing a valid case_id")
        by_case.setdefault(case_id, []).append(record)

    cases = {case_id: aggregate_case(case_id, by_case[case_id], confidence_level=confidence_level) for case_id in sorted(by_case)}

    passed = sum(1 for record in records if record.get("passed") is True)
    failed = sum(1 for record in records if record.get("passed") is False)
    manual = sum(1 for record in records if record.get("passed") is None)
    boolean_total = passed + failed
    overall_interval = wilson_interval(passed, boolean_total, confidence_level) if boolean_total else None

    return {
        "observations": len(records),
        "cases": cases,
        "overall": {
            "pass": passed,
            "fail": failed,
            "manual_review": manual,
            "pass_rate": round(passed / boolean_total, 6) if boolean_total else None,
            "wilson_interval": list(overall_interval) if overall_interval else None,
        },
        "evaluators": evaluator_stats(records),
    }


# ---------------------------------------------------------------------------
# Baseline vs candidate comparison
# ---------------------------------------------------------------------------

def paired_case_deltas(baseline_cases: dict[str, dict[str, Any]], candidate_cases: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Per-case candidate-minus-baseline pass-rate delta, for cases comparable in both stages."""
    deltas: dict[str, float] = {}
    for case_id in sorted(set(baseline_cases) & set(candidate_cases)):
        baseline_rate = baseline_cases[case_id]["pass_rate"]
        candidate_rate = candidate_cases[case_id]["pass_rate"]
        if baseline_rate is None or candidate_rate is None:
            continue
        deltas[case_id] = round(candidate_rate - baseline_rate, 6)
    return deltas


def find_incompatible_evaluators(
    baseline_evaluators: dict[str, dict[str, Any]], candidate_evaluators: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Evaluator IDs whose version differs between baseline and candidate; never aggregated together."""
    baseline_versions: dict[str, set[str]] = {}
    candidate_versions: dict[str, set[str]] = {}
    for entry in baseline_evaluators.values():
        baseline_versions.setdefault(entry["evaluator_id"], set()).add(entry["evaluator_version"])
    for entry in candidate_evaluators.values():
        candidate_versions.setdefault(entry["evaluator_id"], set()).add(entry["evaluator_version"])

    incompatible: list[dict[str, Any]] = []
    for evaluator_id in sorted(set(baseline_versions) | set(candidate_versions)):
        baseline_set = baseline_versions.get(evaluator_id, set())
        candidate_set = candidate_versions.get(evaluator_id, set())
        if baseline_set and candidate_set and baseline_set != candidate_set:
            incompatible.append(
                {
                    "evaluator_id": evaluator_id,
                    "baseline_versions": sorted(baseline_set),
                    "candidate_versions": sorted(candidate_set),
                }
            )
    return incompatible


def _majority_verdict(case_aggregate: dict[str, Any]) -> bool | None:
    if case_aggregate["pass"] > case_aggregate["fail"]:
        return True
    if case_aggregate["fail"] > case_aggregate["pass"]:
        return False
    return None


def changed_outcome_cases(baseline_cases: dict[str, dict[str, Any]], candidate_cases: dict[str, dict[str, Any]]) -> list[str]:
    """Cases whose majority pass/fail outcome flipped between baseline and candidate."""
    changed: list[str] = []
    for case_id in sorted(set(baseline_cases) & set(candidate_cases)):
        before = _majority_verdict(baseline_cases[case_id])
        after = _majority_verdict(candidate_cases[case_id])
        if before is not None and after is not None and before != after:
            changed.append(case_id)
    return changed


def flaky_case_ids(stage_cases: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(case_id for case_id, aggregate in stage_cases.items() if aggregate["flaky"])


def stage_fingerprints(records: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Dataset/evaluator fingerprint embedded by `aik evaluate` in every observation.

    Raises if a single stage's own observations disagree (that stage was
    evaluated with more than one dataset/evaluator suite, which should never
    happen). Returns (None, None) when older evidence carries no fingerprint.
    """
    dataset_fps = {
        record["evaluation_metadata"]["dataset_fingerprint"]
        for record in records
        if isinstance(record.get("evaluation_metadata"), dict) and record["evaluation_metadata"].get("dataset_fingerprint")
    }
    evaluator_fps = {
        record["evaluation_metadata"]["evaluator_fingerprint"]
        for record in records
        if isinstance(record.get("evaluation_metadata"), dict) and record["evaluation_metadata"].get("evaluator_fingerprint")
    }
    if len(dataset_fps) > 1 or len(evaluator_fps) > 1:
        raise DataError("Trial evidence contains inconsistent dataset/evaluator fingerprints within one stage")
    return next(iter(dataset_fps), None), next(iter(evaluator_fps), None)


# ---------------------------------------------------------------------------
# Dataset health (advisory only — never mutates the dataset)
# ---------------------------------------------------------------------------

def dataset_health(
    test_cases: list[dict[str, Any]],
    baseline_stage: dict[str, Any],
    candidate_stage: dict[str, Any],
    *,
    minimum_repetitions: int,
) -> dict[str, Any]:
    baseline_cases = baseline_stage["cases"]
    candidate_cases = candidate_stage["cases"]
    all_case_ids = sorted(set(baseline_cases) | set(candidate_cases))

    consistently_easy: list[str] = []
    consistently_failing: list[str] = []
    for case_id in all_case_ids:
        rates = [
            aggregate["pass_rate"]
            for aggregate in (baseline_cases.get(case_id), candidate_cases.get(case_id))
            if aggregate is not None and aggregate["pass_rate"] is not None
        ]
        if rates and all(rate == 1.0 for rate in rates):
            consistently_easy.append(case_id)
        elif rates and all(rate == 0.0 for rate in rates):
            consistently_failing.append(case_id)

    flaky = sorted(set(flaky_case_ids(baseline_cases)) | set(flaky_case_ids(candidate_cases)))

    evaluator_disagreement: list[str] = []
    for case_id in all_case_ids:
        for aggregate in (baseline_cases.get(case_id), candidate_cases.get(case_id)):
            if aggregate is None:
                continue
            if any(entry["pass"] > 0 and entry["fail"] > 0 for entry in aggregate["evaluators"].values()):
                evaluator_disagreement.append(case_id)
                break

    tags_by_case = {case.get("id"): case.get("tags", []) for case in test_cases if isinstance(case.get("tags"), list)}
    observations_by_tag: dict[str, int] = {}
    for case_id in all_case_ids:
        observations = (baseline_cases.get(case_id) or {}).get("observations", 0) + (candidate_cases.get(case_id) or {}).get(
            "observations", 0
        )
        for tag in tags_by_case.get(case_id, []):
            observations_by_tag[tag] = observations_by_tag.get(tag, 0) + observations
    low_observation_tags = sorted(
        tag for tag, total in observations_by_tag.items() if total < minimum_repetitions * 2
    )

    deltas = paired_case_deltas(baseline_cases, candidate_cases)
    distinguishing_cases = sorted(
        (case_id for case_id, delta in deltas.items() if delta != 0 and case_id not in flaky),
        key=lambda case_id: (-abs(deltas[case_id]), case_id),
    )

    return {
        "consistently_easy": sorted(consistently_easy),
        "consistently_failing": sorted(consistently_failing),
        "flaky": flaky,
        "evaluator_disagreement": sorted(set(evaluator_disagreement)),
        "low_observation_tags": low_observation_tags,
        "distinguishing_cases": distinguishing_cases,
    }


# ---------------------------------------------------------------------------
# Report assembly and statistical acceptance
# ---------------------------------------------------------------------------

def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return {**DEFAULT_CONFIG, **(config or {})}


def build_reliability(
    test_cases: list[dict[str, Any]],
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the complete, deterministic reliability.json model from evaluated trials."""
    resolved_config = resolve_config(config)
    confidence_level = float(resolved_config["confidence_level"])
    samples = int(resolved_config["bootstrap_samples"])

    baseline_stage = aggregate_stage(baseline_records, confidence_level=confidence_level)
    candidate_stage = aggregate_stage(candidate_records, confidence_level=confidence_level)

    baseline_dataset_fp, baseline_evaluator_fp = stage_fingerprints(baseline_records)
    candidate_dataset_fp, candidate_evaluator_fp = stage_fingerprints(candidate_records)
    fingerprint_warnings: list[str] = []
    if baseline_dataset_fp and candidate_dataset_fp and baseline_dataset_fp != candidate_dataset_fp:
        fingerprint_warnings.append("Baseline and candidate trials use different dataset fingerprints")
    if baseline_evaluator_fp and candidate_evaluator_fp and baseline_evaluator_fp != candidate_evaluator_fp:
        fingerprint_warnings.append("Baseline and candidate trials use different evaluator fingerprints")

    paired = paired_case_deltas(baseline_stage["cases"], candidate_stage["cases"])
    bootstrap = bootstrap_delta(list(paired.values()), samples=samples, confidence_level=confidence_level, seed=seed)

    incompatible = find_incompatible_evaluators(baseline_stage["evaluators"], candidate_stage["evaluators"])
    changed = changed_outcome_cases(baseline_stage["cases"], candidate_stage["cases"])
    flaky = sorted(set(flaky_case_ids(baseline_stage["cases"])) | set(flaky_case_ids(candidate_stage["cases"])))
    health = dataset_health(
        test_cases, baseline_stage, candidate_stage, minimum_repetitions=int(resolved_config["minimum_repetitions"])
    )

    when = now or datetime.now(timezone.utc)
    return {
        "format_version": FORMAT_VERSION,
        "generated_at": when.isoformat(),
        "method": {
            "version": METHOD_VERSION,
            "confidence_level": confidence_level,
            "bootstrap_samples": samples,
            "seed": seed,
        },
        "config": resolved_config,
        "inputs": {
            "baseline_dataset_fingerprint": baseline_dataset_fp,
            "baseline_evaluator_fingerprint": baseline_evaluator_fp,
            "candidate_dataset_fingerprint": candidate_dataset_fp,
            "candidate_evaluator_fingerprint": candidate_evaluator_fp,
            "fingerprint_warnings": fingerprint_warnings,
        },
        "stages": {"baseline": baseline_stage, "candidate": candidate_stage},
        "comparison": {
            "paired_cases": len(paired),
            "pass_rate_delta": bootstrap["delta_mean"],
            "bootstrap": {"ci_low": bootstrap["ci_low"], "ci_high": bootstrap["ci_high"]},
            "probability_of_improvement": bootstrap["probability_of_improvement"],
            "flaky_cases": flaky,
            "incompatible_evaluators": incompatible,
            "changed_outcome_cases": changed,
        },
        "dataset_health": health,
    }


def reliability_acceptance(reliability: dict[str, Any]) -> dict[str, Any]:
    """Statistical-only recommendation. Never authoritative by itself: combine with
    the deterministic recommendation via `combine_recommendations` before acting."""
    config = reliability["config"]
    comparison = reliability["comparison"]
    stages = reliability["stages"]
    minimum_repetitions = int(config.get("minimum_repetitions", 1))
    minimum_probability = float(config.get("minimum_probability_of_improvement", 0.0))
    maximum_flaky_rate = float(config.get("maximum_flaky_case_rate", 1.0))

    fingerprint_warnings = reliability.get("inputs", {}).get("fingerprint_warnings") or []
    if fingerprint_warnings:
        return {"recommendation": "manual_review", "reasons": list(fingerprint_warnings)}

    paired_cases = comparison["paired_cases"]
    if paired_cases == 0:
        return {
            "recommendation": "manual_review",
            "reasons": ["No case has comparable boolean evidence in both baseline and candidate"],
        }

    reasons: list[str] = []
    baseline_cases = stages["baseline"]["cases"]
    candidate_cases = stages["candidate"]["cases"]
    shared_cases = set(baseline_cases) & set(candidate_cases)
    under_repeated = [
        case_id
        for case_id in shared_cases
        if baseline_cases[case_id]["observations"] < minimum_repetitions
        or candidate_cases[case_id]["observations"] < minimum_repetitions
    ]
    if under_repeated:
        reasons.append(
            f"{len(under_repeated)} case(s) have fewer than the configured minimum_repetitions ({minimum_repetitions})"
        )

    incompatible = comparison["incompatible_evaluators"]
    if incompatible:
        ids = ", ".join(item["evaluator_id"] for item in incompatible)
        reasons.append(f"Evaluator version changed between baseline and candidate, cannot be compared: {ids}")

    considered = set(baseline_cases) | set(candidate_cases)
    flaky_rate = len(comparison["flaky_cases"]) / len(considered) if considered else 0.0
    if flaky_rate > maximum_flaky_rate:
        reasons.append(f"Flaky case rate {flaky_rate:.3f} exceeds the configured maximum {maximum_flaky_rate:.3f}")

    if reasons:
        return {"recommendation": "manual_review", "reasons": reasons}

    probability = comparison["probability_of_improvement"]
    if probability is None:
        return {"recommendation": "manual_review", "reasons": ["Probability of improvement could not be estimated"]}

    if probability < minimum_probability:
        ci_high = comparison["bootstrap"]["ci_high"]
        if ci_high is not None and ci_high < 0:
            return {
                "recommendation": "rejected",
                "reasons": [
                    f"Bootstrap delta confidence interval is entirely negative (upper bound {ci_high:+.3f}); "
                    "the candidate is likely worse, not better"
                ],
            }
        return {
            "recommendation": "manual_review",
            "reasons": [
                f"Probability of improvement {probability:.3f} is below the configured threshold {minimum_probability:.3f}"
            ],
        }

    return {
        "recommendation": "eligible",
        "reasons": [f"Probability of improvement {probability:.3f} meets the configured threshold {minimum_probability:.3f}"],
    }


def combine_recommendations(deterministic: str, statistical: str | None) -> str:
    """Combine a deterministic (Sprint 4) recommendation with an optional statistical
    one. A statistical result can never turn a deterministic rejection into eligible,
    and can never turn insufficient statistical evidence into eligible; the most
    conservative of the two always wins."""
    if statistical is None:
        return deterministic
    return min((deterministic, statistical), key=lambda value: _RECOMMENDATION_PRIORITY.get(value, 1))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_reliability(case_dir: str | Path, *, seed: int = DEFAULT_SEED, now: datetime | None = None) -> dict[str, Any]:
    """Read existing trial evidence and compute reliability.json. Performs no
    agent, evaluator, or judge call."""
    directory = Path(case_dir).resolve()
    test_cases = read_jsonl(directory / "test_cases.jsonl", "id")
    suite = read_json(directory / "evaluation.json") if (directory / "evaluation.json").exists() else {}
    config = suite.get("reliability") if isinstance(suite.get("reliability"), dict) else None

    baseline_path = directory / "baseline.trials.evaluated.jsonl"
    candidate_path = directory / "candidate.trials.evaluated.jsonl"
    if not baseline_path.exists() or not baseline_path.stat().st_size:
        raise DataError(f"No baseline trial evidence found: {baseline_path.name}; run 'aik evaluate --trials' first")
    if not candidate_path.exists() or not candidate_path.stat().st_size:
        raise DataError(f"No candidate trial evidence found: {candidate_path.name}; run 'aik evaluate --trials' first")

    baseline_records = read_jsonl(baseline_path, "observation_id")
    candidate_records = read_jsonl(candidate_path, "observation_id")
    return build_reliability(test_cases, baseline_records, candidate_records, config=config, seed=seed, now=now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate repeated AIK trials into a reliability report")
    parser.add_argument("case_dir")
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    try:
        reliability = run_reliability(args.case_dir, seed=args.seed)
    except DataError as exc:
        parser.error(str(exc))
    output_path = Path(args.output) if args.output else Path(args.case_dir) / "reliability.json"
    write_json(output_path, reliability)
    acceptance = reliability_acceptance(reliability)
    print(f"paired_cases={reliability['comparison']['paired_cases']}")
    print(f"probability_of_improvement={reliability['comparison']['probability_of_improvement']}")
    print(f"recommendation={acceptance['recommendation']}")
    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
