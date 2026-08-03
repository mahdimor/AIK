#!/usr/bin/env python3
"""Small dependency-free command line interface for Agent Improvement Kit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


VERSION = "0.3.0"
ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from common import DataError  # noqa: E402
from execute_cases import execute  # noqa: E402
from init_case import initialize  # noqa: E402
from promote_tests import promote  # noqa: E402
from run_review import run as build_review  # noqa: E402
from validate_case import validate  # noqa: E402


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    runner_command: list[str] = []
    if raw_arguments[:1] == ["run"] and "--" in raw_arguments:
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
            )
            print(" ".join(f"{key}={summary[key]}" for key in ("executed", "skipped", "execution_errors")))
            print(f"results={summary['destination']}")
            return 1 if summary["execution_errors"] else 0

        if args.command in {"review", "demo"}:
            case_dir = args.case_dir
            comparison_path, review_path, result = build_review(case_dir)
            _print_summary(result)
            print(f"comparison={comparison_path}")
            print(f"review={review_path}")
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
    except (DataError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
