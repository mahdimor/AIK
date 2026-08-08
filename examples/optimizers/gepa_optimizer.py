#!/usr/bin/env python3
"""GEPA implementation of the AIK optimizer protocol.

Optional. Requires `pip install gepa` and a reflection model; AIK core never
imports this file and stays dependency-free.

GEPA searches by proposing a prompt, scoring it, reading why it failed, and
proposing again. The scoring half is AIK's job, so this adapter runs each
proposed candidate through the real runner and the configured evaluator suite,
in a throwaway copy of the case directory. The case you are optimizing is never
mutated during the search.

    python aik.py optimize improvements/my-case -- \
      python examples/optimizers/gepa_optimizer.py \
        --case-dir /abs/path/to/improvements/my-case \
        --reflection-lm openai/gpt-4.1 \
        --max-metric-calls 150 \
        --runner python /abs/path/to/my_runner.py

The runner command must use absolute paths: during the search its working
directory is the throwaway workspace, so it can read the candidate's component
files (`prompts/system.md` and friends) relative to its own cwd.

The evidence GEPA sees is whatever `aik optimize` sent on stdin, which never
contains the holdout.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from common import read_jsonl  # noqa: E402
from evaluate_results import evaluate as aik_evaluate  # noqa: E402
from execute_cases import execute as aik_execute  # noqa: E402


@dataclass
class _LocalEvaluationBatch:
    """Field-compatible stand-in used when gepa is not installed, so the adapter
    stays testable offline. When gepa is present its own dataclass is returned."""

    outputs: list[Any]
    scores: list[float]
    trajectories: list[Any] | None = None
    objective_scores: list[dict[str, float]] | None = None
    num_metric_calls: int | None = None


def evaluation_batch(**fields: Any) -> Any:
    try:
        from gepa.core.adapter import EvaluationBatch
    except ImportError:
        return _LocalEvaluationBatch(**fields)
    return EvaluationBatch(**fields)


class AikGepaAdapter:
    """Score GEPA candidates with AIK's runner and evaluator suite.

    Implements the two methods `gepa.core.adapter.GEPAAdapter` requires:
    `evaluate` and `make_reflective_dataset`.
    """

    # The protocol calls this optional, but gepa 0.1.4 reads it unconditionally
    # during reflective mutation: optional means "present and may be None", not
    # "may be absent". Omitting it makes every reflection raise AttributeError,
    # which GEPA swallows as "did not propose a new candidate" — the search then
    # runs its whole budget and returns the seed unchanged, with no error.
    propose_new_texts = None

    def __init__(
        self,
        case_dir: Path,
        component_paths: dict[str, str],
        runner_command: Sequence[str],
        *,
        timeout_seconds: float = 60,
    ) -> None:
        self.case_dir = Path(case_dir)
        self.component_paths = component_paths
        self.runner_command = list(runner_command)
        self.timeout_seconds = timeout_seconds
        self.metric_calls = 0

    def _materialize(self, workspace: Path, candidate: dict[str, str]) -> None:
        for component_id, text in candidate.items():
            relative = self.component_paths.get(component_id)
            if relative is None:
                continue
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8", newline="\n")

    def evaluate(self, batch: list[dict[str, Any]], candidate: dict[str, str], capture_traces: bool = False):
        case_ids = [str(case["id"]) for case in batch]
        with tempfile.TemporaryDirectory(prefix="aik-gepa-") as temporary:
            workspace = Path(temporary) / self.case_dir.name
            # copy2 so evaluation.json, test_cases.jsonl and the prompts travel together;
            # the original case directory is never written to during the search.
            shutil.copytree(self.case_dir, workspace)
            for stale in workspace.glob("*.jsonl"):
                if stale.name.startswith(("baseline.", "candidate.")):
                    stale.unlink()
            self._materialize(workspace, candidate)

            aik_execute(
                workspace,
                "candidate",
                self.runner_command,
                timeout_seconds=self.timeout_seconds,
                selected_ids=set(case_ids),
                overwrite=True,
                # The runner must resolve the component paths of the candidate under
                # test, which only exist in this workspace. Pass the runner command as
                # an absolute path: its own cwd is the throwaway copy, not yours.
                runner_cwd=workspace,
            )
            aik_evaluate(workspace, "candidate", (), selected_ids=set(case_ids), overwrite=True)
            evaluated = {
                str(record["case_id"]): record
                for record in read_jsonl(workspace / "candidate.evaluated.jsonl", "case_id")
            }

        outputs: list[Any] = []
        scores: list[float] = []
        trajectories: list[Any] = []
        by_id = {str(case["id"]): case for case in batch}
        for case_id in case_ids:
            record = evaluated.get(case_id, {})
            outputs.append(record.get("output", ""))
            # An unknown or manual_review verdict scores 0: it is not evidence of success.
            scores.append(1.0 if record.get("passed") is True else 0.0)
            # Carry the case itself: a reflective optimizer that cannot see the input it
            # failed on, or what was expected of it, is reasoning half-blind.
            case = by_id.get(case_id, {})
            trajectories.append({**record, "case_input": case.get("input"), "case_expected": case.get("expected")})
        self.metric_calls += len(batch)

        return evaluation_batch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories if capture_traces else None,
            num_metric_calls=len(batch),
        )

    def make_reflective_dataset(
        self, candidate: dict[str, str], eval_batch: Any, components_to_update: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Turn AIK's per-evaluator reasons into GEPA's reflective records."""
        records: list[dict[str, Any]] = []
        for trajectory in eval_batch.trajectories or []:
            failures = [
                item
                for item in trajectory.get("evaluations", [])
                if isinstance(item, dict) and item.get("status") in {"fail", "error"}
            ]
            feedback = (
                "\n".join(
                    f"[{item.get('evaluator_id', 'unknown')}] {item.get('status')}: {item.get('reason', '')}"
                    for item in failures
                )
                or "All evaluators passed."
            )
            records.append(
                {
                    "Inputs": trajectory.get("case_input", trajectory.get("case_id")),
                    "Expected": trajectory.get("case_expected"),
                    "Generated Outputs": trajectory.get("output", ""),
                    "Feedback": feedback,
                }
            )
        return {component: records for component in components_to_update}


