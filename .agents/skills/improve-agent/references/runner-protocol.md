# Runner protocol 1.1

AIK launches the command once per case without a shell. The runner may use any SDK, MCP server, API, local model, or deterministic mock.

## Input

```json
{
  "protocol_version": "1.1",
  "stage": "candidate",
  "case": {"id": "stable-id", "input": {}, "expected": {}, "tags": []}
}
```

AIK also sets `AIK_STAGE` and `AIK_CASE_ID`.

## Raw output

Write exactly one JSON object to stdout and diagnostics to stderr:

```json
{
  "output": "Human-readable answer",
  "agents": ["orchestrator", "support-agent"],
  "tool_calls": [{"name": "create-ticket", "arguments": {}}],
  "metadata": {}
}
```

`passed` is optional for protocol 1.1. Evaluation belongs to `aik evaluate`, not the runner. Protocol 1.0 runners may still return `passed`, `scores`, and `contracts`; AIK records them and the optional `runner_verdict` evaluator can use the legacy verdict.

Timeouts, non-zero exits, invalid JSON, and mismatched IDs become structured execution errors. They are evidence failures, not trustworthy agent-quality failures.

## Safety

- Inspect runner code before execution.
- Keep credentials in the runner environment or secret store.
- Redact sensitive inputs, outputs, arguments, stderr, and metadata.
- Pass commands after `--`; AIK never invokes a shell.
