#!/usr/bin/env python3
"""Deterministic statistical reliability analysis for repeated AIK trials.

Pure, standard-library-only functions that answer: is an observed
baseline/candidate improvement repeatable, or could it be agent or LLM-judge
variability? Never converts a missing or errored observation into a zero
score, and never lets a statistical result override a deterministic
rejection (see `combine_recommendations`).

`P(candidate > baseline)` is a deterministic bootstrap estimate, not a
formal significance test: with few cases or few repetitions it is
informative but not proof of a real effect. See
`.agents/skills/improve-agent/references/statistical-reliability.md`.
"""

from __future__ import annotations

import argparse
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, fingerprint, hash_file, observation_id_for, read_json, read_jsonl, write_json


FORMAT_VERSION = "1.0"
METHOD_VERSION = "aik-reliability-hierarchical-bootstrap-2.0"
DEFAULT_SEED = 1337
MIN_BOOTSTRAP_SAMPLES = 100
DEFAULT_CONFIG: dict[str, Any] = {
    "minimum_repetitions": 1,
    "confidence_level": 0.95,
    "bootstrap_samples": 5000,
    "minimum_probability_of_improvement": 0.0,
    "maximum_flaky_case_rate": 1.0,
    "seed": DEFAULT_SEED,
}
_RECOMMENDATION_PRIORITY = {"rejected": 0, "manual_review": 1, "eligible": 2}
_BOOLEAN_VERDICT_STATUSES = {"pass", "fail", "error", "not_applicable"}


# ---------------------------------------------------------------------------
# Configuration and observation validation (never let bad input reach a
# calculation as a ZeroDivisionError, KeyError, or IndexError)
# ---------------------------------------------------------------------------

def _require_int(config: dict[str, Any], key: str, *, minimum: int) -> None:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError(f"reliability.{key} must be an integer, got {value!r}")
    if value < minimum:
        raise DataError(f"reliability.{key} must be >= {minimum}, got {value}")


def _require_fraction(config: dict[str, Any], key: str, *, exclusive: bool = False) -> None:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataError(f"reliability.{key} must be a number, got {value!r}")
    value = float(value)
    if exclusive:
        if not (0.0 < value < 1.0):
            raise DataError(f"reliability.{key} must be strictly between 0 and 1, got {value}")
    elif not (0.0 <= value <= 1.0):
        raise DataError(f"reliability.{key} must be between 0 and 1, got {value}")


def validate_reliability_config(config: dict[str, Any]) -> None:
    """Validate a resolved reliability config. Raises DataError with a human
    message for every out-of-range or wrong-type value; never lets an
    invalid config reach a statistical calculation."""
    if not isinstance(config, dict):
        raise DataError("reliability section must be an object")
    _require_int(config, "minimum_repetitions", minimum=1)
    _require_int(config, "bootstrap_samples", minimum=MIN_BOOTSTRAP_SAMPLES)
    _require_fraction(config, "confidence_level", exclusive=True)
    _require_fraction(config, "minimum_probability_of_improvement")
    _require_fraction(config, "maximum_flaky_case_rate")
    seed = config.get("seed", DEFAULT_SEED)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DataError(f"reliability.seed must be an integer, got {seed!r}")


def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    resolved = {**DEFAULT_CONFIG, **(config or {})}
    validate_reliability_config(resolved)
    return resolved


