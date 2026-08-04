#!/usr/bin/env python3
"""Render the canonical AIK dashboard model to standalone, escaped HTML."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


ASSETS = Path(__file__).resolve().parent.parent / "assets"
PALETTE = ["#175cd3", "#16803c", "#b54708", "#7c3aed", "#0891b2", "#be123c"]


def _esc(value: Any, default: str = "N/A") -> str:
    if value is None or value == "":
        return default
    return html.escape(str(value))


def _short(run_id: str | None) -> str:
    return "N/A" if not run_id else run_id[-8:]


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _pp_from_fraction(value: float | None) -> str:
    if value is None:
        return "N/A"
    scaled = value * 100
    sign = "+" if scaled >= 0 else ""
    return f"{sign}{scaled:.1f}pp"


def _slope_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}pp/champion"


def _delta_class(value: float | None) -> str:
    if value is None:
        return ""
    return "pos" if value >= 0 else "neg"


def _badge(text: str | None, css_class: str | None = None) -> str:
    if not text:
        return _esc(None)
    slug = css_class or str(text).lower().replace(" ", "_")
    return f'<span class="badge {html.escape(slug)}">{_esc(text)}</span>'


def _kpi(label: str, value_html: str, sub_html: str = "", extra_class: str = "") -> str:
    sub = f'<div class="sub">{sub_html}</div>' if sub_html else ""
    return f'<div class="kpi {extra_class}"><div class="label">{_esc(label)}</div><div class="num">{value_html}</div>{sub}</div>'


def _build_progress_svg(trend: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    points = [dict(point) for point in trend if isinstance(point.get("pass_rate"), (int, float))]
    if not points:
        return "", [], []

    ordered = sorted(points, key=lambda point: (point.get("recorded_at") or "", point.get("run_id") or ""))
    for index, point in enumerate(ordered):
        point["_x_index"] = index

    width, height = 860, 320
    margin_left, margin_right, margin_top, margin_bottom = 60, 30, 24, 46
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    n = len(ordered)

    def x_for(index: int) -> float:
        return margin_left + (plot_w * index / (n - 1) if n > 1 else plot_w / 2)

    def y_for(pass_rate: float) -> float:
        return margin_top + plot_h * (1 - pass_rate)

    cohorts: dict[str, list[dict[str, Any]]] = {}
    for point in ordered:
        if point.get("kind") in {"anchor", "champion"}:
            cohorts.setdefault(point.get("cohort_id") or "unknown", []).append(point)

    svg_parts = [
        '<svg class="trend" viewBox="0 0 {w} {h}" role="img" aria-labelledby="trend-title trend-desc" '
        'xmlns="http://www.w3.org/2000/svg">'.format(w=width, h=height),
        "<title id=\"trend-title\">Champion pass rate over time</title>",
        f'<desc id="trend-desc">Line chart of accepted champion pass rate across {len(cohorts)} cohort(s), 0 to 100 percent. '
        "A disconnected line segment marks each cohort; a diamond marks the selected candidate.</desc>",
    ]
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y_for(fraction)
        svg_parts.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="#dbe3ef" stroke-width="1"/>')
        svg_parts.append(f'<text x="{margin_left - 8}" y="{y + 4:.1f}" font-size="11" fill="#64748b" text-anchor="end">{int(fraction * 100)}%</text>')

    legend: list[dict[str, Any]] = []
    for index, (cohort_id, cohort_points) in enumerate(cohorts.items()):
        color = PALETTE[index % len(PALETTE)]
        cohort_sorted = sorted(cohort_points, key=lambda point: point["_x_index"])
        path = " ".join(f"{x_for(point['_x_index']):.1f},{y_for(point['pass_rate']):.1f}" for point in cohort_sorted)
        svg_parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for point in cohort_sorted:
            radius = 6 if point.get("kind") == "anchor" else 5
            cx, cy = x_for(point["_x_index"]), y_for(point["pass_rate"])
            svg_parts.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius}" fill="{color}" stroke="#fff" stroke-width="1.5">'
                f"<title>{_esc(point.get('run_id'))}: {point['pass_rate'] * 100:.1f}%</title></circle>"
            )
        legend.append({"cohort_id": cohort_id, "color": color, "prefix": (cohort_id.split(":", 1)[-1] if ":" in cohort_id else cohort_id)[:8]})

    for point in ordered:
        if point.get("kind") == "candidate":
            cx, cy = x_for(point["_x_index"]), y_for(point["pass_rate"])
            svg_parts.append(
                f'<rect x="{cx - 6:.1f}" y="{cy - 6:.1f}" width="12" height="12" fill="#fff" stroke="#b54708" '
                f'stroke-width="2.5" transform="rotate(45 {cx:.1f} {cy:.1f})">'
                f"<title>Candidate {_esc(point.get('run_id'))}: {point['pass_rate'] * 100:.1f}%</title></rect>"
            )

    svg_parts.append(
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{width - margin_right}" y2="{margin_top + plot_h}" '
        'stroke="#172033" stroke-width="1"/>'
    )
    svg_parts.append("</svg>")
    return "".join(svg_parts), legend, ordered


def _fallback_table(ordered_points: list[dict[str, Any]]) -> str:
    if not ordered_points:
        return '<p class="empty">No trend data is available yet.</p>'
    rows = "".join(
        "<tr>"
        f"<td>{_esc(point.get('recorded_at'))}</td>"
        f"<td>{_esc(point.get('run_id'))}</td>"
        f"<td>{_esc(point.get('kind'))}</td>"
        f"<td>{_esc((point.get('cohort_id') or '')[7:15])}</td>"
        f"<td>{_pct(point.get('pass_rate'))}</td>"
        "</tr>"
        for point in ordered_points
    )
    return (
        '<details class="fallback"><summary>Accessible data table</summary>'
        "<table><thead><tr><th>Recorded</th><th>Run</th><th>Kind</th><th>Cohort</th><th>Pass rate</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def _progress_panel(trend: list[dict[str, Any]]) -> str:
    svg, legend, ordered_points = _build_progress_svg(trend)
    if not svg:
        return '<section class="panel"><h2>Progress</h2><p class="empty">No comparable trend points are recorded yet.</p></section>'
    legend_html = "".join(
        f'<span><span class="swatch" style="background:{entry["color"]}"></span>cohort {_esc(entry["prefix"])}</span>' for entry in legend
    )
    return (
        '<section class="panel"><h2>Progress</h2>'
        f'<div class="chart-wrap">{svg}</div>'
        f'<div class="legend">{legend_html}'
        '<span><span class="swatch" style="background:#fff;border:2px solid #b54708"></span>candidate (unpromoted)</span></div>'
        f"{_fallback_table(ordered_points)}</section>"
    )


def _kpi_row(kpis: dict[str, Any]) -> str:
    champion_sub = "N/A"
    if kpis.get("champion_level_denominator"):
        champion_sub = f"{kpis.get('champion_level_numerator')}/{kpis.get('champion_level_denominator')} verdicts"
        if kpis.get("champion_manual_review"):
            champion_sub += f" &middot; {kpis['champion_manual_review']} manual review"
    candidate_sub = "N/A"
    if kpis.get("candidate_level_denominator"):
        candidate_sub = f"{kpis.get('candidate_level_numerator')}/{kpis.get('candidate_level_denominator')} verdicts"

    delta_champion_html = _pp_from_fraction(kpis.get("delta_vs_champion"))
    delta_champion_sub = _esc(kpis.get("delta_vs_champion_reason"), "")
    delta_anchor_html = _pp_from_fraction(kpis.get("delta_vs_anchor"))
    delta_anchor_sub = _esc(kpis.get("delta_vs_anchor_reason"), "")

    cards = [
        _kpi("Champion level", _pct(kpis.get("champion_level")), champion_sub),
        _kpi("Candidate level", _pct(kpis.get("candidate_level")), candidate_sub),
        _kpi(
            "Delta vs champion",
            f'<span class="{_delta_class(kpis.get("delta_vs_champion"))}">{delta_champion_html}</span>',
            delta_champion_sub,
        ),
        _kpi(
            "Delta vs anchor",
            f'<span class="{_delta_class(kpis.get("delta_vs_anchor"))}">{delta_anchor_html}</span>',
            delta_anchor_sub,
        ),
        _kpi("Accepted-run slope", _slope_text(kpis.get("slope")), _esc(kpis.get("slope_reason"), "")),
        _kpi("Candidate recommendation", _badge(kpis.get("candidate_recommendation")) or "N/A"),
        _kpi("Regressions", "N/A" if kpis.get("regressions") is None else str(kpis["regressions"])),
        _kpi("Manual review", "N/A" if kpis.get("manual_review") is None else str(kpis["manual_review"])),
        _kpi(
            "P(candidate > baseline)",
            "N/A" if kpis.get("probability_of_improvement") is None else f"{kpis['probability_of_improvement']:.3f}",
            (
                f"threshold {float(kpis['probability_of_improvement_threshold']):.2f}"
                if kpis.get("probability_of_improvement_threshold") is not None
                else "one run only — uncertainty unavailable" if not kpis.get("reliability_available") else ""
            ),
        ),
    ]
    return f'<div class="kpis">{"".join(cards)}</div>'


def _regression_panel(regressions: dict[str, Any]) -> str:
    items = regressions.get("items", [])
    if not items:
        return '<section class="panel"><h2>Regressions</h2><p class="empty">No detected regressions</p></section>'
    rows = []
    for item in items:
        tags = ", ".join(_esc(tag) for tag in item.get("tags", [])) or "untagged"
        evaluators = ", ".join(_esc(evaluator_id) for evaluator_id in item.get("failing_evaluators", [])) or "none reported"
        link = f'<a href="{_esc(item.get("review_link"))}">review.html</a>' if item.get("review_link") else "N/A"
        rows.append(
            "<tr>"
            f"<td>{_esc(item.get('case_id'))}</td>"
            f"<td>{_esc(item.get('description'))}</td>"
            f"<td>{_badge(item.get('risk'))}</td>"
            f"<td>{tags}</td>"
            f"<td>{evaluators}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    return (
        '<section class="panel"><h2>Regressions</h2>'
        "<table><thead><tr><th>Case</th><th>Description</th><th>Risk</th><th>Tags</th><th>Failing evaluators</th><th>Evidence</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _evaluator_panel(evaluators: list[dict[str, Any]]) -> str:
    if not evaluators:
        return '<section class="panel"><h2>Evaluators</h2><p class="empty">No evaluator evidence is available yet.</p></section>'
    rows = []
    for row in evaluators:
        champ = "N/A" if row.get("champion_score") is None else f"{row['champion_score']:.3f}"
        cand = "N/A" if row.get("candidate_score") is None else f"{row['candidate_score']:.3f}"
        delta = "N/A" if row.get("delta") is None else f"{row['delta']:+.3f}"
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('evaluator_id'))} "
            f"<span class=\"sub\">v{_esc(row.get('evaluator_version'))} · {_esc(row.get('kind'))}</span></td>"
            f"<td>{champ}</td><td>{cand}</td>"
            f"<td class=\"{_delta_class(row.get('delta'))}\">{delta}</td>"
            f"<td>{row.get('pass', 0)}</td><td>{row.get('fail', 0)}</td><td>{row.get('error', 0)}</td>"
            f"<td>{row.get('not_applicable', 0)}</td><td>{row.get('cases_scored', 0)}</td>"
            "</tr>"
        )
    return (
        '<section class="panel"><h2>Evaluators</h2>'
        "<table><thead><tr><th>Evaluator</th><th>Comparable reference</th><th>Candidate score</th><th>Delta</th>"
        "<th>Pass</th><th>Fail</th><th>Error</th><th>N/A</th><th>Cases scored</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _reliability_stage_row(label: str, overall: dict[str, Any]) -> str:
    interval = overall.get("wilson_interval")
    ci_text = "N/A" if not interval else f"[{interval[0] * 100:.1f}%, {interval[1] * 100:.1f}%]"
    return (
        "<tr>"
        f"<td>{_esc(label)}</td>"
        f"<td>{overall.get('pass', 0) + overall.get('fail', 0) + overall.get('manual_review', 0)}</td>"
        f"<td>{_pct(overall.get('pass_rate'))}</td>"
        f"<td>{ci_text}</td>"
        f"<td>{overall.get('manual_review', 0)}</td>"
        "</tr>"
    )


def _reliability_panel(reliability: dict[str, Any]) -> str:
    if not reliability.get("available"):
        reason = reliability.get("reason", "Not available")
        return (
            '<section class="panel"><h2>Reliability</h2>'
            f'<p class="empty">One run only — uncertainty unavailable. {_esc(reason)}</p></section>'
        )

    method = reliability.get("method", {})
    comparison = reliability.get("comparison", {})
    config = reliability.get("config", {})
    stages = reliability.get("stages", {})
    bootstrap = comparison.get("bootstrap", {})
    probability = comparison.get("probability_of_improvement")
    threshold = config.get("minimum_probability_of_improvement")

    stage_rows = _reliability_stage_row("Baseline", stages.get("baseline", {})) + _reliability_stage_row(
        "Candidate", stages.get("candidate", {})
    )

    flaky = reliability.get("flaky_cases", [])
    flaky_rows = "".join(
        f"<tr><td>{_esc(item.get('case_id'))}</td><td>{_badge(item.get('risk'))}</td></tr>" for item in flaky
    ) or '<tr><td colspan="2" class="empty">No flaky cases detected</td></tr>'

    unstable = reliability.get("unstable_evaluators", [])
    unstable_rows = "".join(
        f"<tr><td>{_esc(item.get('stage'))}</td><td>{_esc(item.get('evaluator'))}</td>"
        f"<td>{item.get('pass', 0)}</td><td>{item.get('fail', 0)}</td></tr>"
        for item in unstable
    ) or '<tr><td colspan="4" class="empty">No evaluator instability detected</td></tr>'

    incompatible = comparison.get("incompatible_evaluators", [])
    incompatible_html = "".join(
        f"<li>{_esc(item.get('evaluator_id'))}: baseline v{_esc(','.join(item.get('baseline_versions', [])))} "
        f"vs candidate v{_esc(','.join(item.get('candidate_versions', [])))} — not aggregated together</li>"
        for item in incompatible
    ) or "<li>None</li>"

    health = reliability.get("dataset_health", {})
    health_html = "".join(
        f"<li>{_esc(label)}: {', '.join(_esc(case_id) for case_id in health.get(key, [])) or 'none'}</li>"
        for label, key in (
            ("Consistently easy", "consistently_easy"),
            ("Consistently failing", "consistently_failing"),
            ("Evaluator disagreement", "evaluator_disagreement"),
            ("Low-observation tags", "low_observation_tags"),
            ("Distinguishing cases", "distinguishing_cases"),
        )
    )

    return f"""<section class="panel"><h2>Reliability</h2>
