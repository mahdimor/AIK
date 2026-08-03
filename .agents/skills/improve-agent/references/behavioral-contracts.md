# Behavioral Contracts

Contracts protect critical behavior independently of subjective quality scores.

Recommended contract families:

- Routing: required agent, forbidden agent, maximum handoffs.
- Tools: required tool, forbidden tool, maximum calls, required arguments.
- Approval: a user-confirmation event must precede a side effect.
- Conversation: maximum turns, no repeated question, active-agent continuity.
- Information: required facts preserved, forbidden internal data absent.
- Reliability: no runtime error, timeout, or malformed structured output.

Represent observed results in each run:

```json
"contracts": {"confirmation-before-action": "pass", "single-side-effect": "fail"}
```

The MVP compares recorded contract outcomes; it does not infer arbitrary contracts from raw traces.
