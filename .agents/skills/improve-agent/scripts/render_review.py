#!/usr/bin/env python3
"""Render a standalone human-readable AIK comparison report."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


STATUS_LABELS = {
    "improved": "Improved",
    "regressed": "Regressed",
    "unchanged": "Unchanged",
    "not_comparable": "Not comparable",
}

RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _fmt_ci(interval: list[float] | None) -> str:
    if not interval:
        return "N/A"
    return f"[{interval[0] * 100:.1f}%, {interval[1] * 100:.1f}%]"


def _fmt_pp(value: float | None) -> str:
    if value is None:
        return "N/A"
    scaled = value * 100
    sign = "+" if scaled >= 0 else ""
    return f"{sign}{scaled:.1f}pp"


def _reliability_panel(comparison: dict[str, Any], reliability: dict[str, Any] | None) -> str:
    """Statistical reliability panel. Never fabricates a confidence interval or
    probability when the underlying evidence does not support one."""
    if reliability is None:
        return (
            '<div class="recommend reliability"><h3>Reliability: one run only</h3>'
            "Statistical confidence is unavailable from a single observation. Run with "
            "<code>--repetitions N</code>, then <code>aik evaluate --trials</code> and "
            "<code>aik reliability</code>, to measure repeatability.</div>"
        )

    stages = reliability.get("stages", {})
    comparison_stats = reliability.get("comparison", {})
    config = reliability.get("config", {})
    method = reliability.get("method", {})

    def _stage_row(label: str, stage: dict[str, Any]) -> str:
        overall = stage.get("overall", {})
        return (
            f'<div class="meta"><strong>{html.escape(label)}</strong> &middot; n={stage.get("observations", 0)} &middot; '
            f'pass rate {_fmt_pct(overall.get("pass_rate"))} &middot; 95% CI {_fmt_ci(overall.get("wilson_interval"))}</div>'
        )

    probability = comparison_stats.get("probability_of_improvement")
    threshold = config.get("minimum_probability_of_improvement")
    bootstrap_ci = comparison_stats.get("bootstrap", {})
    probability_text = "N/A" if probability is None else f"{probability:.3f}"
    threshold_text = "" if threshold is None else f" (configured threshold {float(threshold):.2f})"

    risk_map = {item["case_id"]: str(item.get("risk", "medium")) for item in comparison.get("items", [])}
    flaky = sorted(comparison_stats.get("flaky_cases", []), key=lambda case_id: (RISK_ORDER.get(risk_map.get(case_id, "medium"), 2), case_id))
    flaky_html = "".join(
        f'<li>{html.escape(case_id)} <span class="sub">({html.escape(risk_map.get(case_id, "medium"))} risk)</span></li>' for case_id in flaky
    ) or "<li>No flaky cases detected</li>"

    incompatible = comparison_stats.get("incompatible_evaluators", [])
    incompatible_html = "".join(
        f'<li>{html.escape(str(item.get("evaluator_id")))}: baseline v{html.escape(",".join(item.get("baseline_versions", [])))} '
        f'vs candidate v{html.escape(",".join(item.get("candidate_versions", [])))} — not aggregated together</li>'
        for item in incompatible
    ) or "<li>None</li>"

    unstable: list[str] = []
    for stage_name in ("baseline", "candidate"):
        for key, entry in stages.get(stage_name, {}).get("evaluators", {}).items():
            if entry.get("pass", 0) > 0 and entry.get("fail", 0) > 0:
                unstable.append(f"{stage_name}: {key} ({entry['pass']} pass / {entry['fail']} fail across repetitions)")
    unstable_html = "".join(f"<li>{html.escape(item)}</li>" for item in unstable) or "<li>None detected</li>"

    statistical = (comparison.get("acceptance") or {}).get("statistical_reliability") or {}
    reasons_text = "; ".join(str(reason) for reason in statistical.get("reasons", [])) or "No statistical reasons recorded"

    return f"""<div class="recommend reliability"><h3>Reliability</h3>