<p class="sub">Method {_esc(method.get('version'))} &middot; confidence {_esc(method.get('confidence_level'))} &middot;
{_esc(method.get('bootstrap_samples'))} bootstrap samples &middot; seed {_esc(method.get('seed'))}</p>
<table><thead><tr><th>Stage</th><th>n</th><th>Pass rate</th><th>95% CI</th><th>Manual review</th></tr></thead>
<tbody>{stage_rows}</tbody></table>
<p><strong>Delta</strong>: {_pp_from_fraction(comparison.get('pass_rate_delta'))} &middot;
bootstrap CI [{_pp_from_fraction(bootstrap.get('ci_low'))}, {_pp_from_fraction(bootstrap.get('ci_high'))}] &middot;
P(candidate&gt;baseline) {"N/A" if probability is None else f"{probability:.3f}"}
{f" (threshold {float(threshold):.2f})" if threshold is not None else ""}</p>
<h3>Flaky cases</h3><table><tbody>{flaky_rows}</tbody></table>
<h3>Evaluator instability</h3><table><thead><tr><th>Stage</th><th>Evaluator</th><th>Pass</th><th>Fail</th></tr></thead>
<tbody>{unstable_rows}</tbody></table>
<h3>Incompatible evaluator versions</h3><ul>{incompatible_html}</ul>
<h3>Dataset health (advisory — never auto-applied)</h3><ul>{health_html}</ul>
</section>"""


def _distribution_table(title: str, counts: dict[str, int]) -> str:
    if not counts:
        return f"<div><h3>{_esc(title)}</h3><p class=\"empty\">No data</p></div>"
    rows = "".join(f"<tr><td>{_esc(key)}</td><td>{value}</td></tr>" for key, value in sorted(counts.items()))
    return f"<div><h3>{_esc(title)}</h3><table><tbody>{rows}</tbody></table></div>"


def _coverage_panel(coverage: dict[str, Any]) -> str:
    if not coverage.get("available"):
        return f'<section class="panel"><h2>Coverage</h2><p class="empty">{_esc(coverage.get("reason"), "Not available")}</p></section>'
    warnings_html = "".join(f'<div class="banner warn">{_esc(warning)}</div>' for warning in coverage.get("warnings", []))
    duplicates = coverage.get("duplicate_inputs", [])
    duplicates_html = ""
    if duplicates:
        groups = "; ".join(", ".join(_esc(case_id) for case_id in group) for group in duplicates)
        duplicates_html = f'<div class="banner warn">Exact duplicate inputs: {groups}</div>'
    return (
        '<section class="panel"><h2>Coverage</h2>'
        f'<p>Total accepted cases: <strong>{coverage.get("total_cases", 0)}</strong> &middot; '
        f'Untagged: {coverage.get("untagged", 0)} &middot; High/critical: {coverage.get("high_or_critical", 0)}</p>'
        f"{warnings_html}{duplicates_html}"
        '<div class="header-grid">'
        f'{_distribution_table("By split", coverage.get("by_split", {}))}'
        f'{_distribution_table("By risk", coverage.get("by_risk", {}))}'
        f'{_distribution_table("By source", coverage.get("by_source", {}))}'
        f'{_distribution_table("By tag", coverage.get("by_tag", {}))}'
        "</div>"
        '<p class="sub">Coverage growth reflects dataset distribution, not measured quality improvement.</p></section>'
    )


def _timeline_panel(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return '<section class="panel"><h2>Timeline</h2><p class="empty">No history has been recorded yet.</p></section>'
    rows = []
    for row in timeline:
        markers = []
        if row.get("is_current_champion"):
            markers.append(_badge("champion"))
        if row.get("promoted"):
            markers.append(_badge("promoted"))
        links = []
        if row.get("comparison_link"):
            links.append(f'<a href="{_esc(row["comparison_link"])}">comparison</a>')
        if row.get("review_link"):
            links.append(f'<a href="{_esc(row["review_link"])}">review</a>')
        rows.append(
            "<tr>"
            f"<td>{_esc(row.get('recorded_at'))}</td>"
            f"<td>{_esc(row.get('label'))}<div class=\"sub\">{_esc(row.get('agent_version'))}</div></td>"
            f"<td>{_esc(row.get('stage'))} / {_esc(row.get('role'))}</td>"
            f"<td>{_esc(row.get('cohort_prefix'))}</td>"
            f"<td>{_pct(row.get('pass_rate'))}</td>"
            f"<td>{_badge(row.get('acceptance_recommendation'))}</td>"
            f"<td>{_badge(row.get('human_decision'))}</td>"
            f"<td>{' '.join(markers) or '&nbsp;'}</td>"
            f"<td>{' &middot; '.join(links) or 'N/A'}</td>"
            "</tr>"
        )
    return (
        '<section class="panel"><h2>Timeline</h2>'
        "<table><thead><tr><th>Recorded</th><th>Label / version</th><th>Stage / role</th><th>Cohort</th>"
        "<th>Pass rate</th><th>Recommendation</th><th>Decision</th><th>Promotion</th><th>Evidence</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def render_dashboard(dashboard: dict[str, Any], output_path: str | Path) -> Path:
    css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    header = dashboard.get("header", {})
    integrity = dashboard.get("integrity", {"valid": True, "errors": []})
    warnings = dashboard.get("warnings", [])

    banners = []
    if not integrity.get("valid", True):
        error_list = "; ".join(str(item) for item in integrity.get("errors", []))
        banners.append(f'<div class="banner error">History integrity check failed: {_esc(error_list)}</div>')
    for warning in warnings:
        banners.append(f'<div class="banner warn">{_esc(warning)}</div>')

    header_html = (
        '<header class="panel"><h1>AIK Champion History Dashboard</h1>'
        f'<div class="sub">Generated {_esc(header.get("generated_at"))} &middot; History root: '
        f'<code class="mono">{_esc(header.get("history_root"))}</code></div>'
        f"{''.join(banners)}"
        '<div class="header-grid">'
        f'<div class="header-item"><div class="label">Current cohort</div><div class="value">{_esc((header.get("current_cohort_id") or "")[7:15], "N/A")}</div></div>'
        f'<div class="header-item"><div class="label">Anchor</div><div class="value">{_esc(header.get("anchor_agent_version"))} '
        f'<span class="sub">{_esc(_short(header.get("anchor_run_id")))}</span></div></div>'
        f'<div class="header-item"><div class="label">Champion</div><div class="value">{_esc(header.get("champion_agent_version"))} '
        f'<span class="sub">{_esc(_short(header.get("champion_run_id")))}</span></div></div>'
        f'<div class="header-item"><div class="label">Candidate</div><div class="value">{_esc(header.get("candidate_agent_version"))} '
        f'<span class="sub">{_esc(_short(header.get("candidate_run_id")))}</span></div></div>'
        "</div></header>"
    )

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIK Champion History Dashboard</title><style>{css}</style></head>
<body><main class="wrap">
{header_html}
{_kpi_row(dashboard.get("kpis", {}))}
{_progress_panel(dashboard.get("trend", []))}
{_regression_panel(dashboard.get("regressions", {}))}
{_evaluator_panel(dashboard.get("evaluators", []))}
{_reliability_panel(dashboard.get("reliability", {}))}
{_coverage_panel(dashboard.get("coverage", {}))}
{_timeline_panel(dashboard.get("timeline", []))}
<p class="sub">Generated locally from open AIK artifacts. No network request or remote asset is used.</p>
</main></body></html>"""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8", newline="\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an AIK dashboard.json into standalone HTML")
    parser.add_argument("dashboard_json")
    parser.add_argument("--output", "-o", default="dashboard.html")
    args = parser.parse_args()
    dashboard = json.loads(Path(args.dashboard_json).read_text(encoding="utf-8"))
    output = render_dashboard(dashboard, args.output)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
