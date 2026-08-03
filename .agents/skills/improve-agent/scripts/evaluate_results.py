#!/usr/bin/env python3
"""Evaluate raw AIK run evidence with portable deterministic and external evaluators."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from common import DataError, fingerprint, index_by, read_json, read_jsonl, write_jsonl


STATUSES = {"pass", "fail", "not_applicable", "error"}
EXPECTATION_KEYS = {
    "required_agents": "required_agents",
    "forbidden_agents": "forbidden_agents",
    "required_tools": "required_tools",
    "forbidden_tools": "forbidden_tools",
    "max_tool_calls": "max_tool_calls",
    "required_tool_arguments": "required_tool_arguments",
    "output_contains": "output_contains",
    "output_not_contains": "output_not_contains",
    "json_schema_subset": "output_schema",
    "llm_rubric": "rubric",
}


def _result(evaluator: dict[str, Any], status: str, reason: str, evidence: Any = None, score: float | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "evaluator_id": evaluator["id"],
        "evaluator_version": str(evaluator.get("version", "1")),
        "kind": "semantic" if evaluator["type"] == "llm_rubric" else "deterministic",
        "status": status,
        "reason": reason,
        "evidence": evidence if evidence is not None else {},
    }
    if score is not None:
        item["score"] = round(float(score), 6)
    return item


def _expected(case: dict[str, Any], evaluator: dict[str, Any]) -> tuple[bool, Any]:
    key = evaluator.get("expectation") or EXPECTATION_KEYS.get(evaluator["type"])
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    if "value" in evaluator:
        return True, evaluator["value"]
    return (key in expected, expected.get(key)) if key else (True, None)


def _observed_names(raw: dict[str, Any], field: str) -> tuple[bool, list[str]]:
    if field not in raw:
        return False, []
    value = raw.get(field)
    if not isinstance(value, list):
        return False, []
    if field == "tool_calls":
        return True, [str(item.get("name")) for item in value if isinstance(item, dict) and item.get("name")]
    return True, [str(item) for item in value]


def _list_contract(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any], *, field: str, forbidden: bool) -> dict[str, Any]:
    applies, value = _expected(case, evaluator)
    if not applies:
        return _result(evaluator, "not_applicable", f"No {EXPECTATION_KEYS[evaluator['type']]} expectation")
    if not isinstance(value, list):
        return _result(evaluator, "error", "Expectation must be an array")
    available, observed = _observed_names(raw, field)
    if not available:
        return _result(evaluator, "error", f"Raw evidence has no valid '{field}' array")
    wanted, actual = set(map(str, value)), set(observed)
    violations = sorted(wanted & actual) if forbidden else sorted(wanted - actual)
    ok = not violations
    label = "Forbidden values observed" if forbidden else "Required values missing"
    reason = "Contract satisfied" if ok else f"{label}: {', '.join(violations)}"
    return _result(evaluator, "pass" if ok else "fail", reason, {"expected": value, "observed": observed}, 1.0 if ok else 0.0)


def _no_execution_error(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any]) -> dict[str, Any]:
    error = raw.get("error")
    ok = error in (None, {})
    return _result(evaluator, "pass" if ok else "fail", "Execution completed" if ok else "Execution error", {"error": error}, 1.0 if ok else 0.0)


def _max_tool_calls(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any]) -> dict[str, Any]:
    applies, maximum = _expected(case, evaluator)
    if not applies:
        return _result(evaluator, "not_applicable", "No max_tool_calls expectation")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        return _result(evaluator, "error", "max_tool_calls must be a non-negative integer")
    calls = raw.get("tool_calls")
    if not isinstance(calls, list):
        return _result(evaluator, "error", "Raw evidence has no valid 'tool_calls' array")
    ok = len(calls) <= maximum
    return _result(evaluator, "pass" if ok else "fail", f"Observed {len(calls)} tool calls; maximum is {maximum}", {"maximum": maximum, "actual": len(calls)}, 1.0 if ok else 0.0)


def _required_tool_arguments(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any]) -> dict[str, Any]:
    applies, required = _expected(case, evaluator)
    if not applies:
        return _result(evaluator, "not_applicable", "No required_tool_arguments expectation")
    if not isinstance(required, dict):
        return _result(evaluator, "error", "required_tool_arguments must be an object keyed by tool name")
    calls = raw.get("tool_calls")
    if not isinstance(calls, list):
        return _result(evaluator, "error", "Raw evidence has no valid 'tool_calls' array")
    missing: list[str] = []
    for tool, arguments in required.items():
        if not isinstance(arguments, dict):
            return _result(evaluator, "error", f"Required arguments for {tool} must be an object")
        candidates = [item.get("arguments", item.get("args", {})) for item in calls if isinstance(item, dict) and item.get("name") == tool]
        if not any(isinstance(actual, dict) and all(actual.get(key) == value for key, value in arguments.items()) for actual in candidates):
            missing.append(str(tool))
    ok = not missing
    return _result(evaluator, "pass" if ok else "fail", "Required tool arguments observed" if ok else f"Missing matching arguments for: {', '.join(missing)}", {"required": required}, 1.0 if ok else 0.0)


def _text_contract(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any], *, forbidden: bool) -> dict[str, Any]:
    applies, patterns = _expected(case, evaluator)
    if not applies:
        return _result(evaluator, "not_applicable", f"No {EXPECTATION_KEYS[evaluator['type']]} expectation")
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        return _result(evaluator, "error", "Text expectation must be a string or array of strings")
    output = raw.get("output")
    if not isinstance(output, str):
        return _result(evaluator, "error", "Raw evidence has no string 'output'")
    haystack = output.casefold() if evaluator.get("case_sensitive") is not True else output
    matched = [item for item in patterns if (item if evaluator.get("case_sensitive") is True else item.casefold()) in haystack]
    ok = not matched if forbidden else len(matched) == len(patterns)
    violations = matched if forbidden else [item for item in patterns if item not in matched]
    return _result(evaluator, "pass" if ok else "fail", "Text contract satisfied" if ok else f"Text contract violations: {violations}", {"patterns": patterns, "matched": matched}, 1.0 if ok else 0.0)


def _json_schema_subset(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any]) -> dict[str, Any]:
    applies, schema = _expected(case, evaluator)
    if not applies:
        return _result(evaluator, "not_applicable", "No output_schema expectation")
    if not isinstance(schema, dict):
        return _result(evaluator, "error", "output_schema must be an object")
    value = raw.get("output")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return _result(evaluator, "fail", "Output is not valid JSON", {"output": value}, 0.0)
    if not isinstance(value, dict):
        return _result(evaluator, "fail", "Output is not a JSON object", {}, 0.0)
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in value:
            errors.append(f"missing required key '{key}'")
    type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list}
    for key, definition in schema.get("properties", {}).items():
        if key in value and isinstance(definition, dict) and definition.get("type") in type_map:
            expected_type = type_map[definition["type"]]
            if not isinstance(value[key], expected_type) or (definition["type"] in {"number", "integer"} and isinstance(value[key], bool)):
                errors.append(f"key '{key}' is not {definition['type']}")
    ok = not errors
    return _result(evaluator, "pass" if ok else "fail", "JSON shape satisfied" if ok else "; ".join(errors), {"errors": errors}, 1.0 if ok else 0.0)


def _runner_verdict(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw.get("passed"), bool):
        return _result(evaluator, "not_applicable", "Runner supplied no legacy verdict")
    passed = raw["passed"]
    return _result(evaluator, "pass" if passed else "fail", "Legacy runner verdict", {"passed": passed}, 1.0 if passed else 0.0)


BUILT_INS: dict[str, Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "no_execution_error": _no_execution_error,
    "required_agents": lambda c, r, e: _list_contract(c, r, e, field="agents", forbidden=False),
    "forbidden_agents": lambda c, r, e: _list_contract(c, r, e, field="agents", forbidden=True),
    "required_tools": lambda c, r, e: _list_contract(c, r, e, field="tool_calls", forbidden=False),
    "forbidden_tools": lambda c, r, e: _list_contract(c, r, e, field="tool_calls", forbidden=True),
    "max_tool_calls": _max_tool_calls,
    "required_tool_arguments": _required_tool_arguments,
    "output_contains": lambda c, r, e: _text_contract(c, r, e, forbidden=False),
    "output_not_contains": lambda c, r, e: _text_contract(c, r, e, forbidden=True),
    "json_schema_subset": _json_schema_subset,
    "runner_verdict": _runner_verdict,
}


def _external_evaluation(case: dict[str, Any], raw: dict[str, Any], evaluator: dict[str, Any], command: Sequence[str], timeout: float) -> dict[str, Any]:
    applies, rubric = _expected(case, evaluator)
    if not applies or not rubric:
        return _result(evaluator, "not_applicable", "No rubric expectation")
    if not command:
        if evaluator.get("required", False):
            return _result(evaluator, "error", "A judge command is required for this evaluator")
        return _result(evaluator, "not_applicable", "Optional semantic evaluator skipped; no judge command supplied")
    payload = {
        "protocol_version": "1.0",
        "evaluator": evaluator,
        "case": case,
        "result": raw,
        "rubric": rubric,
    }
    started = time.monotonic()
    try:
        completed = subprocess.run(list(command), input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False, check=False, env=os.environ.copy())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _result(evaluator, "error", f"Judge execution failed: {exc}")
    duration = round((time.monotonic() - started) * 1000)
    if completed.returncode:
        return _result(evaluator, "error", f"Judge exited with {completed.returncode}: {completed.stderr.strip()[-1000:]}", {"duration_ms": duration})
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return _result(evaluator, "error", f"Judge returned invalid JSON: {exc.msg}", {"duration_ms": duration})
    if not isinstance(response, dict) or response.get("status") not in STATUSES:
        return _result(evaluator, "error", "Judge response requires a valid status", {"duration_ms": duration})
    score = response.get("score")
    if score is not None and (not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= float(score) <= 1):
        return _result(evaluator, "error", "Judge score must be between 0 and 1", {"duration_ms": duration})
    threshold = float(evaluator.get("threshold", 0.7))
    status = response["status"]
    if status in {"pass", "fail"} and score is not None:
        status = "pass" if float(score) >= threshold else "fail"
    item = _result(evaluator, status, str(response.get("reason", "External judge result")), response.get("evidence", {}), float(score) if score is not None else None)
    item["metadata"] = {**(response.get("metadata") if isinstance(response.get("metadata"), dict) else {}), "duration_ms": duration}
    return item


def _validate_suite(suite: dict[str, Any]) -> list[dict[str, Any]]:
    evaluators = suite.get("evaluators")
    if not isinstance(evaluators, list) or not evaluators:
        raise DataError("evaluation.json requires a non-empty 'evaluators' array")
    seen: set[str] = set()
    for index, evaluator in enumerate(evaluators):
        if not isinstance(evaluator, dict) or not isinstance(evaluator.get("id"), str) or not evaluator["id"]:
            raise DataError(f"evaluation.json evaluators[{index}] requires a non-empty id")
        if evaluator["id"] in seen:
            raise DataError(f"Duplicate evaluator id: {evaluator['id']}")
        seen.add(evaluator["id"])
        if evaluator.get("type") not in {*BUILT_INS, "llm_rubric"}:
            raise DataError(f"Unknown evaluator type: {evaluator.get('type')}")
    return evaluators


def evaluate(
    case_dir: str | Path,
    stage: str,
    judge_command: Sequence[str] = (),
    *,
    timeout_seconds: float = 60,
    selected_ids: set[str] | None = None,
    selected_tags: set[str] | None = None,
    resume: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    if stage not in {"baseline", "candidate"}:
        raise DataError("Stage must be baseline or candidate")
    directory = Path(case_dir).resolve()
    cases = read_jsonl(directory / "test_cases.jsonl", "id")
    known_ids = {case["id"] for case in cases}
    unknown_ids = (selected_ids or set()) - known_ids
    if unknown_ids:
        raise DataError(f"Unknown selected case IDs: {', '.join(sorted(unknown_ids))}")
    selected_cases = []
    for case in cases:
        if selected_ids and case["id"] not in selected_ids:
            continue
        tags = set(case.get("tags", [])) if isinstance(case.get("tags"), list) else set()
        if selected_tags and not (tags & selected_tags):
            continue
        selected_cases.append(case)
    if not selected_cases:
        raise DataError("No test cases matched the selected IDs and tags")
    if resume and overwrite:
        raise DataError("Use either resume or overwrite, not both")
    suite = read_json(directory / "evaluation.json")
    evaluators = _validate_suite(suite)
    raw_path = directory / f"{stage}.raw.jsonl"
    if not raw_path.exists():
        raw_path = directory / f"{stage}.jsonl"
    raw_records = read_jsonl(raw_path, "case_id")
    raw_index = index_by(raw_records, "case_id")
    destination = directory / f"{stage}.evaluated.jsonl"
    if destination.exists() and destination.stat().st_size and not (overwrite or resume):
        raise DataError(f"{destination.name} already contains results; use --overwrite")
    dataset_fp = fingerprint(cases)
    evaluator_fp = fingerprint({"evaluators": evaluators, "acceptance": suite.get("acceptance", {})})
    evaluated = read_jsonl(destination, "case_id") if resume and destination.exists() else []
    evaluated_index = index_by(evaluated, "case_id")
    errors = failed = passed = manual = 0
    skipped = 0
    for case in selected_cases:
        if resume and case["id"] in evaluated_index:
            skipped += 1
            continue
        raw = raw_index.get(case["id"])
        if raw is None:
            evaluations = [_result({"id": "execution-present", "type": "no_execution_error"}, "error", "Missing raw result")]
            output: dict[str, Any] = {"case_id": case["id"], "output": ""}
        else:
            evaluations = []
            for evaluator in evaluators:
                if evaluator.get("enabled", True) is False:
                    continue
                if evaluator["type"] == "llm_rubric":
                    evaluation = _external_evaluation(case, raw, evaluator, judge_command, timeout_seconds)
                else:
                    evaluation = BUILT_INS[evaluator["type"]](case, raw, evaluator)
                evaluations.append(evaluation)
            output = dict(raw)
        required_by_id = {item["id"]: item.get("required", True) for item in evaluators}
        required_results = [item for item in evaluations if required_by_id.get(item["evaluator_id"], True) and item["status"] != "not_applicable"]
        if any(item["status"] == "error" for item in required_results):
            verdict, boolean = "manual_review", None
            manual += 1
        elif any(item["status"] == "fail" for item in required_results):
            verdict, boolean = "fail", False
            failed += 1
        elif required_results and all(item["status"] == "pass" for item in required_results):
            verdict, boolean = "pass", True
            passed += 1
        else:
            verdict, boolean = "manual_review", None
            manual += 1
        errors += sum(item["status"] == "error" for item in evaluations)
        output.update({
            "case_id": case["id"],
            "passed": boolean,
            "verdict": verdict,
            "evaluations": evaluations,
            "scores": {item["evaluator_id"]: item["score"] for item in evaluations if "score" in item},
            "contracts": {item["evaluator_id"]: item["status"] for item in evaluations if item["kind"] == "deterministic"},
            "evaluation_metadata": {"stage": stage, "dataset_fingerprint": dataset_fp, "evaluator_fingerprint": evaluator_fp},
        })
        evaluated_index[case["id"]] = output
        print(f"{case['id']}: {verdict.upper()}")
    evaluated = [evaluated_index[key] for key in sorted(evaluated_index)]
    write_jsonl(destination, evaluated)
    return {"destination": destination, "total": len(evaluated), "selected": len(selected_cases), "executed": len(selected_cases) - skipped, "skipped": skipped, "passed": passed, "failed": failed, "manual_review": manual, "evaluator_errors": errors, "dataset_fingerprint": dataset_fp, "evaluator_fingerprint": evaluator_fp}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate AIK raw run evidence")
    parser.add_argument("case_dir")
    parser.add_argument("--stage", required=True, choices=("baseline", "candidate"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--id", action="append", dest="ids")
    parser.add_argument("--tag", action="append", dest="tags")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--overwrite", action="store_true")
    raw_arguments = sys.argv[1:]
    command: list[str] = []
    if "--" in raw_arguments:
        separator = raw_arguments.index("--")
        command = raw_arguments[separator + 1:]
        raw_arguments = raw_arguments[:separator]
    args = parser.parse_args(raw_arguments)
    try:
        summary = evaluate(
            args.case_dir,
            args.stage,
            command,
            timeout_seconds=args.timeout,
            selected_ids=set(args.ids) if args.ids else None,
            selected_tags=set(args.tags) if args.tags else None,
            resume=args.resume,
            overwrite=args.overwrite,
        )
    except (DataError, ValueError) as exc:
        parser.error(str(exc))
    print(" ".join(f"{key}={summary[key]}" for key in ("passed", "failed", "manual_review", "evaluator_errors")))
    print(summary["destination"])
    return 1 if summary["evaluator_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
