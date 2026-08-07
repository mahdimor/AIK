#!/usr/bin/env python3
"""Test fixture: exits non-zero."""
import sys

sys.stdin.read()
print("optimizer blew up", file=sys.stderr)
raise SystemExit(3)
