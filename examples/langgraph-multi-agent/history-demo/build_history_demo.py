#!/usr/bin/env python3
"""Deterministic, safe setup script for the AIK champion-history demo.

Archives the already-produced LangGraph multi-agent mock evidence in
`examples/langgraph-multi-agent/case` (baseline pass rate 75%, candidate pass
rate 100%) as an anchor -> candidate -> champion history. Demonstrates that a
pending human decision blocks promotion and that explicit acceptance
unblocks it, then generates the local dashboard.

This script never invokes LangGraph, an LLM, an SDK, or any network call. It
only copies and archives evidence Sprint 4 already produced.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from common import DataError, write_json  # noqa: E402
from dashboard_model import build_dashboard  # noqa: E402
from history import history_init, history_promote, history_record  # noqa: E402
from render_dashboard import render_dashboard  # noqa: E402


SOURCE_CASE = Path(__file__).resolve().parent.parent / "case"
DEMO_ROOT = Path(__file__).resolve().parent / "aik-history"


def build_demo() -> dict:
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)

    workspace = Path(tempfile.mkdtemp(prefix="aik-history-demo-")).resolve()
    case_copy = workspace / "case"
    shutil.copytree(SOURCE_CASE, case_copy)
    old_cwd = Path.cwd()
    os.chdir(workspace)
    try:
        anchor = history_init(
            case_copy,
            history_root=DEMO_ROOT,
            stage="baseline",
            label="Initial supervisor",
            agent_version="demo-v1",
        )
        print(f"anchor/champion recorded: {anchor['run_id']} pass_rate={anchor['pass_rate']}")

        pending_candidate = history_record(
            case_copy,
            history_root=DEMO_ROOT,
            stage="candidate",
            label="Fixed mixed routing (pending review)",
            agent_version="demo-v2",
            role="candidate",
        )
        print(f"candidate recorded: {pending_candidate['run_id']} decision={pending_candidate['human_decision']}")

        try:
            history_promote(pending_candidate["run_id"], history_root=DEMO_ROOT)
        except DataError as exc:
            print(f"promotion correctly blocked while pending: {exc}")
        else:
            raise AssertionError("Promotion of a pending candidate must be blocked")

        decision_path = case_copy / "decision.yaml"
        decision_path.write_text(
            decision_path.read_text(encoding="utf-8").replace("status: pending", "status: accepted"),
            encoding="utf-8",
        )

        accepted_candidate = history_record(
            case_copy,
            history_root=DEMO_ROOT,
            stage="candidate",
            label="Fixed mixed routing (accepted)",
            agent_version="demo-v2",
            role="candidate",
        )
        print(f"candidate recorded: {accepted_candidate['run_id']} decision={accepted_candidate['human_decision']}")

        promotion = history_promote(accepted_candidate["run_id"], history_root=DEMO_ROOT)
        print(f"champion promoted: {promotion['champion_run_id']}")

        dashboard = build_dashboard(DEMO_ROOT, candidate_run_id=accepted_candidate["run_id"])
        write_json(DEMO_ROOT / "dashboard.json", dashboard)
        render_dashboard(dashboard, DEMO_ROOT / "dashboard.html")

        print(f"anchor level: {anchor['pass_rate'] * 100:.0f}%")
        print(f"champion level after promotion: {dashboard['kpis']['champion_level'] * 100:.0f}%")
        print(f"delta vs anchor: {dashboard['kpis']['delta_vs_anchor'] * 100:+.0f} percentage points")
        print(f"regressions: {dashboard['regressions']['summary'].get('regressed', 0)}")
        print(
            "current recommendation: "
            f"{dashboard['kpis']['candidate_recommendation']}/{dashboard['kpis']['candidate_human_decision']}"
        )
        return dashboard
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    build_demo()
