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
    passed = "PASS" if result.get("passed") is True else "FAIL"
    contracts = html.escape(json.dumps(result.get("contracts", {}), ensure_ascii=False, indent=2))
    return (
        f'<div class="pane"><div class="pane-title">{title} · {passed}</div>'
        f"<pre>{output}</pre><div class=\"meta\">Agents: {agents}<br>Tools: {tools}</div>"
        f"<details><summary>Contracts and raw details</summary><pre>{contracts}</pre></details></div>"
    )


def _recommend(summary: dict[str, int]) -> tuple[str, str]:
    if summary.get("regressed", 0):
        return "REJECT OR FIX", "At least one behavior regressed. Review and correct every regression before acceptance."
    if summary.get("not_comparable", 0):
        return "MORE TESTS REQUIRED", "Some cases are missing or invalid, so the candidate cannot be assessed completely."
    if summary.get("improved", 0):
        return "READY FOR HUMAN DECISION", "The candidate improved measured cases without detected pass/fail regressions. Human approval is still required."
    return "NO MEASURABLE IMPROVEMENT", "No test changed from failing to passing. Review metric deltas and the value of the change."


def render(comparison_path: str | Path, case_yaml_path: str | Path, output_path: str | Path) -> Path:
    comparison = json.loads(Path(comparison_path).read_text(encoding="utf-8"))
    summary = comparison["summary"]
    case = _simple_yaml_values(Path(case_yaml_path))
    recommendation, reason = _recommend(summary)
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
        sections.append(
            '<section class="case">'
            f'<div class="case-head"><span class="badge {status}">{STATUS_LABELS[status]}</span>'
            f'<div><h3>{html.escape(item["case_id"])}</h3><div class="sub">{html.escape(item["description"])}</div></div></div>'
            f'<div class="case-body"><div class="grid">{_pane("Baseline", item.get("baseline"))}{_pane("Candidate", item.get("candidate"))}</div>'
            f'<div class="meta">Tags: {html.escape(tags)}<br><span class="delta">Score deltas: {html.escape(delta_text)}</span></div>{warnings}</div></section>'
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
