#!/usr/bin/env python3
"""Test fixture: ignores the candidate budget."""
import json
import sys

payload = json.loads(sys.stdin.read())
component = payload["components"][0]["id"]
json.dump(
    {
        "protocol_version": "1.0",
        "candidates": [{"id": f"c{index}", "components": {component: f"text {index}"}} for index in range(5)],
    },
    sys.stdout,
)
