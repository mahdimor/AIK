# Behavioral Contracts

Contracts protect critical behavior independently of subjective quality scores.

Recommended contract families:

- Routing: required agent, forbidden agent, maximum handoffs.
- Tools: required tool, forbidden tool, maximum calls, required arguments.
- Approval: a user-confirmation event must precede a side effect.
- Conversation: maximum turns, no repeated question, active-agent continuity.
- Information: required facts preserved, forbidden internal data absent.
- Reliability: no runtime error, timeout, or malformed structured output.

Define expectations in the case and let `aik evaluate` infer contract outcomes from raw evidence:

```json
"expected": {"required_tools": ["confirm"], "max_tool_calls": 1}
```

Use an external evaluator for contracts that require domain or semantic judgment.
