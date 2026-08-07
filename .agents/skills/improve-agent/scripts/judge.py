#!/usr/bin/env python3
"""Turn AIK evidence about one case into a domain verdict.

The rest of AIK judges a *version*: is this candidate better than the champion?
A business reviewer needs the other object — a verdict on one dossier, with the
criteria that decided it, what was violated, and what to do next.

The machinery is the same. Evaluators already produce a status, a score, a reason
and evidence per case; this module maps them onto weighted criteria and a verdict
vocabulary. There is no second evaluation engine, and no evaluator runs here.

`confidence` is coverage, not correctness: it is the share of criterion weight
that produced a definite status. A judge that could not check half of its
criteria reports 0.5, and that is all it claims.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, index_by, read_json, read_jsonl, write_json, write_jsonl


VERDICTS = ("approve", "escalate", "reject", "insufficient_evidence")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULT_ACTIONS = {
    "approve": "proceed",
    "escalate": "escalate_to_human",
    "reject": "reject_with_reason",
    "insufficient_evidence": "request_missing_evidence",
}
DEFAULT_APPROVE_AT = 0.85
DEFAULT_ESCALATE_AT = 0.5


def _fraction(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise DataError(f"judge '{label}' must be a number between 0 and 1")
    return float(value)


def validate_judge_config(config: Any, evaluator_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate the `judge` section of evaluation.json."""
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise DataError("evaluation.json 'judge' must be an object")
    unknown = sorted(
        set(config)
        - {"id", "domain", "version", "criteria", "hard_rules", "abstention", "verdicts", "recommended_actions"}
    )
    if unknown:
        raise DataError(f"Unknown judge key(s): {', '.join(unknown)}")

    if not isinstance(config.get("id"), str) or not SAFE_ID.match(config["id"]):
        raise DataError(f"judge 'id' is missing or invalid: {config.get('id')!r}")
    if not isinstance(config.get("version"), str) or not config["version"].strip():
        raise DataError("judge 'version' is required")

    criteria = config.get("criteria")
    if not isinstance(criteria, dict) or not criteria:
        raise DataError("judge 'criteria' must be a non-empty object")
    referenced: set[str] = set()
    for name, criterion in criteria.items():
        if not SAFE_ID.match(str(name)):
            raise DataError(f"Invalid judge criterion name: {name!r}")
        if not isinstance(criterion, dict):
            raise DataError(f"judge criterion '{name}' must be an object")
        evaluators = criterion.get("evaluators")
        if not isinstance(evaluators, list) or not evaluators or not all(isinstance(item, str) for item in evaluators):
            raise DataError(f"judge criterion '{name}' requires a non-empty list of evaluator ids")
        referenced.update(evaluators)
        weight = criterion.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise DataError(f"judge criterion '{name}' weight must be a positive number")

    hard_rules = config.get("hard_rules", [])
    if not isinstance(hard_rules, list) or not all(isinstance(item, str) for item in hard_rules):
        raise DataError("judge 'hard_rules' must be a list of evaluator ids")
    referenced.update(hard_rules)

    abstention = config.get("abstention", {})
    if not isinstance(abstention, dict):
        raise DataError("judge 'abstention' must be an object")
    if abstention.get("enabled") is not None and not isinstance(abstention["enabled"], bool):
        raise DataError("judge 'abstention.enabled' must be a boolean")
    if "minimum_confidence" in abstention:
        _fraction(abstention["minimum_confidence"], "abstention.minimum_confidence")

    verdicts = config.get("verdicts", {})
    if not isinstance(verdicts, dict):
        raise DataError("judge 'verdicts' must be an object")
    unknown_verdicts = sorted(set(verdicts) - {"approve_at", "escalate_at"})
    if unknown_verdicts:
        raise DataError(f"Unknown judge verdict threshold(s): {', '.join(unknown_verdicts)}")
    approve_at = _fraction(verdicts.get("approve_at", DEFAULT_APPROVE_AT), "verdicts.approve_at")
    escalate_at = _fraction(verdicts.get("escalate_at", DEFAULT_ESCALATE_AT), "verdicts.escalate_at")
    if escalate_at > approve_at:
        raise DataError("judge 'verdicts.escalate_at' cannot be above 'verdicts.approve_at'")

    actions = config.get("recommended_actions", {})
    if not isinstance(actions, dict) or not all(isinstance(value, str) and value for value in actions.values()):
        raise DataError("judge 'recommended_actions' must map a criterion name to a non-empty action")
    unknown_actions = sorted(set(actions) - set(criteria))
    if unknown_actions:
        raise DataError(f"judge 'recommended_actions' references unknown criteria: {', '.join(unknown_actions)}")

    if evaluator_ids is not None:
        missing = sorted(referenced - evaluator_ids)
        if missing:
            raise DataError(f"judge references evaluator(s) not in the suite: {', '.join(missing)}")
    return config


