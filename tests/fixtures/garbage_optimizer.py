#!/usr/bin/env python3
"""Test fixture: writes something that is not JSON."""
import sys

sys.stdin.read()
sys.stdout.write("not json at all")
