#!/usr/bin/env python3
"""Measure how much a domain judge agrees with the experts it is meant to replace.

Cohen's kappa corrects raw agreement for the agreement two raters would reach by
chance alone. Raw agreement flatters any judge on a skewed dataset: if 90% of
dossiers are approvals, a judge that approves everything agrees 90% of the time
and has learned nothing.

Two numbers matter and only one of them is usually reported:

- how well the judge agrees with the experts;
- how well the experts agree with **each other**.

The second is the ceiling. A judge cannot be more right than the humans are
consistent, so a kappa is only interpretable next to the human band. When experts
disagree on a case, that is a signal the policy is ambiguous — not proof the judge
is wrong.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from common import DataError, write_json
from judge import VERDICTS, judge


DEFAULT_SEED = 1337
BOOTSTRAP_RESAMPLES = 2000
SMALL_SAMPLE = 20
ABSTENTION = "insufficient_evidence"


def cohen_kappa(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Cohen's kappa for one pair of raters over paired categorical labels."""
    if not pairs:
        return {"kappa": None, "observed_agreement": None, "n": 0, "undefined_reason": "No paired decisions"}

    total = len(pairs)
    observed = sum(left == right for left, right in pairs) / total
    left_counts = Counter(left for left, _right in pairs)
    right_counts = Counter(right for _left, right in pairs)
    expected = sum(
        (left_counts[label] / total) * (right_counts[label] / total)
        for label in set(left_counts) | set(right_counts)
    )
    if abs(1.0 - expected) < 1e-12:
        # Both raters used a single identical category. Agreement is total and
        # chance agreement is also total, so kappa carries no information.
        return {
            "kappa": None,
            "observed_agreement": round(observed, 6),
            "n": total,
            "undefined_reason": "Chance agreement is 1.0: every decision used the same category",
        }
    return {
        "kappa": round((observed - expected) / (1.0 - expected), 6),
        "observed_agreement": round(observed, 6),
        "expected_agreement": round(expected, 6),
        "n": total,
    }


def bootstrap_kappa(
    pairs: list[tuple[str, str]], *, seed: int = DEFAULT_SEED, resamples: int = BOOTSTRAP_RESAMPLES
) -> dict[str, Any] | None:
    """Deterministic seeded percentile interval for kappa, resampling cases."""
    if len(pairs) < 2:
        return None
    rng = random.Random(seed)
    values: list[float] = []
    undefined = 0
    for _ in range(resamples):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        result = cohen_kappa(sample)
        if result["kappa"] is None:
            undefined += 1
            continue
        values.append(result["kappa"])
    if not values:
        return None
    values.sort()
    low = values[int(0.025 * (len(values) - 1))]
    high = values[int(0.975 * (len(values) - 1))]
    return {
        "ci_low": round(low, 6),
        "ci_high": round(high, 6),
        "resamples": resamples,
        "undefined_resamples": undefined,
        "seed": seed,
    }


def _measure(pairs: list[tuple[str, str]], *, seed: int) -> dict[str, Any]:
    result = cohen_kappa(pairs)
    result["bootstrap"] = bootstrap_kappa(pairs, seed=seed)
    if 0 < result["n"] < SMALL_SAMPLE:
        result["warning"] = f"{result['n']} paired decisions is a small sample; read the interval, not the point"
    return result


