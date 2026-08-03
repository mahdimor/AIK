#!/usr/bin/env python3
"""Offline fixture implementing AIK's semantic evaluator protocol."""

from __future__ import annotations

import json
import sys


request = json.load(sys.stdin)
output = str(request["result"].get("output", ""))
expected = request["case"].get("expected", {})
required = [str(item).casefold() for item in expected.get("semantic_keywords", [])]
matched = [item for item in required if item in output.casefold()]
score = len(matched) / len(required) if required else 1.0
json.dump(
    {
        "status": "pass" if score >= float(request["evaluator"].get("threshold", 0.7)) else "fail",
        "score": score,
        "reason": f"Matched {len(matched)} of {len(required)} semantic requirements",
        "evidence": matched,
        "metadata": {"provider": "offline-fixture", "model": "none"},
    },
    sys.stdout,
)
