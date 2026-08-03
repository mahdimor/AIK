# Runner protocol

AIK launches the command once per selected test case without a shell. The runner may call any SDK, MCP server, API, local model, or deterministic mock.

## Input

The runner receives one JSON object on standard input:

```json
{
  "protocol_version": "1.0",
  "stage": "baseline",
  "case": {
    "id": "stable-case-id",
    "description": "Expected behavior",
    "input": {},
    "expected": {},
    "tags": []
  }
}
```

AIK also sets `AIK_STAGE` and `AIK_CASE_ID` in the child environment.

## Output

Write exactly one JSON object to standard output. Write diagnostics to standard error.

```json
{
  "passed": true,
  "output": "Human-readable agent output",
  "scores": {"task_success": 1.0},
  "agents": ["orchestrator", "support-agent"],
  "tool_calls": [{"name": "create-ticket", "arguments": {}}],
  "contracts": {"confirmation-before-action": "pass"},
  "metadata": {}
}
```

`passed` is required and must be boolean. `case_id` is optional; if supplied, it must match the input case. All other fields are optional.

The project runner owns both agent execution and case evaluation. It must derive `passed` from the case expectation, an existing test, deterministic contracts, or its chosen evaluator. AIK records and compares that verdict; it does not infer correctness from free-form output.

AIK records process duration and exit code. A timeout, non-zero exit, invalid JSON, mismatched ID, or missing boolean becomes a failed result with a structured `error`. These are execution errors, not trustworthy agent failures, and the command exits non-zero after preserving the evidence.

## Safety

- Inspect runner code before executing it.
- Pass commands as arguments after `--`; AIK never invokes a shell.
- Keep credentials in the runner environment or its normal secret store, never in test artifacts.
- Redact sensitive inputs, outputs, tool arguments, standard error, and metadata before committing results.