def read_expert_decisions(path: str | Path) -> list[dict[str, Any]]:
    """Read expert decisions, allowing several experts per case."""
    file_path = Path(path)
    if not file_path.exists():
        raise DataError(f"File not found: {file_path}")
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    with file_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataError(f"Invalid JSON in {file_path}:{line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise DataError(f"Expected an object in {file_path}:{line_number}")
            for field in ("case_id", "expert", "verdict"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    raise DataError(f"Missing non-empty '{field}' in {file_path}:{line_number}")
            if record["verdict"] not in VERDICTS:
                raise DataError(
                    f"Unknown verdict {record['verdict']!r} in {file_path}:{line_number}; "
                    f"expected one of {', '.join(VERDICTS)}"
                )
            key = (record["case_id"], record["expert"])
            if key in seen:
                raise DataError(f"Expert '{record['expert']}' decided '{record['case_id']}' twice")
            seen.add(key)
            records.append(record)
    if not records:
        raise DataError(f"{file_path} has no expert decisions")
    return records


def _by_expert(decisions: Iterable[dict[str, Any]]) -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for decision in decisions:
        table.setdefault(decision["expert"], {})[decision["case_id"]] = decision["verdict"]
    return table


def measure_agreement(
    case_dir: str | Path,
    *,
    stage: str = "candidate",
    decisions_path: str | Path | None = None,
    seed: int = DEFAULT_SEED,
    output: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(case_dir)
    if not directory.is_dir():
        raise DataError(f"Case directory not found: {directory}")

    # Judge fresh rather than trusting a judgements.jsonl that may predate the
    # current evidence or judge configuration.
    judged = judge(directory, stage=stage)
    verdicts = {item["case_id"]: item for item in judged["judgements"]}

    decisions = read_expert_decisions(decisions_path or directory / "expert_decisions.jsonl")
    unknown_cases = sorted({item["case_id"] for item in decisions} - set(verdicts))
    if unknown_cases:
        raise DataError(f"Expert decisions reference unknown case(s): {', '.join(unknown_cases)}")

    experts = _by_expert(decisions)
    expert_names = sorted(experts)

    # Consensus is unanimity. A contested case is evidence about the policy, not
    # about the judge, so it is reported rather than averaged away.
    consensus: dict[str, str] = {}
    contested: list[dict[str, Any]] = []
    for case_id in sorted({item["case_id"] for item in decisions}):
        given = {name: table[case_id] for name, table in experts.items() if case_id in table}
        distinct = set(given.values())
        if len(distinct) == 1:
            consensus[case_id] = distinct.pop()
        else:
            contested.append({"case_id": case_id, "expert_verdicts": given})

    abstained = sorted(
        case_id for case_id in consensus if verdicts[case_id]["verdict"] == ABSTENTION
    )
    scored_cases = [case_id for case_id in consensus if case_id not in set(abstained)]
    consensus_pairs = [(verdicts[case_id]["verdict"], consensus[case_id]) for case_id in scored_cases]

    per_expert = {}
    for name in expert_names:
        pairs = [
            (verdicts[case_id]["verdict"], verdict)
            for case_id, verdict in sorted(experts[name].items())
            if verdicts[case_id]["verdict"] != ABSTENTION
        ]
        per_expert[name] = _measure(pairs, seed=seed)

    expert_pairs: dict[str, Any] = {}
    for left, right in combinations(expert_names, 2):
        shared = sorted(set(experts[left]) & set(experts[right]))
        expert_pairs[f"{left} vs {right}"] = _measure(
            [(experts[left][case_id], experts[right][case_id]) for case_id in shared], seed=seed
        )

    ceiling_values = [item["kappa"] for item in expert_pairs.values() if item["kappa"] is not None]
    judge_kappa = _measure(consensus_pairs, seed=seed)

    interpretation: list[str] = []
    if not ceiling_values:
        interpretation.append(
            "Only one expert rated these cases, so there is no human ceiling to compare against. "
            "A second independent rater on a subsample is what makes the judge's kappa interpretable."
        )
    elif judge_kappa["kappa"] is not None:
        lowest = min(ceiling_values)
        if judge_kappa["kappa"] >= lowest:
            interpretation.append(
                f"The judge (kappa {judge_kappa['kappa']:g}) is inside the human band "
                f"(experts agree with each other at {lowest:g} or better). Further calibration is "
                "likely to be fitting noise; the ambiguity is in the policy."
            )
        else:
            interpretation.append(
                f"The judge (kappa {judge_kappa['kappa']:g}) is below the weakest expert pair "
                f"({lowest:g}). There is room to calibrate."
            )
    if abstained:
        share = len(scored_cases) / len(consensus) if consensus else 0.0
        interpretation.append(
            f"The judge abstained on {len(abstained)} of {len(consensus)} uncontested case(s). "
            f"Kappa covers the remaining {share:.0%}: a judge that abstains often can score well on "
            "what is left."
        )
    if contested:
        interpretation.append(
            f"{len(contested)} case(s) are contested between experts and are excluded from the "
            "judge's kappa. Contested cases mark an ambiguous policy worth resolving first."
        )

    matrix: dict[str, dict[str, int]] = {}
    for case_id in scored_cases:
        row = matrix.setdefault(consensus[case_id], {})
        row[verdicts[case_id]["verdict"]] = row.get(verdicts[case_id]["verdict"], 0) + 1

    disagreements = [
        {
            "case_id": case_id,
            "expert_verdict": consensus[case_id],
            "judge_verdict": verdicts[case_id]["verdict"],
            "judge_score": verdicts[case_id]["score"],
            "judge_confidence": verdicts[case_id]["confidence"],
            "failing_criteria": sorted(
                name
                for name, item in verdicts[case_id]["criteria"].items()
                if item["measured"] and item["status"] != "pass"
            ),
            "critical_violations": [item["evaluator_id"] for item in verdicts[case_id]["critical_violations"]],
        }
        for case_id in scored_cases
        if verdicts[case_id]["verdict"] != consensus[case_id]
    ]

    report = {
        "format_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_dir": str(directory),
        "stage": stage,
        "judge": judged["judge"],
        "experts": expert_names,
        "summary": {
            "cases_with_expert_decisions": len({item["case_id"] for item in decisions}),
            "uncontested_cases": len(consensus),
            "contested_cases": len(contested),
            "judge_abstained": len(abstained),
            "scored_cases": len(scored_cases),
        },
        "judge_vs_consensus": judge_kappa,
        "judge_vs_each_expert": per_expert,
        "expert_vs_expert": expert_pairs,
        "human_ceiling": {
            "lowest_pair_kappa": min(ceiling_values) if ceiling_values else None,
            "highest_pair_kappa": max(ceiling_values) if ceiling_values else None,
        },
        "interpretation": interpretation,
        "confusion_matrix": matrix,
        "contested": contested,
        "judge_abstained_cases": abstained,
        "disagreements": disagreements,
    }
    destination = Path(output) if output else directory / "agreement.json"
    write_json(destination, report)
    report["destination"] = str(destination)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure judge/expert agreement with Cohen's kappa")
    parser.add_argument("case_dir")
    parser.add_argument("--stage", default="candidate", choices=("baseline", "candidate"))
    parser.add_argument("--decisions", default=None, help="Defaults to expert_decisions.jsonl in the case")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    try:
        report = measure_agreement(
            args.case_dir, stage=args.stage, decisions_path=args.decisions, seed=args.seed, output=args.output
        )
    except DataError as exc:
        parser.error(str(exc))
    print_report(report)
    return 0


def print_report(report: dict[str, Any]) -> None:
    def line(label: str, measure: dict[str, Any]) -> str:
        if measure["kappa"] is None:
            return f"{label:<28} kappa=N/A  ({measure.get('undefined_reason', 'no data')})"
        interval = measure.get("bootstrap")
        band = f" [{interval['ci_low']:.3f}, {interval['ci_high']:.3f}]" if interval else ""
        return (
            f"{label:<28} kappa={measure['kappa']:.3f}{band}  "
            f"agreement={measure['observed_agreement']:.3f}  n={measure['n']}"
        )

    print(line("judge vs consensus", report["judge_vs_consensus"]))
    for name, measure in report["judge_vs_each_expert"].items():
        print(line(f"judge vs {name}", measure))
    for name, measure in report["expert_vs_expert"].items():
        print(line(name, measure))
    summary = report["summary"]
    print(" ".join(f"{key}={value}" for key, value in summary.items()))
    for note in report["interpretation"]:
        print(f"NOTE: {note}")
    print(f"agreement={report['destination']}")


if __name__ == "__main__":
    raise SystemExit(main())
