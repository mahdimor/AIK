#!/usr/bin/env python3
"""Compute the canonical AIK dashboard model from champion history.

Pure model layer: reads immutable history artifacts and produces the
`dashboard.json` shape. Never recomputes business metrics in the renderer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, read_json, read_jsonl
from history import (
    ACCEPTED_DECISIONS,  # noqa: F401  (re-exported for callers/tests)
    _find_valid_cohort_anchor,
    history_validate,
    list_run_ids,
    load_events,
    load_manifest,
    load_state,
)


FORMAT_VERSION = "1.0"
RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_dir(history_root: str | Path, run_id: str) -> Path:
    return Path(history_root).resolve() / "runs" / run_id


def _portable_history_root(history_root: str | Path) -> str:
    """Describe the history location without leaking a developer's absolute path."""
    root = Path(history_root)
    if not root.is_absolute():
        return root.as_posix()
    try:
        return root.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return root.name or "aik-history"


def _optional_json(path: Path) -> dict[str, Any] | None:
    return read_json(path) if path.exists() else None


def _optional_jsonl(path: Path, id_field: str) -> list[dict[str, Any]] | None:
    return read_jsonl(path, id_field) if path.exists() and path.stat().st_size else None


def _level_denominator(manifest: dict[str, Any] | None) -> int | None:
    if not manifest:
        return None
    return manifest.get("passed", 0) + manifest.get("failed", 0)


def _latest_unpromoted_candidate(
    manifests: dict[str, dict[str, Any]], events: list[dict[str, Any]], champion_run_id: str | None
) -> str | None:
    promoted = {event.get("run_id") for event in events if event.get("type") == "champion_promoted"}
    candidates = [
        run_id
        for run_id, manifest in manifests.items()
        if manifest.get("role") == "candidate"
        and run_id not in promoted
        and run_id != champion_run_id
        and manifest.get("parent_champion_run_id") == champion_run_id
    ]
    return max(candidates, key=lambda run_id: (str(manifests[run_id].get("recorded_at", "")), run_id)) if candidates else None


