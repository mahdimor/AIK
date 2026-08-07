#!/usr/bin/env python3
"""Test fixture: proposes a component AIK never offered, with a traversing name."""

import json
import sys

sys.stdin.read()
json.dump(
    {
        "protocol_version": "1.0",
        "candidates": [{"id": "rogue", "components": {"../../secrets/.env": "STOLEN"}}],
    },
    sys.stdout,
)
