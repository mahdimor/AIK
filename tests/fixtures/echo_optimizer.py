#!/usr/bin/env python3
"""Test fixture: returns the payload it received as the candidate text."""
import json
import sys

payload = json.loads(sys.stdin.read())
component = payload["components"][0]["id"]
json.dump(
    {
        "protocol_version": "1.0",
        "candidates": [{"id": "echo", "components": {component: json.dumps(payload)}}],
    },
    sys.stdout,
)
