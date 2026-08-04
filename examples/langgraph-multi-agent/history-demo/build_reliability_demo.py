#!/usr/bin/env python3
"""Offline demo of AIK Sprint 6 statistical reliability.

Runs a small two-case suite through the real external-process runner bridge
with `--repetitions`, evaluates every observation, computes a deterministic
reliability report, and archives the result as a new candidate in the
Sprint 5 champion-history demo. Answers the exact question Sprint 6 adds:
is an observed improvement repeatable, or could it be agent variability?

Never invokes LangGraph, an LLM, or the network. `reliability_runner.py` is a
pure, seed-free function of (case_id, stage, repetition), so this demo is
100% reproducible offline. Requires `build_history_demo.py`'s anchor/champion
history; runs it automatically if it has not been generated yet.
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

DEMO_ROOT = Path(__file__).resolve().parent
if str(DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ROOT))

from common import write_json, write_jsonl  # noqa: E402
from evaluate_results import evaluate, evaluate_trials  # noqa: E402
from execute_cases import execute  # noqa: E402
from history import history_promote, history_record  # noqa: E402
from init_case import initialize  # noqa: E402
from reliability import run_reliability  # noqa: E402
from run_review import run as build_review  # noqa: E402

import build_history_demo  # noqa: E402


HISTORY_ROOT = DEMO_ROOT / "aik-history"
RUNNER = [sys.executable, str(DEMO_ROOT / "reliability_runner.py")]
REPETITIONS = 6

CASES = [
    {
        "id": "stable-greeting",
        "description": "A plain greeting never needs a specialist",
        "input": {"messages": [{"role": "user", "content": "Hello!"}]},
        "expected": {"forbidden_agents": ["billing-agent"]},
        "tags": ["routing", "negative"],
        "risk": "low",
        "source": "authored_scenario",
        "split": "challenge",
        "status": "accepted",
    },
    {
        "id": "intermittent-billing-routing",
        "description": "A billing request must reach the billing specialist",
        "input": {"messages": [{"role": "user", "content": "I was charged twice."}]},
        "expected": {"required_agents": ["billing-agent"]},
        "tags": ["billing", "routing"],
        "risk": "high",
        "source": "known_regression",
        "split": "regression",
        "status": "accepted",
    },
]


def _build_case() -> Path:
    workspace = Path(tempfile.mkdtemp(prefix="aik-reliability-demo-")).resolve()
    case_dir = initialize("reliability-demo", workspace)
    write_jsonl(case_dir / "test_cases.jsonl", CASES)
    write_json(
        case_dir / "evaluation.json",
        {
            "format_version": "1.0",
            "suite_id": "reliability-demo-suite",
            "version": "1.0.0",
            "evaluators": [
                {"id": "execution-ok", "type": "no_execution_error", "required": True},
                {"id": "required-agents", "type": "required_agents", "required": True},
                {"id": "forbidden-agents", "type": "forbidden_agents", "required": True},
            ],
            "acceptance": {
                "reject_on_evaluator_error": True,
                "no_regression_at_or_above_risk": "high",
                "critical_cases_must_pass": True,
                "maximum_regressions": 0,
                "minimum_candidate_pass_rate": 0.0,
                "minimum_delta": 0.0,
            },
            "reliability": {
                "minimum_repetitions": 5,
                "confidence_level": 0.95,
                "bootstrap_samples": 5000,
                "minimum_probability_of_improvement": 0.9,
                "maximum_flaky_case_rate": 0.5,
            },
        },
    )
    (case_dir / "decision.yaml").write_text("status: accepted\n", encoding="utf-8")
    return case_dir


def build_demo() -> dict:
    if not HISTORY_ROOT.exists():
        print("No champion history found yet; running build_history_demo.py first...")
        build_history_demo.build_demo()

    case_dir = _build_case()
    old_cwd = Path.cwd()
    os.chdir(case_dir.parent)
    try:
        for stage in ("baseline", "candidate"):
            execute(case_dir, stage, RUNNER, repetitions=REPETITIONS, overwrite=True)
            evaluate_trials(case_dir, stage, overwrite=True)
            # The deterministic Sprint 4/5 single-run comparison reuses
            # repetition 0's evidence (AIK_REPETITION is unset for a single run).
            execute(case_dir, stage, RUNNER, overwrite=True)
            evaluate(case_dir, stage, overwrite=True)

        reliability = run_reliability(case_dir)
        write_json(case_dir / "reliability.json", reliability)
        build_review(case_dir)

        candidate = history_record(
            case_dir,
            history_root=HISTORY_ROOT,
            stage="candidate",
            label="Reliability-checked billing routing fix",
            agent_version="demo-v3",
        )
        comparison = reliability["comparison"]
        print(f"candidate recorded: {candidate['run_id']}")
        print(f"paired_cases={comparison['paired_cases']}")
        print(f"probability_of_improvement={comparison['probability_of_improvement']}")
        print(f"bootstrap_ci=[{comparison['bootstrap']['ci_low']}, {comparison['bootstrap']['ci_high']}]")
        print(f"flaky_cases={comparison['flaky_cases']}")
        print(f"archived acceptance_recommendation={candidate['acceptance_recommendation']}")

        if candidate["acceptance_recommendation"] == "eligible":
            promotion = history_promote(candidate["run_id"], history_root=HISTORY_ROOT)
            print(f"champion promoted: {promotion['champion_run_id']}")
        else:
            print(
                "promotion correctly withheld: the single-run comparison looked like a clean "
                "improvement, but repeated trials show the probability of improvement is below "
                "the configured threshold — the same evidence that looked eligible one-shot is "
                "not yet repeatable evidence."
            )
        return reliability
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(case_dir.parent, ignore_errors=True)


if __name__ == "__main__":
    build_demo()
