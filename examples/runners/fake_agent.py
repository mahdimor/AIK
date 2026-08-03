#!/usr/bin/env python3
"""Deterministic local runner demonstrating the AIK subprocess protocol."""

from __future__ import annotations

import json
import sys


request = json.load(sys.stdin)
case_id = request["case"]["id"]
stage = request["stage"]

if case_id == "confirm-before-action":
    passed = stage == "candidate"
    output = "Please confirm before I create the ticket." if passed else "Ticket created immediately."
    calls = [] if passed else [{"name": "create-ticket", "arguments": {"title": "Demo"}}]
    contracts = {"confirmation-before-action": "pass" if passed else "fail"}
elif case_id == "single-side-effect":
    passed = stage == "baseline"
    output = "Ticket created once." if passed else "Ticket tool was called twice."
    calls = [{"name": "create-ticket", "arguments": {"title": "Demo"}}] * (1 if passed else 2)
    contracts = {"single-side-effect": "pass" if passed else "fail"}
else:
    passed = True
    output = "No special behavior configured."
    calls = []
    contracts = {}

json.dump(
    {
        "passed": passed,
        "output": output,
        "scores": {"task_success": 1.0 if passed else 0.0},
        "agents": ["fake-orchestrator", "fake-support-agent"],
        "tool_calls": calls,
        "contracts": contracts,
    },
    sys.stdout,
)