def _validate_observation_record(
    record: dict[str, Any],
    *,
    expected_stage: str,
    known_case_ids: set[str] | None,
    seen_repetitions: dict[str, set[int]],
) -> None:
    case_id = record.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise DataError("Trial record is missing a valid case_id")
    if known_case_ids is not None and case_id not in known_case_ids:
        raise DataError(f"Trial record references unknown case_id '{case_id}'")

    repetition = record.get("repetition")
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 0:
        raise DataError(f"Trial record for case '{case_id}' has an invalid repetition: {repetition!r}")
    if repetition in seen_repetitions.setdefault(case_id, set()):
        raise DataError(f"Duplicate repetition {repetition} for case '{case_id}'")
    seen_repetitions[case_id].add(repetition)

    observation_id = record.get("observation_id")
    expected_observation_id = observation_id_for(case_id, repetition)
    if observation_id != expected_observation_id:
        raise DataError(
            f"observation_id {observation_id!r} does not match case_id/repetition "
            f"(expected {expected_observation_id!r})"
        )

    metadata = record.get("evaluation_metadata")
    if not isinstance(metadata, dict):
        raise DataError(f"Trial record {observation_id} is missing evaluation_metadata")
    if metadata.get("stage") != expected_stage:
        raise DataError(
            f"Trial record {observation_id} has stage {metadata.get('stage')!r}, expected {expected_stage!r}"
        )
    if not metadata.get("dataset_fingerprint") or not metadata.get("evaluator_fingerprint"):
        raise DataError(f"Trial record {observation_id} is missing dataset/evaluator fingerprints")

    passed = record.get("passed")
    if passed is not None and not isinstance(passed, bool):
        raise DataError(f"Trial record {observation_id} has an invalid 'passed' value: {passed!r}")

    for evaluation in record.get("evaluations", []) or []:
        if not isinstance(evaluation, dict):
            raise DataError(f"Trial record {observation_id} has a non-object evaluation entry")
        evaluator_id = evaluation.get("evaluator_id")
        if not isinstance(evaluator_id, str) or not evaluator_id:
            raise DataError(f"Trial record {observation_id} has an evaluation without a valid evaluator_id")
        version = evaluation.get("evaluator_version", "1")
        if isinstance(version, bool) or not isinstance(version, (str, int)):
            raise DataError(f"Trial record {observation_id} has an invalid evaluator_version for '{evaluator_id}'")
        status = evaluation.get("status")
        if status is not None and status not in _BOOLEAN_VERDICT_STATUSES:
            raise DataError(f"Trial record {observation_id} has an invalid evaluation status: {status!r}")


def validate_stage_records(
    records: list[dict[str, Any]],
    *,
    stage: str,
    known_case_ids: set[str] | None = None,
) -> None:
    """Validate every observation of one stage's trial evidence.

    Called at the file-reading boundary (`run_reliability`, champion history
    recomputation) — not inside the pure aggregation functions below, which
    stay reusable with minimal synthetic fixtures in unit tests.
    """
    seen: dict[str, set[int]] = {}
    for record in records:
        _validate_observation_record(record, expected_stage=stage, known_case_ids=known_case_ids, seen_repetitions=seen)


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
    """Deterministic, seeded bootstrap over one delta value per case (legacy,
    single-level resampling). Kept for callers that only need a quick summary
    over already-averaged per-case deltas (e.g. dataset-health ranking); the
    baseline/candidate comparison itself uses `hierarchical_bootstrap_delta`,
    which preserves within-case repetition variance instead of collapsing it
    away before resampling.
    """
    n = len(paired_deltas)
    if n == 0:
        return {"delta_mean": None, "ci_low": None, "ci_high": None, "probability_of_improvement": None, "paired_cases": 0}
    if samples < 1:
        raise DataError("bootstrap samples must be >= 1")

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


def paired_observations_by_case(
    baseline_records: list[dict[str, Any]], candidate_records: list[dict[str, Any]]
) -> tuple[dict[str, list[tuple[bool, bool]]], int, int]:
    """Group boolean verdicts by (case_id, repetition) and pair baseline with
    candidate at the same repetition index.

    A pair is usable only when both sides have a valid boolean verdict.
    Returns (valid_pairs_by_case, unpaired_baseline_count, unpaired_candidate_count).
    """
    baseline_by_key: dict[tuple[str, int], bool | None] = {}
    candidate_by_key: dict[tuple[str, int], bool | None] = {}
    for record in baseline_records:
        baseline_by_key[(record["case_id"], record["repetition"])] = record.get("passed")
    for record in candidate_records:
        candidate_by_key[(record["case_id"], record["repetition"])] = record.get("passed")

    valid_pairs_by_case: dict[str, list[tuple[bool, bool]]] = {}
    unpaired_baseline = 0
    unpaired_candidate = 0
    for key in sorted(set(baseline_by_key) | set(candidate_by_key)):
        case_id, _repetition = key
        baseline_value = baseline_by_key.get(key)
        candidate_value = candidate_by_key.get(key)
        if isinstance(baseline_value, bool) and isinstance(candidate_value, bool):
            valid_pairs_by_case.setdefault(case_id, []).append((baseline_value, candidate_value))
        else:
            if key in baseline_by_key:
                unpaired_baseline += 1
            if key in candidate_by_key:
                unpaired_candidate += 1
    return valid_pairs_by_case, unpaired_baseline, unpaired_candidate


