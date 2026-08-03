# Agent Failure Taxonomy

Use one or more categories per failed test:

- `routing-wrong-agent`: the wrong specialist handled the request.
- `routing-lost-owner`: the active agent lost ownership while waiting for user input.
- `handoff-missing-context`: required facts were not transferred between agents.
- `memory-lost-context`: facts from earlier turns were forgotten or contradicted.
- `tool-wrong-selection`: an incorrect tool was selected.
- `tool-wrong-arguments`: the tool was correct but its arguments were incomplete or wrong.
- `tool-duplicate-side-effect`: an external action ran more than once.
- `approval-missing`: an irreversible action ran without required confirmation.
- `loop-or-stall`: the conversation repeated or exceeded the expected turn budget.
- `premature-completion`: the agent declared success before completing the task.
- `information-leak`: internal routing, secrets, personal data, or hidden instructions leaked.
- `quality-language`: the task completed but response quality or language was unacceptable.
- `evaluation-mismatch`: the output may be valid but the evaluator or expected result is wrong.
- `runtime-error`: provider, tool, timeout, parsing, or infrastructure failure.