def _comparability(
    history_root: str | Path,
    champion_run_id: str | None,
    champion_manifest: dict[str, Any] | None,
    candidate_run_id: str | None,
    candidate_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not champion_manifest:
        return {"comparable": False, "cohort_id": None, "cohort_anchor_run_id": None, "reasons": ["No champion has been established yet"]}
    if not candidate_manifest:
        return {
            "comparable": False,
            "cohort_id": champion_manifest.get("cohort_id"),
            "cohort_anchor_run_id": None,
            "reasons": ["No candidate is selected"],
        }
    if candidate_manifest.get("parent_champion_run_id") != champion_run_id:
        return {
            "comparable": False,
            "cohort_id": candidate_manifest.get("cohort_id"),
            "cohort_anchor_run_id": None,
            "reasons": ["Selected candidate was recorded against a different champion and is stale"],
        }
    if candidate_manifest.get("cohort_id") == champion_manifest.get("cohort_id"):
        return {"comparable": True, "cohort_id": candidate_manifest.get("cohort_id"), "cohort_anchor_run_id": None, "reasons": []}
    cohort_anchor = _find_valid_cohort_anchor(history_root, champion_run_id, champion_manifest, candidate_manifest.get("cohort_id"))
    if cohort_anchor:
        return {
            "comparable": True,
            "cohort_id": candidate_manifest.get("cohort_id"),
            "cohort_anchor_run_id": cohort_anchor.get("run_id"),
            "reasons": [],
        }
    return {
        "comparable": False,
        "cohort_id": candidate_manifest.get("cohort_id"),
        "cohort_anchor_run_id": None,
        "reasons": [
            "Candidate dataset/evaluator fingerprints differ from the champion cohort; rerun the current "
            "champion on this dataset/evaluator suite and record it with role cohort_anchor before comparing"
        ],
    }


def _ols_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return numerator / denominator if denominator else 0.0


def _champion_timeline_run_ids(manifests: dict[str, dict[str, Any]], events: list[dict[str, Any]], anchor_run_id: str | None) -> list[str]:
    ordered: list[str] = []
    if anchor_run_id and anchor_run_id in manifests:
        ordered.append(anchor_run_id)
    for event in events:
        if event.get("type") == "champion_promoted" and event.get("run_id") in manifests:
            ordered.append(event["run_id"])
    return ordered


def _trend_and_slope(
    manifests: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
    anchor_run_id: str | None,
    champion_run_id: str | None,
    candidate_run_id: str | None,
    candidate_manifest: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], float | None, str | None]:
    timeline_ids = _champion_timeline_run_ids(manifests, events, anchor_run_id)
    trend: list[dict[str, Any]] = []
    cohort_positions: dict[str, int] = {}
    for run_id in timeline_ids:
        manifest = manifests[run_id]
        cohort = manifest.get("cohort_id")
        position = cohort_positions.get(cohort, 0)
        cohort_positions[cohort] = position + 1
        trend.append(
            {
                "run_id": run_id,
                "order": position,
                "cohort_id": cohort,
                "recorded_at": manifest.get("recorded_at"),
                "agent_version": manifest.get("agent_version"),
                "pass_rate": manifest.get("pass_rate"),
                "kind": "anchor" if run_id == anchor_run_id else "champion",
            }
        )

    if candidate_run_id and candidate_manifest and candidate_run_id not in timeline_ids:
        cohort = candidate_manifest.get("cohort_id")
        trend.append(
            {
                "run_id": candidate_run_id,
                "order": cohort_positions.get(cohort, 0),
                "cohort_id": cohort,
                "recorded_at": candidate_manifest.get("recorded_at"),
                "agent_version": candidate_manifest.get("agent_version"),
                "pass_rate": candidate_manifest.get("pass_rate"),
                "kind": "candidate",
            }
        )

    current_cohort = (manifests.get(champion_run_id) or {}).get("cohort_id") if champion_run_id else None
    cohort_points = [
        point
        for point in trend
        if point["cohort_id"] == current_cohort and point["kind"] in {"anchor", "champion"} and point["pass_rate"] is not None
    ]
    if len(cohort_points) < 2:
        return trend, None, "Fewer than two accepted champion points exist in the current cohort"
    xs = list(range(len(cohort_points)))
    ys = [point["pass_rate"] * 100 for point in cohort_points]
    return trend, round(_ols_slope(xs, ys), 6), None


def _regressions(history_root: str | Path, candidate_run_id: str | None, comparable: bool) -> dict[str, Any]:
    empty = {"summary": {}, "by_risk": {}, "by_tag": {}, "items": []}
    if not candidate_run_id or not comparable:
        return empty
    comparison = _optional_json(_run_dir(history_root, candidate_run_id) / "comparison.json")
    if not comparison:
        return empty
    summary = comparison.get("summary", {})
    regressed_items = [item for item in comparison.get("items", []) if item.get("status") == "regressed"]
    by_risk: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    detail: list[dict[str, Any]] = []
    for item in regressed_items:
        risk = str(item.get("risk", "medium"))
        by_risk[risk] = by_risk.get(risk, 0) + 1
        for tag in item.get("tags", []):
            by_tag[str(tag)] = by_tag.get(str(tag), 0) + 1
        candidate_result = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        failing_evaluators = [
            evaluation.get("evaluator_id")
            for evaluation in candidate_result.get("evaluations", [])
            if isinstance(evaluation, dict) and evaluation.get("status") == "fail"
        ]
        detail.append(
            {
                "case_id": item.get("case_id"),
                "description": item.get("description"),
                "risk": risk,
                "tags": item.get("tags", []),
                "failing_evaluators": failing_evaluators,
                "high_or_critical": risk in {"high", "critical"},
                "review_link": f"runs/{candidate_run_id}/review.html",
            }
        )
    detail.sort(key=lambda entry: (RISK_ORDER.get(entry["risk"], 4), str(entry["case_id"])))
    return {
        "summary": {status: summary.get(status, 0) for status in ("improved", "regressed", "unchanged", "not_comparable", "total")},
        "by_risk": by_risk,
        "by_tag": by_tag,
        "items": detail,
    }


