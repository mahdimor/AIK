#!/usr/bin/env python3
"""Run the complete LangGraph multi-agent demo through AIK."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


DEMO_DIR = Path(__file__).resolve().parent
ROOT = DEMO_DIR.parents[1]
CASE_DIR = DEMO_DIR / "case"
ADAPTER = ROOT / ".agents" / "skills" / "improve-agent" / "scripts" / "langgraph_runner.py"
OPENAI_JUDGE = ROOT / ".agents" / "skills" / "improve-agent" / "scripts" / "openai_judge.py"


def call(arguments: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(arguments, cwd=ROOT, env=environment, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AIK's LangGraph multi-agent demo")
    parser.add_argument("--mode", choices=("mock", "openai"), default="mock")
    parser.add_argument("--model", default=os.getenv("AIK_DEMO_MODEL", "gpt-5.6-luna"))
    parser.add_argument("--judge", choices=("none", "openai"), default="none")
    args = parser.parse_args()
    missing = [package for package in ("langchain", "langgraph") if importlib.util.find_spec(package) is None]
    if args.mode == "openai" and importlib.util.find_spec("langchain_openai") is None:
        missing.append("langchain-openai")
    if missing:
        parser.error(
            "Missing optional dependencies: "
            + ", ".join(missing)
            + ". Install examples/langgraph-multi-agent/requirements.txt"
        )
    if (args.mode == "openai" or args.judge == "openai") and not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is required for OpenAI agent or judge mode")

    environment = os.environ.copy()
    environment["AIK_DEMO_MODE"] = args.mode
    environment["AIK_DEMO_MODEL"] = args.model
    runner = [
        "--",
        sys.executable,
        str(ADAPTER),
        "--graph",
        "multi_agent_demo:build_graph",
        "--transform",
        "multi_agent_demo:to_aik_evidence",
    ]
    for stage in ("baseline", "candidate"):
        call(
            [
                sys.executable,
                str(ROOT / "aik.py"),
                "run",
                str(CASE_DIR),
                "--stage",
                stage,
                "--overwrite",
                "--cwd",
                str(DEMO_DIR),
                *runner,
            ],
            environment,
        )
        evaluate_command = [
            sys.executable,
            str(ROOT / "aik.py"),
            "evaluate",
            str(CASE_DIR),
            "--stage",
            stage,
            "--overwrite",
        ]
        if args.judge == "openai":
            evaluate_command.extend(["--", sys.executable, str(OPENAI_JUDGE)])
        call(evaluate_command, environment)
    call([sys.executable, str(ROOT / "aik.py"), "review", str(CASE_DIR)], environment)
    print(f"review={CASE_DIR / 'review.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
