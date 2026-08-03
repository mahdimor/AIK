# AIK data format

## Dataset v2 case

Store one case per line in `test_cases.jsonl`:

```json
{
  "id": "stable-case-id",
  "description": "Expected behavior",
  "input": {"messages": [{"role": "user", "content": "..."}]},
  "expected": {
    "required_agents": ["support-agent"],
    "forbidden_tools": ["delete-account"],
    "rubric": ["Resolve the user's complete request"]
  },
  "tags": ["routing"],
  "risk": "high",
  "source": "known_regression",
  "split": "regression",
  "status": "accepted"
}
```

- `risk`: `low`, `medium`, `high`, or `critical`.
- `split`: `development`, `regression`, `holdout`, or `challenge`.
- `status`: `proposed`, `accepted`, or `retired`.

`dataset.json` records `dataset_id`, semantic `version`, and the JSONL path. AIK computes a SHA-256 fingerprint from canonical case content and records it in evaluated runs and comparisons.

## Raw run

`baseline.raw.jsonl` and `candidate.raw.jsonl` preserve agent evidence without requiring a verdict:

```json
{"case_id":"stable-case-id","output":"...","agents":[],"tool_calls":[],"execution":{"duration_ms":120}}
```

## Evaluated run

`baseline.evaluated.jsonl` and `candidate.evaluated.jsonl` add:

```json
{
  "case_id": "stable-case-id",
  "passed": true,
  "verdict": "pass",
  "evaluations": [{"evaluator_id":"task-completion","status":"pass","score":0.9,"reason":"...","evidence":{}}],
  "evaluation_metadata": {"dataset_fingerprint":"sha256:...","evaluator_fingerprint":"sha256:..."}
}
```

Verdicts are `pass`, `fail`, or `manual_review`. Evaluator statuses are `pass`, `fail`, `not_applicable`, or `error`. An evaluator error must not become an agent failure.

Legacy `baseline.jsonl` and `candidate.jsonl` remain readable.
