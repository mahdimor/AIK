#!/usr/bin/env python3
"""Test fixture for AIK's subprocess protocol."""

from __future__ import annotations

import json
import sys
import time


request = json.load(sys.stdin)
case = request["case"]
mode = case.get("input", {}).get("mode", "normal")
if mode == "timeout":
    time.sleep(2)
elif mode == "invalid-json":
    print("not json")
elif mode == "exit-error":
    print("runner failed", file=sys.stderr)
    raise SystemExit(7)
else:
    json.dump(
        {
            "passed": request["stage"] == "candidate",
            "output": f"{request['stage']}:{case['id']}",
            "agents": ["fixture-agent"],
            "metadata": {"protocol": request["protocol_version"]},
        },
        sys.stdout,
    )
