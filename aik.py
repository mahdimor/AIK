#!/usr/bin/env python3
"""Small dependency-free command line interface for Agent Improvement Kit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


VERSION = "0.1.0"
ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from common import DataError  # noqa: E402
from init_case import initialize  # noqa: E402
from promote_tests import promote  # noqa: E402
from run_review import run  # noqa: E402
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
    args = parser.parse_args(argv)
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

        if args.command in {"review", "demo"}:
            case_dir = args.case_dir
            comparison_path, review_path, result = run(case_dir)
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
