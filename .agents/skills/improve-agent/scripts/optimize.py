#!/usr/bin/env python3
"""Drive an external optimizer over AIK evidence and store the candidates it proposes.

AIK does not optimize anything itself. It owns the two ends that must not be
negotiable: what evidence an optimizer is allowed to see, and what it is allowed
to write back. Between those, any process that speaks the optimizer protocol will
do — a local Python adapter, a service, another language entirely.

The feedback payload is always produced by `export_feedback`, never supplied by
the caller, so an optimizer reached through this command cannot be handed the
holdout.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from common import DataError, fingerprint, read_json, read_jsonl, write_json
from export_feedback import LEDGER_NAME, _append_ledger, _read_ledger, export_feedback, holdout_seal


PROTOCOL_VERSION = "1.0"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULT_MAX_CANDIDATES = 4


def validate_optimization_config(config: Any) -> dict[str, Any]:
    """Validate the optional `optimization` section of evaluation.json."""
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise DataError("evaluation.json 'optimization' must be an object")
    unknown = sorted(set(config) - {"components", "max_candidates", "max_metric_calls"})
    if unknown:
        raise DataError(f"Unknown optimization key(s): {', '.join(unknown)}")

    components = config.get("components")
    if not isinstance(components, list) or not components:
        raise DataError("optimization 'components' must be a non-empty list")
    seen: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise DataError("Each optimization component must be an object")
        component_id = component.get("id")
        if not isinstance(component_id, str) or not SAFE_ID.match(component_id):
            raise DataError(f"Invalid optimization component id: {component_id!r}")
        if component_id in seen:
            raise DataError(f"Duplicate optimization component id: {component_id}")
        seen.add(component_id)
        if not isinstance(component.get("path"), str) or not component["path"].strip():
            raise DataError(f"Optimization component '{component_id}' requires a path")

    for key in ("max_candidates", "max_metric_calls"):
        if key in config and (isinstance(config[key], bool) or not isinstance(config[key], int) or config[key] < 1):
            raise DataError(f"optimization '{key}' must be a positive integer")
    return config


def _resolve_component_path(directory: Path, relative: str) -> Path:
    """Resolve a component path and refuse to leave the case directory.

    A case must stay self-contained and reproducible, so an optimizable prompt
    lives inside it. This also means a misconfigured path cannot make AIK read or
    overwrite something elsewhere on disk.
    """
    root = directory.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise DataError(f"Optimization component path escapes the case directory: {relative}")
    return candidate


def load_components(directory: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for component in config["components"]:
        path = _resolve_component_path(directory, component["path"])
        if not path.is_file():
            raise DataError(f"Optimization component '{component['id']}' not found: {path}")
        components.append(
            {
                "id": component["id"],
                "path": component["path"],
                "text": path.read_text(encoding="utf-8"),
            }
        )
    return components


def _validate_response(payload: Any, component_ids: set[str], max_candidates: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise DataError("Optimizer response must be one JSON object")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise DataError("Optimizer response requires a non-empty 'candidates' list")
    if len(candidates) > max_candidates:
        raise DataError(
            f"Optimizer returned {len(candidates)} candidates; the configured budget is {max_candidates}"
        )

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise DataError(f"Candidate {index} must be an object")
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not SAFE_ID.match(candidate_id):
            raise DataError(f"Candidate {index} has an invalid id: {candidate_id!r}")
        if candidate_id in seen:
            raise DataError(f"Duplicate candidate id: {candidate_id}")
        seen.add(candidate_id)

        components = candidate.get("components")
        if not isinstance(components, dict) or not components:
            raise DataError(f"Candidate '{candidate_id}' requires a non-empty 'components' object")
        unknown = sorted(set(components) - component_ids)
        if unknown:
            # An optimizer may only rewrite what it was given. Inventing a component
            # would write a file nobody asked for.
            raise DataError(f"Candidate '{candidate_id}' proposes unknown component(s): {', '.join(unknown)}")
        for component_id, text in components.items():
            if not isinstance(text, str) or not text.strip():
                raise DataError(f"Candidate '{candidate_id}' component '{component_id}' must be non-empty text")

        validated.append(
            {
                "id": candidate_id,
                "components": components,
                "rationale": candidate.get("rationale"),
                "metadata": candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {},
            }
        )
    return validated


def optimize(
    case_dir: str | Path,
    command: Sequence[str],
    *,
    stage: str = "candidate",
    timeout_seconds: float = 900,
    overwrite: bool = False,
) -> dict[str, Any]:
    directory = Path(case_dir)
    if not directory.is_dir():
        raise DataError(f"Case directory not found: {directory}")
    if not command:
        raise DataError("Optimizer command is required after '--'")
    if timeout_seconds <= 0:
        raise DataError("Timeout must be greater than zero")

    suite = read_json(directory / "evaluation.json")
    config = validate_optimization_config(suite.get("optimization"))
    if not config:
        raise DataError(
            "evaluation.json has no 'optimization' section: declare which components an "
            "optimizer may rewrite before running one"
        )
    components = load_components(directory, config)
    max_candidates = int(config.get("max_candidates", DEFAULT_MAX_CANDIDATES))

    destination = directory / "optimization"
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise DataError(f"{destination} already contains a proposal; use --overwrite")

    # The optimizer never chooses its own evidence: this call is the only source,
    # and it cannot emit the holdout.
    feedback = export_feedback(directory, stage=stage, record_export=False)
    cases = read_jsonl(directory / "test_cases.jsonl", "id")
    full_dataset_fingerprint = fingerprint(cases)
    sealed_holdout = holdout_seal(cases)

    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "stage": stage,
        "objective": {"metric": "pass_rate", "direction": "maximize"},
        "budget": {
            "max_candidates": max_candidates,
            "max_metric_calls": config.get("max_metric_calls"),
        },
        "components": components,
        "feedback": {
            "summary": feedback["summary"],
            "dataset_fingerprint": feedback["dataset_fingerprint"],
            "items": feedback["items"],
        },
    }

    try:
        completed = subprocess.run(
            list(command),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DataError(f"Optimizer exceeded {timeout_seconds:g} seconds") from exc
    except OSError as exc:
        raise DataError(f"Optimizer could not be started: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip()[-4000:] or f"exit code {completed.returncode}"
        raise DataError(f"Optimizer failed: {detail}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DataError(f"Optimizer stdout is not one JSON object: {exc.msg}") from exc

    component_ids = {component["id"] for component in components}
    candidates = _validate_response(response, component_ids, max_candidates)

    for candidate in candidates:
        candidate_dir = destination / "candidates" / candidate["id"]
        candidate_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        for component_id, text in candidate["components"].items():
            path = candidate_dir / f"{component_id}.txt"
            path.write_text(text, encoding="utf-8", newline="\n")
            written[component_id] = str(path.relative_to(directory).as_posix())
        candidate["files"] = written
        candidate.pop("components")

    generated_at = datetime.now(timezone.utc).isoformat()
    proposal = {
        "format_version": "1.0",
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": generated_at,
        "case_dir": str(directory),
        "stage": stage,
        "command": list(command),
        "dataset_fingerprint": full_dataset_fingerprint,
        "holdout": {"count": sealed_holdout["count"], "fingerprint": sealed_holdout["fingerprint"]},
        "visible_cases": feedback["summary"]["visible_cases"],
        "budget": payload["budget"],
        "components": [{"id": item["id"], "path": item["path"]} for item in components],
        "optimizer_metadata": response.get("metadata") if isinstance(response.get("metadata"), dict) else {},
        "candidates": candidates,
        # Proposals are text on disk. Nothing is evaluated, compared or promoted here.
        "next_step": "Run each candidate through aik run/evaluate, then aik pareto or aik review.",
    }
    proposal_path = write_json(destination / "proposal.json", proposal)

    ledger_path = directory / LEDGER_NAME
    _append_ledger(
        ledger_path,
        {
            "exported_at": generated_at,
            "export_number": len(_read_ledger(ledger_path)) + 1,
            "kind": "optimize",
            "stage": stage,
            "visible_cases": feedback["summary"]["visible_cases"],
            "dataset_fingerprint": full_dataset_fingerprint,
            "holdout": {"count": sealed_holdout["count"], "fingerprint": sealed_holdout["fingerprint"]},
            "candidates": len(candidates),
        },
    )

    proposal["destination"] = str(proposal_path)
    return proposal


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Propose candidates with an external optimizer",
        epilog="Place the optimizer command after --, for example: -- python my_optimizer.py",
    )
    parser.add_argument("case_dir")
    parser.add_argument("--stage", default="candidate", choices=("baseline", "candidate"))
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--overwrite", action="store_true")
    raw_arguments = sys.argv[1:]
    command: list[str] = []
    if "--" in raw_arguments:
        separator = raw_arguments.index("--")
        command = raw_arguments[separator + 1 :]
        raw_arguments = raw_arguments[:separator]
    args = parser.parse_args(raw_arguments)
    try:
        proposal = optimize(
            args.case_dir, command, stage=args.stage, timeout_seconds=args.timeout, overwrite=args.overwrite
        )
    except DataError as exc:
        parser.error(str(exc))
    print(f"candidates={len(proposal['candidates'])}")
    for candidate in proposal["candidates"]:
        print(f"{candidate['id']}: {', '.join(sorted(candidate['files'].values()))}")
    print(f"proposal={proposal['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
