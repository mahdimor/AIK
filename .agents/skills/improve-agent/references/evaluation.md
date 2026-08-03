# Evaluation and acceptance

Define `evaluation.json` before baseline and candidate runs.

## Built-in evaluator types

- `no_execution_error`
- `required_agents`
- `forbidden_agents`
- `required_tools`
- `forbidden_tools`
- `max_tool_calls`
- `required_tool_arguments`
- `output_contains`
- `output_not_contains`
- `json_schema_subset`
- `runner_verdict` for legacy runners

Most built-ins read a same-named value from the case `expected` object. Use `expectation` to select a different expected field or `value` for a suite-wide constant.

## Semantic evaluation

Use `llm_rubric` for task completion, answer relevance, correctness, groundedness, completeness, or predicted satisfaction:

```json
{"id":"task-completion","type":"llm_rubric","required":true,"threshold":0.8}
```

Pass a judge command to `aik evaluate`. AIK sends the case, raw result, evaluator definition, and rubric over stdin. The judge returns:

```json
{"status":"pass","score":0.9,"reason":"...","evidence":["..."],"metadata":{"provider":"...","model":"..."}}
```

The generic protocol keeps AIK provider agnostic. `scripts/openai_judge.py` is an optional implementation using the OpenAI Responses API and structured output. Set `OPENAI_API_KEY`; override `AIK_JUDGE_MODEL` if needed.

## Acceptance

Policy recommendations are:

- `eligible`: all gates pass; human approval is still required;
- `rejected`: a protected rule, regression, or threshold fails;
- `manual_review`: evidence is missing, incompatible, or an evaluator errors.

Useful gates include `maximum_regressions`, `no_regression_at_or_above_risk`, `critical_cases_must_pass`, `minimum_candidate_pass_rate`, and `minimum_delta`.

Never treat an aggregate improvement as permission to ignore a regression. Never describe predicted satisfaction from a judge as actual user satisfaction.
