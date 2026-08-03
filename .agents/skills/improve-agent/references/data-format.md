# AIK Data Format

## Test cases

Store one JSON object per line in `test_cases.jsonl`.

Required fields:

```json
{
  "id": "stable-case-id",
  "description": "Human explanation of the behavior",
  "input": {"messages": [{"role": "user", "content": "Create a support ticket"}]},
  "expected": {"required_agent": "ticket-agent", "required_tool": "create-ticket", "max_tool_calls": 1},
  "tags": ["ticket", "tool-use"]
}
```

`id` must be unique and stable across baseline and candidate runs. `input`, `expected`, and `tags` may contain project-specific fields.

## Run results

Store one JSON object per line in `baseline.jsonl` and `candidate.jsonl`.

Required fields:

```json
{"case_id": "stable-case-id", "passed": true, "output": "The ticket was created after confirmation."}
```

Optional portable fields:

```json
{
  "scores": {"task_success": 1.0, "quality": 0.9},
  "agents": ["orchestrator", "ticket-agent"],
  "tool_calls": [{"name": "create-ticket", "arguments": {}}],
  "contracts": {"confirmation-before-action": "pass", "single-side-effect": "pass"},
  "latency_ms": 1250,
  "cost": 0.004,
  "error": null,
  "execution": {"duration_ms": 1250, "exit_code": 0},
  "metadata": {}
}
```

Use `pass`, `fail`, or `not_applicable` for contract results.

Execution failures use a structured `error`, for example `{"type": "timeout", "message": "Runner exceeded 60 seconds"}`. They are preserved for diagnosis but must not be interpreted as trustworthy agent-quality failures. See `runner-protocol.md` for command integration.

## Comparison

`comparison.json` is generated. Its summary contains `total`, `improved`, `regressed`, `unchanged`, and `not_comparable`. Do not edit generated comparison data manually.