def _evaluation_index(result: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    evaluations = (result or {}).get("evaluations")
    if not isinstance(evaluations, list):
        return {}
    return {
        str(item["evaluator_id"]): item
        for item in evaluations
        if isinstance(item, dict) and item.get("evaluator_id")
    }


def _criterion_outcome(evaluator_ids: list[str], evaluations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    values: list[float] = []
    statuses: list[str] = []
    unmeasured: list[str] = []
    for evaluator_id in evaluator_ids:
        evaluation = evaluations.get(evaluator_id)
        if evaluation is None:
            unmeasured.append(f"{evaluator_id}: no result")
            continue
        status = evaluation.get("status")
        if status in {"error", "not_applicable"}:
            unmeasured.append(f"{evaluator_id}: {status}")
            continue
        if status not in {"pass", "fail"}:
            unmeasured.append(f"{evaluator_id}: unknown status {status!r}")
            continue
        statuses.append(status)
        score = evaluation.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            values.append(float(score))
        else:
            values.append(1.0 if status == "pass" else 0.0)

    if not statuses:
        return {"status": "unknown", "score": None, "measured": False, "unmeasured": unmeasured}
    if all(status == "pass" for status in statuses):
        status = "pass"
    elif all(status == "fail" for status in statuses):
        status = "fail"
    else:
        status = "partial"
    return {
        "status": status,
        "score": round(sum(values) / len(values), 6),
        "measured": True,
        "unmeasured": unmeasured,
    }


def judge_case(case: dict[str, Any], result: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    """Produce one domain verdict from one evaluated case."""
    evaluations = _evaluation_index(result)
    criteria_config = config["criteria"]
    total_weight = sum(float(item.get("weight", 1)) for item in criteria_config.values())

    criteria: dict[str, Any] = {}
    measured_weight = 0.0
    weighted_score = 0.0
    unmeasured: list[str] = []
    for name, criterion in criteria_config.items():
        weight = float(criterion.get("weight", 1))
        outcome = _criterion_outcome(list(criterion["evaluators"]), evaluations)
        unmeasured.extend(f"{name}/{item}" for item in outcome["unmeasured"])
        if outcome["measured"]:
            measured_weight += weight
            weighted_score += weight * outcome["score"]
        criteria[name] = {
            "status": outcome["status"],
            "score": outcome["score"],
            "weight": round(weight / total_weight, 6),
            "measured": outcome["measured"],
            "evaluators": list(criterion["evaluators"]),
        }

    score = round(weighted_score / measured_weight, 6) if measured_weight else None
    # Coverage, not a probability of being right.
    confidence = round(measured_weight / total_weight, 6) if total_weight else 0.0

    critical_violations = []
    unchecked_rules = []
    for evaluator_id in config.get("hard_rules", []):
        evaluation = evaluations.get(evaluator_id)
        if evaluation is None or evaluation.get("status") in {"error", "not_applicable"}:
            unchecked_rules.append(evaluator_id)
            continue
        if evaluation.get("status") == "fail":
            critical_violations.append(
                {"evaluator_id": evaluator_id, "reason": str(evaluation.get("reason", "")).strip()}
            )

    abstention = config.get("abstention", {})
    abstention_enabled = bool(abstention.get("enabled", False))
    minimum_confidence = float(abstention.get("minimum_confidence", 0.0))
    thresholds = config.get("verdicts", {})
    approve_at = float(thresholds.get("approve_at", DEFAULT_APPROVE_AT))
    escalate_at = float(thresholds.get("escalate_at", DEFAULT_ESCALATE_AT))

    abstained = False
    abstention_reasons: list[str] = []
    if critical_violations:
        # A hard rule is not weighed against anything: one violation decides.
        verdict = "reject"
    elif unchecked_rules:
        verdict = "insufficient_evidence"
        abstained = True
        abstention_reasons.append("Hard rule(s) could not be checked: " + ", ".join(sorted(unchecked_rules)))
    elif score is None:
        verdict = "insufficient_evidence"
        abstained = True
        abstention_reasons.append("No criterion produced a definite status")
    elif abstention_enabled and confidence < minimum_confidence:
        verdict = "insufficient_evidence"
        abstained = True
        abstention_reasons.append(f"Confidence {confidence:g} is below the required {minimum_confidence:g}")
    elif score >= approve_at:
        verdict = "approve"
    elif score >= escalate_at:
        verdict = "escalate"
    else:
        verdict = "reject"

    weakest = sorted(
        ((name, item) for name, item in criteria.items() if item["measured"] and item["status"] != "pass"),
        key=lambda pair: (pair[1]["score"], pair[0]),
    )
    actions = config.get("recommended_actions", {})
    recommended_action = DEFAULT_ACTIONS[verdict]
    for name, _item in weakest:
        if name in actions:
            recommended_action = actions[name]
            break

    citations = [
        {"evaluator_id": evaluator_id, "reason": str(item.get("reason", "")).strip(), "evidence": item.get("evidence")}
        for evaluator_id, item in sorted(evaluations.items())
        if item.get("evidence")
    ]

    metadata = (result or {}).get("evaluation_metadata") if isinstance((result or {}).get("evaluation_metadata"), dict) else {}
    return {
        "format_version": "1.0",
        "judge": {
            "id": config["id"],
            "domain": config.get("domain"),
            "version": config["version"],
        },
        "case_id": str(case["id"]),
        "risk": case.get("risk"),
        "split": case.get("split"),
        "verdict": verdict,
        "score": score,
        "confidence": confidence,
        "confidence_basis": "share of criterion weight that produced a definite status",
        "criteria": criteria,
        "critical_violations": critical_violations,
        "abstained": abstained,
        "abstention_reasons": abstention_reasons,
        "recommended_action": recommended_action,
        "evidence_citations": citations,
        "unmeasured": unmeasured,
        "provenance": {
            "stage": metadata.get("stage"),
            "dataset_fingerprint": metadata.get("dataset_fingerprint"),
            "evaluator_fingerprint": metadata.get("evaluator_fingerprint"),
        },
    }


def _result_path(directory: Path, stage: str) -> Path:
    for name in (f"{stage}.evaluated.jsonl", f"{stage}.jsonl"):
        path = directory / name
        if path.exists() and path.stat().st_size:
            return path
    raise DataError(f"No non-empty {stage}.evaluated.jsonl or legacy {stage}.jsonl")


def judge(
    case_dir: str | Path,
    *,
    case_id: str | None = None,
    stage: str = "candidate",
    output: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(case_dir)
    if not directory.is_dir():
        raise DataError(f"Case directory not found: {directory}")
    suite = read_json(directory / "evaluation.json")
    if "judge" not in suite:
        raise DataError(
            "evaluation.json has no 'judge' section: declare the criteria, weights and "
            "hard rules before asking for a domain verdict"
        )
    evaluator_ids = {
        str(item["id"]) for item in suite.get("evaluators", []) if isinstance(item, dict) and item.get("id")
    }
    config = validate_judge_config(suite["judge"], evaluator_ids)

    cases = read_jsonl(directory / "test_cases.jsonl", "id")
    results = index_by(read_jsonl(_result_path(directory, stage), "case_id"), "case_id")
    if case_id is not None:
        selected = [case for case in cases if str(case["id"]) == case_id]
        if not selected:
            raise DataError(f"Unknown case id: {case_id}")
    else:
        selected = cases

    judgements = [judge_case(case, results.get(str(case["id"])), config) for case in selected]
    generated_at = datetime.now(timezone.utc).isoformat()
    for judgement in judgements:
        judgement["generated_at"] = generated_at

    if case_id is not None:
        destination = Path(output) if output else directory / "judgement.json"
        write_json(destination, judgements[0])
        report = dict(judgements[0])
    else:
        destination = Path(output) if output else directory / "judgements.jsonl"
        write_jsonl(destination, judgements)
        report = {
            "format_version": "1.0",
            "generated_at": generated_at,
            "judge": judgements[0]["judge"] if judgements else {"id": config["id"], "version": config["version"]},
            "stage": stage,
            "summary": {verdict: sum(item["verdict"] == verdict for item in judgements) for verdict in VERDICTS},
            "judgements": judgements,
        }
    report["destination"] = str(destination)
    return report


def render_yaml(judgement: dict[str, Any]) -> str:
    """Render one judgement in the JudgeSpec shape.

    A deliberately small serializer for this fixed structure, not a general YAML
    writer: AIK has no third-party dependency and must not pretend to have one.
    """

    def scalar(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return f"{value:g}"
        text = str(value)
        return text if SAFE_ID.match(text) else '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines = [
        f"case_id: {scalar(judgement['case_id'])}",
        f"judge: {scalar(judgement['judge']['id'])}@{judgement['judge']['version']}",
        f"verdict: {scalar(judgement['verdict'])}",
        f"score: {scalar(judgement['score'])}",
        f"confidence: {scalar(judgement['confidence'])}",
        "criteria:",
    ]
    for name, item in judgement["criteria"].items():
        lines.append(f"  {name}: {item['status']}")
    if judgement["critical_violations"]:
        lines.append("critical_violations:")
        for violation in judgement["critical_violations"]:
            lines.append(f"  - {violation['evaluator_id']}: {scalar(violation['reason'])}")
    else:
        lines.append("critical_violations: []")
    if judgement["abstained"]:
        lines.append("abstention_reasons:")
        for reason in judgement["abstention_reasons"]:
            lines.append(f"  - {scalar(reason)}")
    lines.append(f"recommended_action: {scalar(judgement['recommended_action'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce a domain verdict for one case or all cases")
    parser.add_argument("case_dir")
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--stage", default="candidate", choices=("baseline", "candidate"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    try:
        report = judge(args.case_dir, case_id=args.case_id, stage=args.stage, output=args.output)
    except DataError as exc:
        parser.error(str(exc))
    if args.case_id:
        print(render_yaml(report))
    else:
        for judgement in report["judgements"]:
            score = "N/A" if judgement["score"] is None else f"{judgement['score']:.3f}"
            print(
                f"{judgement['case_id']:<28} {judgement['verdict']:<22} "
                f"score={score} confidence={judgement['confidence']:.3f} "
                f"-> {judgement['recommended_action']}"
            )
        print(" ".join(f"{key}={value}" for key, value in report["summary"].items()))
    print(f"judgement={report['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