def _case_mean_pair_delta(pairs: list[tuple[bool, bool]]) -> float:
    return sum(int(candidate) - int(baseline) for baseline, candidate in pairs) / len(pairs)


def hierarchical_bootstrap_delta(
    valid_pairs_by_case: dict[str, list[tuple[bool, bool]]],
    *,
    samples: int,
    confidence_level: float,
    seed: int,
) -> dict[str, Any]:
    """Two-stage (hierarchical) paired bootstrap that preserves repetition
    variance instead of collapsing every case to a single mean before
    resampling.

    Method, one bootstrap draw:
      1. resample cases with replacement (equal weight per case, regardless
         of how many repetitions it has);
      2. for each resampled case, resample its own valid (baseline,
         candidate) repetition-pairs with replacement (equal weight per
         pair) and take the mean candidate-minus-baseline delta;
      3. the draw's statistic is the unweighted mean of those per-case
         means.

    With a single case, step 1 is degenerate (always the same case), but
    step 2 still resamples that case's repetitions, so uncertainty from a
    small repetition count is preserved rather than collapsed to a point
    estimate. `P(candidate > baseline)` is the fraction of draws whose
    statistic is strictly positive — a deterministic bootstrap estimate,
    not a formal significance test.
    """
    case_ids = sorted(valid_pairs_by_case)
    n_cases = len(case_ids)
    if n_cases == 0:
        return {
            "delta_mean": None,
            "ci_low": None,
            "ci_high": None,
            "probability_of_improvement": None,
            "paired_cases": 0,
            "paired_observations": 0,
        }
    if samples < 1:
        raise DataError("bootstrap samples must be >= 1")

    observed_case_deltas = [_case_mean_pair_delta(valid_pairs_by_case[case_id]) for case_id in case_ids]
    observed_mean = sum(observed_case_deltas) / n_cases
    total_pairs = sum(len(valid_pairs_by_case[case_id]) for case_id in case_ids)

    rng = random.Random(seed)
    bootstrap_means: list[float] = []
    for _ in range(samples):
        sample_case_deltas: list[float] = []
        for _ in range(n_cases):
            case_id = case_ids[rng.randrange(n_cases)]
            pairs = valid_pairs_by_case[case_id]
            pair_count = len(pairs)
            resampled_pairs = [pairs[rng.randrange(pair_count)] for _ in range(pair_count)]
            sample_case_deltas.append(_case_mean_pair_delta(resampled_pairs))
        bootstrap_means.append(sum(sample_case_deltas) / n_cases)
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
        "paired_cases": n_cases,
        "paired_observations": total_pairs,
    }


# ---------------------------------------------------------------------------
# Aggregation of repeated observations
# ---------------------------------------------------------------------------

def _evaluator_key(evaluator_id: str, evaluator_version: str) -> str:
    return f"{evaluator_id}@{evaluator_version}"


def _required_evaluator_ids(evaluators: list[dict[str, Any]] | None) -> frozenset[str]:
    if not evaluators:
        return frozenset()
    return frozenset(str(item["id"]) for item in evaluators if isinstance(item, dict) and item.get("required", True) and item.get("id"))