def _seed_and_paths(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    seed = {component["id"]: component["text"] for component in payload["components"]}
    paths = {component["id"]: component["path"] for component in payload["components"]}
    return seed, paths


def _candidates_from_result(result: Any, seed: dict[str, str]) -> list[dict[str, Any]]:
    best = getattr(result, "best_candidate", None)
    if not isinstance(best, dict) or not best:
        raise SystemExit("GEPA returned no best_candidate; nothing to propose")
    components = {key: value for key, value in best.items() if key in seed and isinstance(value, str) and value.strip()}
    if not components:
        raise SystemExit("GEPA's best candidate contains none of the requested components")

    unchanged = components == {key: seed[key] for key in components}
    explored = getattr(result, "num_candidates", None)
    # GEPA catches its own reflection failures and logs "did not propose a new
    # candidate", then finishes the whole budget and returns the seed. That looks
    # identical to an honest "no improvement found", so refuse to report it as one:
    # a search that never grew its pool did not search.
    if unchanged and explored is not None and explored <= 1:
        raise SystemExit(
            "GEPA spent its budget without ever proposing a candidate (num_candidates=1). "
            "This is a broken search, not a negative result — check the reflection model "
            "on stderr. A reflection_lm given as a string needs litellm installed."
        )

    return [
        {
            "id": "gepa-best",
            "components": components,
            "rationale": "Unchanged: GEPA searched and found no improvement." if unchanged else "GEPA best candidate.",
            "metadata": {"unchanged": unchanged, "candidates_explored": explored},
        }
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="GEPA implementation of the AIK optimizer protocol")
    parser.add_argument("--reflection-lm", required=True, help="Model that reflects on failures, e.g. openai/gpt-5")
    parser.add_argument("--max-metric-calls", type=int, default=None)
    # Required, not read from the payload: the protocol carries no local path, because
    # an optimizer running anywhere else could not use one. Needing the case on disk is
    # a property of this adapter, not of the protocol.
    parser.add_argument("--case-dir", required=True, help="The case directory to score candidates against")
    parser.add_argument("--timeout", type=float, default=60, help="Per-case runner timeout")
    parser.add_argument("--runner", nargs=argparse.REMAINDER, required=True, help="Runner command, last argument")
    args = parser.parse_args()
    if not args.runner:
        parser.error("--runner requires a command")

    # GEPA logs the prompts it is reflecting on, which contain whatever the model
    # wrote. On a cp1252 console that raises UnicodeEncodeError mid-search and takes
    # the whole run down, so force UTF-8 on both streams first.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    payload = json.loads(sys.stdin.read())
    if payload.get("protocol_version") != "1.0":
        print(f"Unsupported protocol version: {payload.get('protocol_version')}", file=sys.stderr)
        return 1

    # stdout carries exactly one JSON object. AIK's execute/evaluate and GEPA's own
    # search both narrate their progress, and any of it would corrupt the response,
    # so everything printed between here and the final dump goes to stderr.
    response_stream = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        response = _search(args, payload)
    json.dump(response, response_stream, ensure_ascii=False)
    return 0


def _search(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    case_dir = Path(args.case_dir)
    seed, paths = _seed_and_paths(payload)
    visible_ids = {item["case_id"] for item in payload["feedback"]["items"]}
    cases = [case for case in read_jsonl(case_dir / "test_cases.jsonl", "id") if case["id"] in visible_ids]
    if not cases:
        raise SystemExit("No optimizer-visible cases to train on")

    budget = payload.get("budget", {})
    max_metric_calls = args.max_metric_calls or budget.get("max_metric_calls")

    try:
        import gepa
    except ImportError:
        raise SystemExit("GEPA is not installed. Run: pip install gepa") from None

    adapter = AikGepaAdapter(case_dir, paths, args.runner, timeout_seconds=args.timeout)
    result = gepa.optimize(
        seed_candidate=seed,
        trainset=cases,
        adapter=adapter,
        reflection_lm=args.reflection_lm,
        max_metric_calls=max_metric_calls,
        display_progress_bar=False,
    )

    return {
        "protocol_version": "1.0",
        "candidates": _candidates_from_result(result, seed),
        "metadata": {
            "optimizer": "gepa",
            "reflection_lm": args.reflection_lm,
            "metric_calls": getattr(result, "total_metric_calls", None) or adapter.metric_calls,
            "candidates_explored": getattr(result, "num_candidates", None),
            "trainset_cases": len(cases),
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