def _evaluator_summary(records: list[dict[str, Any]] | None) -> dict[tuple[str, str], dict[str, Any]]:
    summary: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records or []:
        for evaluation in record.get("evaluations", []):
            if not isinstance(evaluation, dict):
                continue
            evaluator_id = evaluation.get("evaluator_id")
            if not evaluator_id:
                continue
            evaluator_version = str(evaluation.get("evaluator_version", "1"))
            key = (str(evaluator_id), evaluator_version)
            entry = summary.setdefault(key, {"kind": evaluation.get("kind"), "pass": 0, "fail": 0, "error": 0, "not_applicable": 0, "scores": []})
            status = evaluation.get("status")
            if status in {"pass", "fail", "error", "not_applicable"}:
                entry[status] += 1
            score = evaluation.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["scores"].append(float(score))
    return summary


def _evaluators_panel(
    history_root: str | Path,
    reference_run_id: str | None,
    candidate_run_id: str | None,
    comparable: bool,
) -> list[dict[str, Any]]:
    champion_records = _optional_jsonl(_run_dir(history_root, reference_run_id) / "evaluated.jsonl", "case_id") if reference_run_id else None
    candidate_records = _optional_jsonl(_run_dir(history_root, candidate_run_id) / "evaluated.jsonl", "case_id") if candidate_run_id else None
    champion_summary = _evaluator_summary(champion_records)
    candidate_summary = _evaluator_summary(candidate_records)
    rows: list[dict[str, Any]] = []
    for evaluator_id, evaluator_version in sorted(set(champion_summary) | set(candidate_summary)):
        key = (evaluator_id, evaluator_version)
        champ = champion_summary.get(key, {})
        cand = candidate_summary.get(key, {})
        champ_scores = champ.get("scores", [])
        cand_scores = cand.get("scores", [])
        champ_mean = round(sum(champ_scores) / len(champ_scores), 6) if champ_scores else None
        cand_mean = round(sum(cand_scores) / len(cand_scores), 6) if cand_scores else None
        delta = round(cand_mean - champ_mean, 6) if comparable and champ_mean is not None and cand_mean is not None else None
        rows.append(
            {
                "evaluator_id": evaluator_id,
                "evaluator_version": evaluator_version,
                "kind": cand.get("kind") or champ.get("kind"),
                "champion_score": champ_mean,
                "candidate_score": cand_mean,
                "delta": delta,
                "pass": cand.get("pass", 0),
                "fail": cand.get("fail", 0),
                "error": cand.get("error", 0),
                "not_applicable": cand.get("not_applicable", 0),
                "cases_scored": len(cand_scores),
            }
        )
    return rows


def _coverage_warnings(by_split: dict[str, int], by_risk: dict[str, int], untagged: int, duplicates: list[list[str]]) -> list[str]:
    warnings: list[str] = []
    if not by_split.get("regression"):
        warnings.append("No regression-split cases")
    if not by_split.get("holdout"):
        warnings.append("No holdout-split cases")
    if not (by_risk.get("high", 0) + by_risk.get("critical", 0)):
        warnings.append("No high/critical-risk cases")
    if untagged:
        warnings.append(f"{untagged} untagged case(s)")
    if duplicates:
        warnings.append(f"{len(duplicates)} exact duplicate input group(s)")
    return warnings


