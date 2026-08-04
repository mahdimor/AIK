#!/usr/bin/env python3
"""Deterministic offline runner for the AIK Sprint 6 repeated-trials demo.

Simulates trial-to-trial agent variability using only the case ID and the
AIK_REPETITION environment variable that `aik run --repetitions N` sets for
every observation — no LangGraph import, no randomness module, no network
call. Every repetition of every case is a pure function of
(case_id, stage, repetition), so re-running this demo always reproduces the
exact same reliability.json.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    request = json.load(sys.stdin)
    case = request["case"]
    case_id = case["id"]
    stage = request["stage"]
    repetition = int(os.environ.get("AIK_REPETITION", "0"))

    if case_id == "intermittent-billing-routing":
        if stage == "baseline":
            # The legacy first-match router genuinely misses billing on
            # about one in three repetitions — a real intermittent bug.
            agents = ["billing-agent"] if repetition % 3 != 0 else []
        else:
            # The fix is much more reliable, but this demo does not claim
            # perfection: one rare miss (repetition 5) keeps the story honest.
            agents = ["billing-agent"] if repetition != 5 else []
    else:
        agents = []

    response = {
        "output": f"{stage}:{case_id}:repetition-{repetition}",
        "agents": agents,
        "tool_calls": [],
    }
    json.dump(response, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