def evaluator_stats(records: list[dict[str, Any]], *, evaluators: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Per (evaluator_id, evaluator_version) pass/fail/error/n-a counts and score stats.

    Two evaluators are only ever aggregated together when both the ID and
    the version match; a version change starts a brand new series. An
    optional evaluator that errors is counted and displayed here, never
    silently folded into a zero score.
    """
    required_ids = _required_evaluator_ids(evaluators)
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
                key,
                {
                    "kind": evaluation.get("kind"),
                    "pass": 0,
                    "fail": 0,
                    "error": 0,
                    "not_applicable": 0,
                    "scores": [],
                    "required": str(evaluator_id) in required_ids,
                },
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


def _has_error(record: dict[str, Any], *, required_ids: frozenset[str] | None = None) -> bool:
    for evaluation in record.get("evaluations", []) or []:
        if not isinstance(evaluation, dict) or evaluation.get("status") != "error":
            continue
        if required_ids is None or evaluation.get("evaluator_id") in required_ids:
            return True
    return False


def _has_not_applicable(record: dict[str, Any]) -> bool:
    return any(
        isinstance(evaluation, dict) and evaluation.get("status") == "not_applicable"
        for evaluation in record.get("evaluations", []) or []
    )


def aggregate_case(
    case_id: str,
    records: list[dict[str, Any]],
    *,
    confidence_level: float,
    evaluators: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate every repeated observation of one case within one stage."""
    required_ids = _required_evaluator_ids(evaluators)
    passed = sum(1 for record in records if record.get("passed") is True)
    failed = sum(1 for record in records if record.get("passed") is False)
    manual = sum(1 for record in records if record.get("passed") is None)
    boolean_total = passed + failed
    evaluator_errors = sum(1 for record in records if _has_error(record))
    required_evaluator_errors = sum(1 for record in records if _has_error(record, required_ids=required_ids))
    not_applicable = sum(1 for record in records if _has_not_applicable(record))
    pass_rate = round(passed / boolean_total, 6) if boolean_total else None
    interval = wilson_interval(passed, boolean_total, confidence_level) if boolean_total else None
    return {
        "case_id": case_id,
        "total_executions": len(records),
        "observations": len(records),
        "boolean_observations": boolean_total,
        "pass": passed,
        "fail": failed,
        "manual_review": manual,
        "manual_review_observations": manual,
        "evaluator_error_observations": evaluator_errors,
        "required_evaluator_error_observations": required_evaluator_errors,
        "not_applicable_observations": not_applicable,
        "pass_rate": pass_rate,
        "wilson_interval": list(interval) if interval else None,
        "flaky": passed > 0 and failed > 0,
        "evaluators": evaluator_stats(records, evaluators=evaluators),
    }


def aggregate_stage(
    records: list[dict[str, Any]],
    *,
    confidence_level: float = 0.95,
    stage: str | None = None,
    known_case_ids: set[str] | None = None,
    evaluators: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate every repeated observation of a whole stage (all cases).

    Pass `stage`/`known_case_ids` to additionally validate every observation
    (see `validate_stage_records`); omitted by default so pure statistical
    unit tests can feed minimal synthetic fixtures directly.
    """
    if stage is not None:
        validate_stage_records(records, stage=stage, known_case_ids=known_case_ids)

    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise DataError("Trial evaluated record is missing a valid case_id")
        by_case.setdefault(case_id, []).append(record)

    cases = {
        case_id: aggregate_case(case_id, by_case[case_id], confidence_level=confidence_level, evaluators=evaluators)
        for case_id in sorted(by_case)
    }

    required_ids = _required_evaluator_ids(evaluators)
    passed = sum(1 for record in records if record.get("passed") is True)
    failed = sum(1 for record in records if record.get("passed") is False)
    manual = sum(1 for record in records if record.get("passed") is None)
    boolean_total = passed + failed
    evaluator_errors = sum(1 for record in records if _has_error(record))
    required_evaluator_errors = sum(1 for record in records if _has_error(record, required_ids=required_ids))
    not_applicable = sum(1 for record in records if _has_not_applicable(record))
    overall_interval = wilson_interval(passed, boolean_total, confidence_level) if boolean_total else None

    return {
        "observations": len(records),
        "total_executions": len(records),
        "boolean_observations": boolean_total,
        "cases": cases,
        "overall": {
            "pass": passed,
            "fail": failed,
            "manual_review": manual,
            "manual_review_observations": manual,
            "evaluator_error_observations": evaluator_errors,
            "required_evaluator_error_observations": required_evaluator_errors,
            "not_applicable_observations": not_applicable,
            "pass_rate": round(passed / boolean_total, 6) if boolean_total else None,
            "wilson_interval": list(overall_interval) if overall_interval else None,
        },
        "evaluators": evaluator_stats(records, evaluators=evaluators),
    }


# ---------------------------------------------------------------------------
# Baseline vs candidate comparison
# ---------------------------------------------------------------------------

def paired_case_deltas(baseline_cases: dict[str, dict[str, Any]], candidate_cases: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Per-case candidate-minus-baseline pass-rate delta, for cases comparable
    in both stages. Descriptive only (used for dataset-health ranking) — the
    statistical comparison itself uses `hierarchical_bootstrap_delta`."""
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

def build_reliability(
    test_cases: list[dict[str, Any]],
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    evaluators: list[dict[str, Any]] | None = None,
    seed: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the complete, deterministic reliability.json model from evaluated trials."""
    resolved_config = resolve_config(config)
    confidence_level = float(resolved_config["confidence_level"])
    samples = int(resolved_config["bootstrap_samples"])
    resolved_seed = int(resolved_config["seed"]) if seed is None else seed

    baseline_stage = aggregate_stage(baseline_records, confidence_level=confidence_level, evaluators=evaluators)
    candidate_stage = aggregate_stage(candidate_records, confidence_level=confidence_level, evaluators=evaluators)

    baseline_dataset_fp, baseline_evaluator_fp = stage_fingerprints(baseline_records)
    candidate_dataset_fp, candidate_evaluator_fp = stage_fingerprints(candidate_records)
    fingerprint_warnings: list[str] = []
    if baseline_dataset_fp and candidate_dataset_fp and baseline_dataset_fp != candidate_dataset_fp:
        fingerprint_warnings.append("Baseline and candidate trials use different dataset fingerprints")
    if baseline_evaluator_fp and candidate_evaluator_fp and baseline_evaluator_fp != candidate_evaluator_fp:
        fingerprint_warnings.append("Baseline and candidate trials use different evaluator fingerprints")

    valid_pairs_by_case, unpaired_baseline, unpaired_candidate = paired_observations_by_case(baseline_records, candidate_records)
    bootstrap = hierarchical_bootstrap_delta(valid_pairs_by_case, samples=samples, confidence_level=confidence_level, seed=resolved_seed)
    valid_boolean_pairs_by_case = {case_id: len(pairs) for case_id, pairs in sorted(valid_pairs_by_case.items())}

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
            "seed": resolved_seed,
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
            "paired_cases": bootstrap["paired_cases"],
            "paired_observations": bootstrap.get("paired_observations", 0),
            "unpaired_baseline_observations": unpaired_baseline,
            "unpaired_candidate_observations": unpaired_candidate,
            "valid_boolean_pairs_by_case": valid_boolean_pairs_by_case,
            "pass_rate_delta": bootstrap["delta_mean"],
            "bootstrap": {"ci_low": bootstrap["ci_low"], "ci_high": bootstrap["ci_high"]},
            "probability_of_improvement": bootstrap["probability_of_improvement"],
            "flaky_cases": flaky,
            "incompatible_evaluators": incompatible,
            "changed_outcome_cases": changed,
        },
        "dataset_health": health,
    }


def reliability_config_fingerprint(config: dict[str, Any] | None) -> str:
    """Fingerprint of the resolved reliability config, so a changed threshold
    (or a missing/added one) always produces a different fingerprint."""
    return fingerprint(resolve_config(config))


def attach_trial_hashes(reliability: dict[str, Any], baseline_path: str | Path, candidate_path: str | Path) -> dict[str, Any]:
    """Bind a reliability report to the exact trial file bytes that produced
    it, and to the reliability config that was in effect — so promotion can
    later detect a report copied from elsewhere or computed with different
    settings."""
    updated = dict(reliability)
    updated["inputs"] = {
        **reliability.get("inputs", {}),
        "baseline_trials_hash": hash_file(baseline_path),
        "candidate_trials_hash": hash_file(candidate_path),
    }
    updated["config_fingerprint"] = reliability_config_fingerprint(reliability.get("config"))
    return updated


def reliability_reports_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Compare two reliability reports ignoring only `generated_at`, the one
    explicitly non-deterministic field. Everything else must match exactly
    when the same trial evidence and config produced both reports."""

    def _strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: _strip(item) for key, item in value.items() if key != "generated_at"}
        if isinstance(value, list):
            return [_strip(item) for item in value]
        return value

    return _strip(a) == _strip(b)


def reliability_acceptance(reliability: dict[str, Any], *, reject_on_evaluator_error: bool = True) -> dict[str, Any]:
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

    baseline_cases = stages["baseline"]["cases"]
    candidate_cases = stages["candidate"]["cases"]

    reasons: list[str] = []

    if reject_on_evaluator_error:
        required_errors = stages["baseline"]["overall"].get("required_evaluator_error_observations", 0) + stages["candidate"][
            "overall"
        ].get("required_evaluator_error_observations", 0)
        if required_errors:
            reasons.append(
                f"{required_errors} repeated observation(s) had a required evaluator error; "
                "reject_on_evaluator_error is configured"
            )

    paired_cases = comparison["paired_cases"]
    if paired_cases == 0:
        return {
            "recommendation": "manual_review",
            "reasons": reasons + ["No case has a comparable boolean-verdict pair in both baseline and candidate"],
        }

    shared_cases = set(baseline_cases) & set(candidate_cases)
    under_repeated = sorted(
        case_id
        for case_id in shared_cases
        if baseline_cases[case_id]["boolean_observations"] < minimum_repetitions
        or candidate_cases[case_id]["boolean_observations"] < minimum_repetitions
        or comparison.get("valid_boolean_pairs_by_case", {}).get(case_id, 0) < minimum_repetitions
    )
    if under_repeated:
        reasons.append(
            f"{len(under_repeated)} case(s) have fewer than the configured minimum_repetitions "
            f"({minimum_repetitions}) valid boolean observations or pairs: {', '.join(under_repeated)}"
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

def run_reliability(case_dir: str | Path, *, seed: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Read existing trial evidence and compute reliability.json. Performs no
    agent, evaluator, or judge call."""
    directory = Path(case_dir).resolve()
    test_cases = read_jsonl(directory / "test_cases.jsonl", "id")
    known_case_ids = {case["id"] for case in test_cases}
    suite = read_json(directory / "evaluation.json") if (directory / "evaluation.json").exists() else {}
    config = suite.get("reliability") if isinstance(suite.get("reliability"), dict) else None
    if config is not None:
        validate_reliability_config(resolve_config(config))
    evaluators = suite.get("evaluators") if isinstance(suite.get("evaluators"), list) else None

    baseline_path = directory / "baseline.trials.evaluated.jsonl"
    candidate_path = directory / "candidate.trials.evaluated.jsonl"
    if not baseline_path.exists() or not baseline_path.stat().st_size:
        raise DataError(f"No baseline trial evidence found: {baseline_path.name}; run 'aik evaluate --trials' first")
    if not candidate_path.exists() or not candidate_path.stat().st_size:
        raise DataError(f"No candidate trial evidence found: {candidate_path.name}; run 'aik evaluate --trials' first")

    baseline_records = read_jsonl(baseline_path, "observation_id")
    candidate_records = read_jsonl(candidate_path, "observation_id")
    validate_stage_records(baseline_records, stage="baseline", known_case_ids=known_case_ids)
    validate_stage_records(candidate_records, stage="candidate", known_case_ids=known_case_ids)

    reliability = build_reliability(
        test_cases, baseline_records, candidate_records, config=config, evaluators=evaluators, seed=seed, now=now
    )
    return attach_trial_hashes(reliability, baseline_path, candidate_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate repeated AIK trials into a reliability report")
    parser.add_argument("case_dir")
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    try:
        reliability = run_reliability(args.case_dir, seed=args.seed)
    except DataError as exc:
        parser.error(str(exc))
    output_path = Path(args.output) if args.output else Path(args.case_dir) / "reliability.json"
    write_json(output_path, reliability)
    acceptance = reliability_acceptance(reliability)
    print(f"paired_cases={reliability['comparison']['paired_cases']}")
    print(f"paired_observations={reliability['comparison']['paired_observations']}")
    print(f"probability_of_improvement={reliability['comparison']['probability_of_improvement']}")
    print(f"recommendation={acceptance['recommendation']}")
    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