def _coverage(history_root: str | Path, run_id: str | None) -> dict[str, Any]:
    if not run_id:
        return {"available": False, "reason": "No run selected", "total_cases": 0}
    cases = _optional_jsonl(_run_dir(history_root, run_id) / "test_cases.jsonl", "id") or []
    accepted = [case for case in cases if case.get("status") == "accepted"]
    by_split: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    untagged = 0
    high_critical = 0
    seen_inputs: dict[str, list[str]] = {}
    for case in accepted:
        split = str(case.get("split", "unknown"))
        risk = str(case.get("risk", "unknown"))
        source = str(case.get("source", "unknown"))
        by_split[split] = by_split.get(split, 0) + 1
        by_risk[risk] = by_risk.get(risk, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        tags = case.get("tags") if isinstance(case.get("tags"), list) else []
        if not tags:
            untagged += 1
        for tag in tags:
            by_tag[str(tag)] = by_tag.get(str(tag), 0) + 1
        if risk in {"high", "critical"}:
            high_critical += 1
        canonical = json.dumps(case.get("input"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        seen_inputs.setdefault(canonical, []).append(str(case.get("id")))
    duplicates = [ids for ids in seen_inputs.values() if len(ids) > 1]
    return {
        "available": True,
        "total_cases": len(accepted),
        "by_split": by_split,
        "by_risk": by_risk,
        "by_source": by_source,
        "by_tag": by_tag,
        "untagged": untagged,
        "high_or_critical": high_critical,
        "duplicate_inputs": duplicates,
        "warnings": _coverage_warnings(by_split, by_risk, untagged, duplicates),
    }


def _reliability_block(history_root: str | Path, candidate_run_id: str | None) -> dict[str, Any]:
    """Statistical reliability for the selected candidate, if `aik reliability` was
    run and archived before this candidate was recorded. Never fabricates a
    confidence interval or probability when the evidence does not support one."""
    if not candidate_run_id:
        return {"available": False, "reason": "No candidate is selected"}
    reliability = _optional_json(_run_dir(history_root, candidate_run_id) / "reliability.json")
    if not reliability:
        return {"available": False, "reason": "No repeated-trial reliability report was archived for this candidate"}

    risk_map: dict[str, str] = {}
    cases = _optional_jsonl(_run_dir(history_root, candidate_run_id) / "test_cases.jsonl", "id") or []
    for case in cases:
        risk_map[str(case.get("id"))] = str(case.get("risk", "medium"))

    comparison = reliability.get("comparison", {})
    flaky = sorted(
        comparison.get("flaky_cases", []),
        key=lambda case_id: (RISK_ORDER.get(risk_map.get(case_id, "medium"), 2), case_id),
    )
    flaky_with_risk = [{"case_id": case_id, "risk": risk_map.get(case_id, "medium")} for case_id in flaky]

    unstable_evaluators: list[dict[str, Any]] = []
    for stage_name in ("baseline", "candidate"):
        for key, entry in reliability.get("stages", {}).get(stage_name, {}).get("evaluators", {}).items():
            if entry.get("pass", 0) > 0 and entry.get("fail", 0) > 0:
                unstable_evaluators.append({"stage": stage_name, "evaluator": key, "pass": entry["pass"], "fail": entry["fail"]})

    return {
        "available": True,
        "method": reliability.get("method", {}),
        "config": reliability.get("config", {}),
        "stages": {
            "baseline": reliability.get("stages", {}).get("baseline", {}).get("overall", {}),
            "candidate": reliability.get("stages", {}).get("candidate", {}).get("overall", {}),
        },
        "comparison": comparison,
        "flaky_cases": flaky_with_risk,
        "unstable_evaluators": unstable_evaluators,
        "dataset_health": reliability.get("dataset_health", {}),
    }


def _timeline(
    history_root: str | Path,
    manifests: dict[str, dict[str, Any]],
    run_ids: list[str],
    events: list[dict[str, Any]],
    champion_run_id: str | None,
) -> list[dict[str, Any]]:
    promoted_run_ids = {event.get("run_id") for event in events if event.get("type") == "champion_promoted"}
    rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        manifest = manifests.get(run_id)
        if not manifest:
            continue
        run_dir = _run_dir(history_root, run_id)
        cohort_id = manifest.get("cohort_id") or ""
        rows.append(
            {
                "run_id": run_id,
                "recorded_at": manifest.get("recorded_at"),
                "label": manifest.get("label"),
                "agent_version": manifest.get("agent_version"),
                "stage": manifest.get("stage"),
                "role": manifest.get("role"),
                "cohort_prefix": cohort_id.split(":", 1)[-1][:8],
                "pass_rate": manifest.get("pass_rate"),
                "acceptance_recommendation": manifest.get("acceptance_recommendation"),
                "human_decision": manifest.get("human_decision"),
                "promoted": run_id in promoted_run_ids,
                "is_current_champion": run_id == champion_run_id,
                "comparison_link": f"runs/{run_id}/comparison.json" if (run_dir / "comparison.json").exists() else None,
                "review_link": f"runs/{run_id}/review.html" if (run_dir / "review.html").exists() else None,
            }
        )
    return rows


def build_dashboard(
    history_root: str | Path = "aik-history",
    *,
    candidate_run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    when = now or _utc_now()
    errors, warnings = history_validate(history_root)
    integrity = {"valid": not errors, "errors": errors}
    dashboard_warnings: list[str] = list(warnings)

    state = load_state(history_root) or {}
    anchor_run_id = state.get("anchor_run_id")
    champion_run_id = state.get("champion_run_id")

    run_ids = list_run_ids(history_root)
    manifests: dict[str, dict[str, Any]] = {}
    for run_id in run_ids:
        try:
            manifests[run_id] = load_manifest(history_root, run_id)
        except DataError:
            continue

    events = load_events(history_root)
    if candidate_run_id is not None and candidate_run_id not in manifests:
        dashboard_warnings.append(f"Selected candidate run {candidate_run_id} was not found; ignoring the selection")
        candidate_run_id = None
    if candidate_run_id is None:
        candidate_run_id = _latest_unpromoted_candidate(manifests, events, champion_run_id)

    champion_manifest = manifests.get(champion_run_id) if champion_run_id else None
    anchor_manifest = manifests.get(anchor_run_id) if anchor_run_id else None
    candidate_manifest = manifests.get(candidate_run_id) if candidate_run_id else None

    comparability = _comparability(history_root, champion_run_id, champion_manifest, candidate_run_id, candidate_manifest)

    trend, slope, slope_reason = _trend_and_slope(manifests, events, anchor_run_id, champion_run_id, candidate_run_id, candidate_manifest)

    champion_level = (champion_manifest or {}).get("pass_rate")
    candidate_level = (candidate_manifest or {}).get("pass_rate")

    delta_vs_champion = None
    delta_vs_champion_reason = None
    if not champion_manifest:
        delta_vs_champion_reason = "No champion has been established yet"
    elif not candidate_manifest:
        delta_vs_champion_reason = "No candidate is selected"
    elif not comparability["comparable"]:
        delta_vs_champion_reason = comparability["reasons"][0] if comparability["reasons"] else "Not comparable"
    else:
        comparable_champion_level = champion_level
        if comparability["cohort_anchor_run_id"]:
            comparable_champion_level = manifests[comparability["cohort_anchor_run_id"]].get("pass_rate")
        if comparable_champion_level is None or candidate_level is None:
            delta_vs_champion_reason = "The comparable run has no boolean pass rate"
        else:
            delta_vs_champion = round(candidate_level - comparable_champion_level, 6)

    reference_run_id = candidate_run_id or champion_run_id
    reference_manifest = candidate_manifest or champion_manifest
    delta_vs_anchor = None
    delta_vs_anchor_reason = None
    if not anchor_manifest:
        delta_vs_anchor_reason = "No anchor has been established yet"
    elif not reference_manifest:
        delta_vs_anchor_reason = "No run is selected to compare against the anchor"
    elif reference_manifest.get("cohort_id") != anchor_manifest.get("cohort_id"):
        delta_vs_anchor_reason = "The fixed anchor is in a different cohort; delta vs anchor is only available within a compatible cohort"
    elif reference_manifest.get("pass_rate") is None or anchor_manifest.get("pass_rate") is None:
        delta_vs_anchor_reason = "The reference run has no boolean pass rate"
    else:
        delta_vs_anchor = round(reference_manifest["pass_rate"] - anchor_manifest["pass_rate"], 6)

    regressions_block = _regressions(history_root, candidate_run_id, comparability["comparable"])
    regression_count = regressions_block["summary"].get("regressed") if regressions_block["summary"] else None
    reliability_block = _reliability_block(history_root, candidate_run_id)
    reliability_comparison = reliability_block.get("comparison", {}) if reliability_block.get("available") else {}
    reliability_config = reliability_block.get("config", {}) if reliability_block.get("available") else {}

    kpis = {
        "champion_level": champion_level,
        "champion_level_numerator": (champion_manifest or {}).get("passed"),
        "champion_level_denominator": _level_denominator(champion_manifest),
        "candidate_level": candidate_level,
        "candidate_level_numerator": (candidate_manifest or {}).get("passed"),
        "candidate_level_denominator": _level_denominator(candidate_manifest),
        "delta_vs_champion": delta_vs_champion,
        "delta_vs_champion_reason": delta_vs_champion_reason,
        "delta_vs_anchor": delta_vs_anchor,
        "delta_vs_anchor_reason": delta_vs_anchor_reason,
        "slope": slope,
        "slope_reason": slope_reason,
        "candidate_recommendation": (candidate_manifest or {}).get("acceptance_recommendation"),
        "candidate_human_decision": (candidate_manifest or {}).get("human_decision"),
        "regressions": regression_count,
        "manual_review": (candidate_manifest or {}).get("manual_review"),
        "champion_manual_review": (champion_manifest or {}).get("manual_review"),
        "probability_of_improvement": reliability_comparison.get("probability_of_improvement"),
        "probability_of_improvement_threshold": reliability_config.get("minimum_probability_of_improvement"),
        "reliability_available": reliability_block.get("available", False),
    }

    coverage_run_id = candidate_run_id or champion_run_id or anchor_run_id
    coverage = _coverage(history_root, coverage_run_id)
    evaluator_reference_run_id = comparability.get("cohort_anchor_run_id") or champion_run_id
    evaluators = _evaluators_panel(
        history_root, evaluator_reference_run_id, candidate_run_id, comparability["comparable"]
    )
    timeline = _timeline(history_root, manifests, run_ids, events, champion_run_id)

    if not anchor_run_id:
        dashboard_warnings.append("No anchor has been recorded yet; run 'aik.py history init'")
    if not champion_run_id:
        dashboard_warnings.append("No champion has been established yet")
    if not run_ids:
        pass
    elif not candidate_run_id:
        dashboard_warnings.append("No candidate is selected; pass --candidate or record one with 'aik.py history record'")
    elif not comparability["comparable"]:
        dashboard_warnings.extend(comparability["reasons"])
    if reference_run_id is None:
        pass
    elif delta_vs_anchor_reason and reference_manifest and anchor_manifest:
        dashboard_warnings.append(delta_vs_anchor_reason)
    if candidate_run_id and not reliability_block.get("available"):
        dashboard_warnings.append("One run only for the selected candidate — uncertainty unavailable")

    header = {
        "generated_at": when.isoformat(),
        "history_root": _portable_history_root(history_root),
        "anchor_run_id": anchor_run_id,
        "anchor_agent_version": (anchor_manifest or {}).get("agent_version"),
        "champion_run_id": champion_run_id,
        "champion_agent_version": (champion_manifest or {}).get("agent_version"),
        "candidate_run_id": candidate_run_id,
        "candidate_agent_version": (candidate_manifest or {}).get("agent_version"),
        "current_cohort_id": (champion_manifest or {}).get("cohort_id"),
    }

    return {
        "format_version": FORMAT_VERSION,
        "generated_at": header["generated_at"],
        "header": header,
        "state": {
            "anchor_run_id": anchor_run_id,
            "champion_run_id": champion_run_id,
            "selected_candidate_run_id": candidate_run_id,
        },
        "comparability": comparability,
        "kpis": kpis,
        "trend": trend,
        "evaluators": evaluators,
        "coverage": coverage,
        "regressions": regressions_block,
        "reliability": reliability_block,
        "timeline": timeline,
        "warnings": dashboard_warnings,
        "integrity": integrity,
    }
