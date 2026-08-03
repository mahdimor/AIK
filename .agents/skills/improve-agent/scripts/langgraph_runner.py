#!/usr/bin/env python3
"""Adapt a LangGraph-compatible runnable to the AIK raw runner protocol."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import io
import json
import sys
from pathlib import Path
from typing import Any


def load_target(reference: str) -> Any:
    if ":" not in reference:
        raise ValueError("Target must use module:attribute syntax")
    module_name, attribute_path = reference.split(":", 1)
    target: Any = importlib.import_module(module_name)
    for part in attribute_path.split("."):
        target = getattr(target, part)
    return target


def build_runnable(target: Any, stage: str) -> Any:
    if hasattr(target, "invoke"):
        return target
    if not callable(target):
        raise TypeError("Graph target must expose invoke() or be a factory")
    signature = inspect.signature(target)
    runnable = target(stage=stage) if "stage" in signature.parameters else target()
    if not hasattr(runnable, "invoke"):
        raise TypeError("Graph factory must return an object exposing invoke()")
    return runnable


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one AIK case through a LangGraph-compatible object")
    parser.add_argument("--graph", required=True, help="Runnable or factory as module:attribute")
    parser.add_argument("--transform", help="Optional result transform as module:attribute")
    parser.add_argument("--evaluator", help="Deprecated alias for --transform")
    args = parser.parse_args()

    sys.path.insert(0, str(Path.cwd()))
    request = json.load(sys.stdin)
    case = request["case"]
    stage = request["stage"]
    graph_target = load_target(args.graph)
    transform_reference = args.transform or args.evaluator
    transform = load_target(transform_reference) if transform_reference else None
    runnable = build_runnable(graph_target, stage)

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = runnable.invoke(case["input"])
        evidence = transform(case=case, result=result, stage=stage) if transform else result
    diagnostics = captured.getvalue()
    if diagnostics:
        print(diagnostics, file=sys.stderr, end="")
    if not isinstance(evidence, dict):
        raise TypeError("Graph result transform must return an object")
    json.dump(evidence, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