<div class="meta">Method {html.escape(str(method.get('version', 'N/A')))} &middot; confidence {html.escape(str(method.get('confidence_level', 'N/A')))} &middot;
{html.escape(str(method.get('bootstrap_samples', 'N/A')))} bootstrap samples &middot; seed {html.escape(str(method.get('seed', 'N/A')))}</div>
{_stage_row('Baseline', stages.get('baseline', {}))}
{_stage_row('Candidate', stages.get('candidate', {}))}
<div class="meta"><strong>Delta</strong> &middot; {_fmt_pp(comparison_stats.get('pass_rate_delta'))} &middot;
bootstrap CI [{_fmt_pp(bootstrap_ci.get('ci_low'))}, {_fmt_pp(bootstrap_ci.get('ci_high'))}] &middot;
P(candidate&gt;baseline) {html.escape(probability_text)}{html.escape(threshold_text)}</div>
<div class="meta"><strong>Statistical recommendation</strong>: {html.escape(str(statistical.get('recommendation', 'N/A')))} — {html.escape(reasons_text)}</div>
<div class="meta"><strong>Flaky cases</strong><ul>{flaky_html}</ul></div>
<div class="meta"><strong>Evaluator instability</strong><ul>{unstable_html}</ul></div>
<div class="meta"><strong>Incompatible evaluator versions</strong><ul>{incompatible_html}</ul></div>
</div>"""


def _simple_yaml_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _tools(result: dict[str, Any] | None) -> str:
    if not result:
        return "None"
    calls = result.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return "None"
    return ", ".join(str(call.get("name", "unknown")) for call in calls if isinstance(call, dict))


def _pane(title: str, result: dict[str, Any] | None) -> str:
    if result is None:
        return f'<div class="pane"><div class="pane-title">{title}</div><em>Missing result</em></div>'
    output = html.escape(str(result.get("output", "")))
    agents = html.escape(" → ".join(str(item) for item in result.get("agents", [])) or "Unknown")
    tools = html.escape(_tools(result))
    passed = "PASS" if result.get("passed") is True else ("FAIL" if result.get("passed") is False else "MANUAL REVIEW")
    details = {
        "scores": result.get("scores", {}),
        "contracts": result.get("contracts", {}),
        "execution": result.get("execution", {}),
        "error": result.get("error"),
        "evaluations": result.get("evaluations", []),
    }
    raw_details = html.escape(json.dumps(details, ensure_ascii=False, indent=2))
    return (
        f'<div class="pane"><div class="pane-title">{title} · {passed}</div>'
        f"<pre>{output}</pre><div class=\"meta\">Agents: {agents}<br>Tools: {tools}</div>"
        f"<details><summary>Scores, contracts, execution, and errors</summary><pre>{raw_details}</pre></details></div>"
    )


def _recommend(summary: dict[str, int], acceptance: dict[str, Any] | None = None) -> tuple[str, str]:
    if acceptance:
        recommendation = acceptance.get("recommendation")
        reasons = "; ".join(str(item) for item in acceptance.get("reasons", []))
        if recommendation == "rejected":
            return "REJECTED BY POLICY", reasons
        if recommendation == "manual_review":
            return "MANUAL REVIEW REQUIRED", reasons
        if recommendation == "eligible":
            return "ELIGIBLE FOR HUMAN APPROVAL", reasons
    if summary.get("regressed", 0):
        return "REJECT OR FIX", "At least one behavior regressed. Review and correct every regression before acceptance."
    if summary.get("not_comparable", 0):
        return "MORE TESTS REQUIRED", "Some cases are missing or invalid, so the candidate cannot be assessed completely."
    if summary.get("improved", 0):
        return "READY FOR HUMAN DECISION", "The candidate improved measured cases without detected pass/fail regressions. Human approval is still required."
    return "NO MEASURABLE IMPROVEMENT", "No test changed from failing to passing. Review metric deltas and the value of the change."


def render(
    comparison_path: str | Path,
    case_yaml_path: str | Path,
    output_path: str | Path,
    reliability: dict[str, Any] | None = None,
) -> Path:
    comparison = json.loads(Path(comparison_path).read_text(encoding="utf-8"))
    summary = comparison["summary"]
    case = _simple_yaml_values(Path(case_yaml_path))
    acceptance = comparison.get("acceptance")
    recommendation, reason = _recommend(summary, acceptance)
    reliability_html = _reliability_panel(comparison, reliability)
    css_path = Path(__file__).resolve().parent.parent / "assets" / "review.css"
    css = css_path.read_text(encoding="utf-8")

    order = {"regressed": 0, "improved": 1, "not_comparable": 2, "unchanged": 3}
    sections: list[str] = []
    for item in sorted(comparison["items"], key=lambda value: (order[value["status"]], value["case_id"])):
        status = item["status"]
        warnings = "".join(f'<div class="warning">{html.escape(text)}</div>' for text in item.get("warnings", []))
        deltas = item.get("score_deltas", {})
        delta_text = ", ".join(f"{key}: {value:+g}" for key, value in deltas.items()) or "No comparable numeric score delta"
        tags = ", ".join(str(tag) for tag in item.get("tags", [])) or "untagged"
        risk = str(item.get("risk", "medium"))
        sections.append(
            '<section class="case">'
            f'<div class="case-head"><span class="badge {status}">{STATUS_LABELS[status]}</span>'
            f'<div><h3>{html.escape(item["case_id"])}</h3><div class="sub">{html.escape(item["description"])}</div></div></div>'
            f'<div class="case-body"><div class="grid">{_pane("Baseline", item.get("baseline"))}{_pane("Candidate", item.get("candidate"))}</div>'
            f'<div class="meta">Risk: {html.escape(risk)} · Tags: {html.escape(tags)}<br><span class="delta">Score deltas: {html.escape(delta_text)}</span></div>{warnings}</div></section>'
        )

    cards = "".join(
        f'<div class="card"><div class="num">{summary[key]}</div><div class="label">{STATUS_LABELS.get(key, key)}</div></div>'
        for key in ("total", "improved", "regressed", "unchanged", "not_comparable")
    )
    title = case.get("name", Path(case_yaml_path).parent.name)
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIK Review · {html.escape(title)}</title><style>{css}</style></head>
<body><main class="wrap"><h1>Behavioral Change Review</h1><div class="sub">{html.escape(title)}</div>
<div class="context"><strong>Problem</strong><br>{html.escape(case.get('problem', 'Not documented'))}<br><br>
<strong>Hypothesis</strong><br>{html.escape(case.get('hypothesis', 'Not documented'))}<br><br>
<strong>Change</strong><br>{html.escape(case.get('change', 'Not documented'))}</div>
<div class="cards">{cards}</div>
<div class="recommend"><h3>{recommendation}</h3>{html.escape(reason)}</div>
{reliability_html}
<h2>Case-by-case evidence</h2>{''.join(sections)}
<p class="sub">Generated from open AIK artifacts. A human decision is required before promotion or deployment.</p>
</main></body></html>"""
    destination = Path(output_path)
    destination.write_text(document, encoding="utf-8", newline="\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an AIK human-readable review")
    parser.add_argument("comparison")
    parser.add_argument("case_yaml")
    parser.add_argument("--output", "-o", default="review.html")
    args = parser.parse_args()
    output = render(args.comparison, args.case_yaml, args.output)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
