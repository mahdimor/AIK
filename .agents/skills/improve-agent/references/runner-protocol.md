# Runner protocol 1.2

AIK launches the command once per case without a shell. The runner may use any SDK, MCP server, API, local model, or deterministic mock.

## Input

```json
{
  "protocol_version": "1.2",
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
  "cost": {"total_usd": 0.0021, "input_tokens": 1840, "output_tokens": 260, "model": "..."},
  "metadata": {}
}
```

`passed` is optional for protocol 1.1 and later. Evaluation belongs to `aik evaluate`, not the runner. Protocol 1.0 runners may still return `passed`, `scores`, and `contracts`; AIK records them and the optional `runner_verdict` evaluator can use the legacy verdict.

Timeouts, non-zero exits, invalid JSON, and mismatched IDs become structured execution errors. They are evidence failures, not trustworthy agent-quality failures.

## Cost and latency

`cost` is optional and only the runner can report it, because only the runner knows
which provider and model ran. `cost.total_usd` must be a non-negative number and is
the only field `aik pareto` reads; the others are recorded for review. Report the
real charge for that one case, not an estimate for a batch.

Latency needs nothing from the runner: AIK records wall-clock `execution.duration_ms`
around the subprocess itself. It therefore includes the runner's own startup, which
is honest for comparing variants launched the same way and misleading for comparing
against a number measured inside an application.

A case with no `cost` is treated as unpriced, never as free. A variant with any
unpriced case cannot be ranked on price. Read `references/objectives-and-pareto.md`.

## Safety

- Inspect runner code before execution.
- Keep credentials in the runner environment or secret store.
- Redact sensitive inputs, outputs, arguments, stderr, and metadata.
- Pass commands after `--`; AIK never invokes a shell.
