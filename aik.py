#!/usr/bin/env python3
"""Small dependency-free command line interface for Agent Improvement Kit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


VERSION = "0.5.1"
ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from common import DataError, write_json  # noqa: E402
from execute_cases import execute  # noqa: E402
from evaluate_results import evaluate, evaluate_trials  # noqa: E402
from export_feedback import export_feedback  # noqa: E402
from init_case import initialize  # noqa: E402
from promote_tests import promote  # noqa: E402
from run_review import run as build_review  # noqa: E402
from validate_case import validate  # noqa: E402
from history import (  # noqa: E402
    history_init,
    history_list,
    history_promote,
    history_record,
    history_validate,
    parse_metadata,
)
from dashboard_model import build_dashboard  # noqa: E402
from render_dashboard import render_dashboard  # noqa: E402
from reliability import reliability_acceptance, run_reliability  # noqa: E402


def _print_summary(result: dict) -> None:
    summary = result["summary"]
    print(
        " ".join(
            f"{status}={summary[status]}"
            for status in ("improved", "regressed", "unchanged", "not_comparable")
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aik",
        description="Turn an agent behavior change into a reproducible before/after review.",
    )
    parser.add_argument("--version", action="version", version=f"AIK {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="Create an empty improvement case")
    init_parser.add_argument("name")
    init_parser.add_argument("--root", default="improvements")

    validate_parser = commands.add_parser("validate", help="Validate one improvement case")
    validate_parser.add_argument("case_dir")

    run_parser = commands.add_parser(
        "run",
        help="Execute cases through an external runner command",
        epilog="Place the runner command after --, for example: -- python my_runner.py",
    )
    run_parser.add_argument("case_dir")
    run_parser.add_argument("--stage", required=True, choices=("baseline", "candidate"))
    run_parser.add_argument("--timeout", type=float, default=60)
    run_parser.add_argument("--id", action="append", dest="ids")
    run_parser.add_argument("--tag", action="append", dest="tags")
    run_mode = run_parser.add_mutually_exclusive_group()
    run_mode.add_argument("--resume", action="store_true")
    run_mode.add_argument("--overwrite", action="store_true")
    run_parser.add_argument("--cwd")
    run_parser.add_argument("--repetitions", type=int, default=1, help="Independently execute each case N times for reliability analysis")

    evaluate_parser = commands.add_parser(
        "evaluate",
        help="Evaluate raw evidence with the configured evaluator suite",
        epilog="Optionally place an external LLM judge command after --",
    )
    evaluate_parser.add_argument("case_dir")
    evaluate_parser.add_argument("--stage", required=True, choices=("baseline", "candidate"))
    evaluate_parser.add_argument("--timeout", type=float, default=60)
    evaluate_parser.add_argument("--id", action="append", dest="ids")
    evaluate_parser.add_argument("--tag", action="append", dest="tags")
    evaluate_mode = evaluate_parser.add_mutually_exclusive_group()
    evaluate_mode.add_argument("--resume", action="store_true")
    evaluate_mode.add_argument("--overwrite", action="store_true")
    evaluate_parser.add_argument("--trials", action="store_true", help="Evaluate repeated observations from --repetitions instead of the single-run stage")

    reliability_parser = commands.add_parser(
        "reliability", help="Aggregate repeated-observation trials into a statistical reliability report"
    )
    reliability_parser.add_argument("case_dir")
    reliability_parser.add_argument("--output", default=None, help="Alternate path for reliability.json")

    feedback_parser = commands.add_parser(
        "feedback",
        help="Export optimizer-visible feedback; holdout cases are never included",
    )
    feedback_parser.add_argument("case_dir")
    feedback_parser.add_argument("--stage", default="candidate", choices=("baseline", "candidate"))
    feedback_parser.add_argument("--output", default=None, help="Alternate path for feedback.json")

    review_parser = commands.add_parser("review", help="Compare runs and build the HTML review")
    review_parser.add_argument("case_dir")

    demo_parser = commands.add_parser("demo", help="Regenerate the included example review")
    demo_parser.add_argument(
        "--case-dir",
        default=str(ROOT / "examples" / "support-agent" / "confirmation-before-action"),
    )

    promote_parser = commands.add_parser("promote", help="Promote accepted cases to the regression dataset")
    promote_parser.add_argument("case_dir")
    promote_parser.add_argument("--dataset", default="datasets/regression.jsonl")
    promote_parser.add_argument(
        "--status",
        action="append",
        choices=("improved", "regressed", "unchanged"),
        default=None,
    )
    promote_parser.add_argument("--ids", nargs="*")

    history_parser = commands.add_parser("history", help="Manage immutable champion history")
    history_commands = history_parser.add_subparsers(dest="history_command", required=True)

    history_init_parser = history_commands.add_parser("init", help="Archive the first anchor/champion run")
    history_init_parser.add_argument("case_dir")
    history_init_parser.add_argument("--stage", default="baseline")
    history_init_parser.add_argument("--label", required=True)
    history_init_parser.add_argument("--agent-version", required=True)
    history_init_parser.add_argument("--history-root", default="aik-history")
    history_init_parser.add_argument("--metadata", action="append", default=[])

    history_record_parser = history_commands.add_parser("record", help="Archive a candidate or cohort anchor run")
    history_record_parser.add_argument("case_dir")
    history_record_parser.add_argument("--stage", required=True, choices=("baseline", "candidate"))
    history_record_parser.add_argument("--label", required=True)
    history_record_parser.add_argument("--agent-version", required=True)
    history_record_parser.add_argument("--role", default="candidate", choices=("candidate", "cohort_anchor"))
    history_record_parser.add_argument("--history-root", default="aik-history")
    history_record_parser.add_argument("--metadata", action="append", default=[])

    history_promote_parser = history_commands.add_parser("promote", help="Promote an archived candidate to champion")
    history_promote_parser.add_argument("run_id")
    history_promote_parser.add_argument("--history-root", default="aik-history")

    history_list_parser = history_commands.add_parser("list", help="List archived history runs")
    history_list_parser.add_argument("--history-root", default="aik-history")

    history_validate_parser = history_commands.add_parser("validate", help="Validate history integrity")
    history_validate_parser.add_argument("--history-root", default="aik-history")

    dashboard_parser = commands.add_parser("dashboard", help="Generate the local champion-history dashboard")
    dashboard_parser.add_argument("--history-root", default="aik-history")
    dashboard_parser.add_argument("--output", default=None, help="Alternate path for dashboard.html")
    dashboard_parser.add_argument("--candidate", default=None, dest="candidate_run_id")
    dashboard_parser.add_argument("--open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    runner_command: list[str] = []
    if raw_arguments[:1] in (["run"], ["evaluate"]) and "--" in raw_arguments:
        separator = raw_arguments.index("--")
        runner_command = raw_arguments[separator + 1 :]
        raw_arguments = raw_arguments[:separator]
    args = parser.parse_args(raw_arguments)
    try:
        if args.command == "init":
            print(initialize(args.name, args.root))
            return 0

        if args.command == "validate":
            errors, warnings = validate(args.case_dir)
            for warning in warnings:
                print(f"WARNING: {warning}")
            for error in errors:
                print(f"ERROR: {error}")
            if errors:
                return 1
            print("VALID")
            return 0

        if args.command == "run":
            summary = execute(
                args.case_dir,
                args.stage,
                runner_command,
                timeout_seconds=args.timeout,
                selected_ids=set(args.ids) if args.ids else None,
                selected_tags=set(args.tags) if args.tags else None,
                resume=args.resume,
                overwrite=args.overwrite,
                runner_cwd=args.cwd,
                repetitions=args.repetitions,
            )
            print(" ".join(f"{key}={summary[key]}" for key in ("executed", "skipped", "execution_errors")))
            print(f"results={summary['destination']}")
            return 1 if summary["execution_errors"] else 0

        if args.command == "evaluate":
            entrypoint = evaluate_trials if args.trials else evaluate
            summary = entrypoint(
                args.case_dir,
                args.stage,
                runner_command,
                timeout_seconds=args.timeout,
                selected_ids=set(args.ids) if args.ids else None,
                selected_tags=set(args.tags) if args.tags else None,
                resume=args.resume,
                overwrite=args.overwrite,
            )
            print(" ".join(f"{key}={summary[key]}" for key in ("passed", "failed", "manual_review", "evaluator_errors")))
            print(f"results={summary['destination']}")
            return 1 if summary["evaluator_errors"] else 0

        if args.command == "feedback":
            report = export_feedback(args.case_dir, args.stage, args.output)
            summary = report["summary"]
            print(" ".join(f"{key}={summary[key]}" for key in sorted(summary)))
            print(f"export_number={report['export_number']}")
            for warning in report["warnings"]:
                print(f"WARNING: {warning}")
            print(f"feedback={report['destination']}")
            return 0

        if args.command in {"review", "demo"}:
            case_dir = args.case_dir
            comparison_path, review_path, result = build_review(case_dir)
            _print_summary(result)
            print(f"comparison={comparison_path}")
            print(f"review={review_path}")
            return 0

        if args.command == "reliability":
            reliability = run_reliability(args.case_dir)
            output_path = write_json(Path(args.output) if args.output else Path(args.case_dir) / "reliability.json", reliability)
            acceptance = reliability_acceptance(reliability)
            print(f"paired_cases={reliability['comparison']['paired_cases']}")
            print(f"probability_of_improvement={reliability['comparison']['probability_of_improvement']}")
            print(f"recommendation={acceptance['recommendation']}")
            print(f"reliability={output_path}")
            return 0

        if args.command == "promote":
            promoted = promote(
                args.case_dir,
                args.dataset,
                set(args.status or ["improved"]),
                set(args.ids) if args.ids else None,
            )
            print(f"promoted={len(promoted)}")
            for case_id in promoted:
                print(case_id)
            return 0

        if args.command == "history":
            if args.history_command == "init":
                manifest = history_init(
                    args.case_dir,
                    history_root=args.history_root,
                    stage=args.stage,
                    label=args.label,
                    agent_version=args.agent_version,
                    metadata=parse_metadata(args.metadata),
                )
                print(f"run_id={manifest['run_id']}")
                print(f"pass_rate={manifest['pass_rate']}")
                print(f"cohort_id={manifest['cohort_id']}")
                return 0
            if args.history_command == "record":
                manifest = history_record(
                    args.case_dir,
                    history_root=args.history_root,
                    stage=args.stage,
                    label=args.label,
                    agent_version=args.agent_version,
                    role=args.role,
                    metadata=parse_metadata(args.metadata),
                )
                print(f"run_id={manifest['run_id']}")
                print(f"role={manifest['role']}")
                print(f"pass_rate={manifest['pass_rate']}")
                return 0
            if args.history_command == "promote":
                result = history_promote(args.run_id, history_root=args.history_root)
                print(f"champion_run_id={result['champion_run_id']}")
                return 0
            if args.history_command == "list":
                rows = history_list(args.history_root)
                for row in rows:
                    markers = " ".join(
                        filter(None, ["ANCHOR" if row["is_anchor"] else "", "CHAMPION" if row["is_champion"] else ""])
                    )
                    pass_rate = "N/A" if row["pass_rate"] is None else f"{row['pass_rate']:.3f}"
                    print(
                        f"{row['run_id']}  {row['role']:<13} {row['agent_version']:<16} "
                        f"{row['cohort_prefix']:<10} pass_rate={pass_rate}  {markers}"
                    )
                return 0
            if args.history_command == "validate":
                errors, warnings = history_validate(args.history_root)
                for warning in warnings:
                    print(f"WARNING: {warning}")
                for error in errors:
                    print(f"ERROR: {error}")
                if errors:
                    return 1
                print("VALID")
                return 0

        if args.command == "dashboard":
            dashboard = build_dashboard(args.history_root, candidate_run_id=args.candidate_run_id)
            history_root = Path(args.history_root).resolve()
            history_root.mkdir(parents=True, exist_ok=True)
            json_path = write_json(history_root / "dashboard.json", dashboard)
            html_path = render_dashboard(dashboard, args.output or (history_root / "dashboard.html"))
            print(f"dashboard_json={json_path}")
            print(f"dashboard_html={html_path}")
            integrity = dashboard.get("integrity", {})
            if not integrity.get("valid", True):
                for error in integrity.get("errors", []):
                    print(f"ERROR: {error}", file=sys.stderr)
            if args.open:
                import webbrowser

                webbrowser.open(Path(html_path).resolve().as_uri())
            return 0 if integrity.get("valid", True) else 2
    except (DataError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
