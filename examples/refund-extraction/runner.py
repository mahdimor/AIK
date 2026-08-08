#!/usr/bin/env python3
"""AIK runner: answers one case with gpt-4.1-nano, using the prompt under test.

Reads prompts/system.md relative to its cwd, which AIK sets to the workspace the
candidate was materialized into.
"""
import json, os, sys, urllib.request
from pathlib import Path

payload = json.loads(sys.stdin.read())
case = payload["case"]
system = Path("prompts/system.md").read_text(encoding="utf-8")

body = json.dumps({
    "model": "gpt-4.1-nano",
    "temperature": 0,
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": case["input"]["request"]},
    ],
}).encode()
req = urllib.request.Request(
    "https://api.openai.com/v1/chat/completions",
    data=body,
    headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=60) as response:
    data = json.load(response)

usage = data.get("usage", {})
# gpt-4.1-nano list price, USD per token
cost = usage.get("prompt_tokens", 0) * 0.10e-6 + usage.get("completion_tokens", 0) * 0.40e-6
json.dump({
    "output": data["choices"][0]["message"]["content"],
    "cost": {"total_usd": round(cost, 8),
             "input_tokens": usage.get("prompt_tokens"),
             "output_tokens": usage.get("completion_tokens"),
             "model": "gpt-4.1-nano"},
}, sys.stdout)
