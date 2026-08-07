#!/usr/bin/env python3
"""A dependency-free reference implementation of the AIK optimizer protocol.

This is not a good optimizer. It exists so the loop can be run, tested and
demonstrated with no API key, no network and no third-party package: it reads the
failures AIK reported and appends one explicit instruction per distinct failing
evaluator. Real gains come from a reflective optimizer such as GEPA — see
`examples/optimizers/gepa_optimizer.py`.

Protocol: read one JSON payload on stdin, write one JSON object on stdout.
"""

from __future__ import annotations

import json
import sys


def build_candidates(payload: dict) -> list[dict]:
    components = payload["components"]
    budget = payload.get("budget", {})
    max_candidates = int(budget.get("max_candidates") or 1)

    failures: dict[str, str] = {}
    for item in payload["feedback"]["items"]:
        for failure in item.get("failed_evaluations", []):
            evaluator_id = str(failure.get("evaluator_id", "unknown"))
            failures.setdefault(evaluator_id, str(failure.get("reason", "")).strip())

    if not failures:
        # Nothing failed on the visible splits: proposing a rewrite anyway would be
        # churn, so return the current text unchanged and say why.
        return [
            {
                "id": "unchanged",
                "components": {component["id"]: component["text"] for component in components},
                "rationale": "No optimizer-visible case failed; no change proposed.",
            }
        ]

    candidates: list[dict] = []
    ordered = sorted(failures.items())
    for index in range(min(max_candidates, len(ordered))):
        # Candidate N addresses the first N+1 distinct failures, so the caller can
        # compare a minimal fix against a broader one.
        addressed = ordered[: index + 1]
        rules = "\n".join(f"- Do not repeat this failure ({key}): {reason}" for key, reason in addressed)
        candidates.append(
            {
                "id": f"rules-{index + 1}",
                "components": {
                    component["id"]: f"{component['text'].rstrip()}\n\nAdditional rules:\n{rules}\n"
                    for component in components
                },
                "rationale": f"Adds explicit rules for {len(addressed)} failing evaluator(s).",
                "metadata": {"addressed_evaluators": [key for key, _reason in addressed]},
            }
        )
    return candidates


def main() -> int:
    payload = json.loads(sys.stdin.read())
    if payload.get("protocol_version") != "1.0":
        print(f"Unsupported protocol version: {payload.get('protocol_version')}", file=sys.stderr)
        return 1
    response = {
        "protocol_version": "1.0",
        "candidates": build_candidates(payload),
        "metadata": {"optimizer": "rule_optimizer", "metric_calls": 0},
    }
    json.dump(response, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
