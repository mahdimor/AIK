#!/usr/bin/env python3
"""Aggregate quality, cost and latency objectives over one evaluated run.

Quality is AIK's existing currency: the pass rate over comparable verdicts. Cost
and latency come from evidence the runner reported, and follow the same rule as
the rest of AIK: missing evidence is never converted into a favourable number.
A run that reported no cost has `cost.per_case_usd = None` and
`cost.complete = False`. It does not have a cost of zero, and it cannot win a
comparison on price.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from common import DataError


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0 or not math.isfinite(number):
        return None
    return number


def case_cost_usd(result: dict[str, Any] | None) -> float | None:
    """Read the runner-reported cost for one case, in US dollars."""
    if not result:
        return None
    cost = result.get("cost")
    if not isinstance(cost, dict):
        return None
    return _positive_number(cost.get("total_usd"))


def case_latency_ms(result: dict[str, Any] | None) -> float | None:
    """Read the wall-clock duration AIK measured for one case."""
    if not result:
        return None
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return None
    return _positive_number(execution.get("duration_ms"))


def percentile(values: list[float], quantile: float) -> float | None:
    """Linear-interpolated percentile over sorted order statistics.

    Deterministic and dependency-free. `quantile` is a fraction in [0, 1].
    """
    if not values:
        return None
    if not 0.0 <= quantile <= 1.0:
        raise DataError("Quantile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def run_objectives(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one evaluated run into quality, cost and latency objectives."""
    records = list(results)
    verdicts = [record.get("passed") for record in records]
    comparable = [value for value in verdicts if isinstance(value, bool)]
    pass_rate = round(sum(comparable) / len(comparable), 6) if comparable else None

    costs = [case_cost_usd(record) for record in records]
    priced = [value for value in costs if value is not None]
    cost_complete = bool(records) and len(priced) == len(records)

    latencies = [case_latency_ms(record) for record in records]
    timed = [value for value in latencies if value is not None]
    latency_complete = bool(records) and len(timed) == len(records)

    return {
        "cases": len(records),
        "quality": {
            "pass_rate": pass_rate,
            "comparable_cases": len(comparable),
            "non_comparable_cases": len(records) - len(comparable),
            "complete": bool(records) and len(comparable) == len(records),
        },
        "cost": {
            # None, never 0.0, when any case is unpriced: an unmeasured run must not
            # be able to win on price.
            "per_case_usd": round(sum(priced) / len(priced), 6) if cost_complete else None,
            "total_usd": round(sum(priced), 6) if cost_complete else None,
            "priced_cases": len(priced),
            "complete": cost_complete,
        },
        "latency": {
            "mean_ms": round(sum(timed) / len(timed), 3) if latency_complete else None,
            "p95_ms": percentile(timed, 0.95) if latency_complete else None,
            "timed_cases": len(timed),
            "complete": latency_complete,
        },
    }


OBJECTIVE_GATES = {
    "minimum_pass_rate": ("quality", "pass_rate", "at_least"),
    "maximum_cost_per_case_usd": ("cost", "per_case_usd", "at_most"),
    "maximum_latency_p95_ms": ("latency", "p95_ms", "at_most"),
}


def validate_objectives_config(config: Any) -> dict[str, Any]:
    """Validate the optional `objectives` section of evaluation.json."""
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise DataError("evaluation.json 'objectives' must be an object")
    known = set(OBJECTIVE_GATES) | {"require_cost", "require_latency"}
    unknown = sorted(set(config) - known)
    if unknown:
        raise DataError(f"Unknown objectives key(s): {', '.join(unknown)}")
    for key in OBJECTIVE_GATES:
        if key in config and _positive_number(config[key]) is None:
            raise DataError(f"objectives '{key}' must be a non-negative number")
    if "minimum_pass_rate" in config and float(config["minimum_pass_rate"]) > 1:
        raise DataError("objectives 'minimum_pass_rate' must be between 0 and 1")
    for key in ("require_cost", "require_latency"):
        if key in config and not isinstance(config[key], bool):
            raise DataError(f"objectives '{key}' must be a boolean")
    return config


def check_objective_gates(metrics: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Return the absolute objective gates this run fails, as human-readable reasons."""
    failures: list[str] = []
    for key, (group, field, direction) in OBJECTIVE_GATES.items():
        if key not in config:
            continue
        limit = float(config[key])
        value = metrics[group][field]
        if value is None:
            failures.append(f"{key} is configured but {group}.{field} was not measured")
            continue
        if direction == "at_least" and value < limit:
            failures.append(f"{group}.{field} {value:g} is below the required {limit:g}")
        if direction == "at_most" and value > limit:
            failures.append(f"{group}.{field} {value:g} exceeds the allowed {limit:g}")
    if config.get("require_cost") and not metrics["cost"]["complete"]:
        failures.append("cost evidence is required but some cases are unpriced")
    if config.get("require_latency") and not metrics["latency"]["complete"]:
        failures.append("latency evidence is required but some cases are untimed")
    return failures
