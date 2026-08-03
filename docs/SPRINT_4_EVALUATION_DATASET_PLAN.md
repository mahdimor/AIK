# Sprint 4 — Evaluation Engine and Dataset v2

## Outcome

At the end of this sprint, AIK can run an agent, evaluate its raw evidence independently, and decide whether a candidate clears a policy defined before the run.

The sprint establishes trustworthy measurements. Longitudinal history and the dashboard are intentionally deferred to Sprint 5, which will consume the artifacts defined here.

## Product rule

```text
Case + expectations + evaluator definitions + acceptance policy
                              ↓
                       Agent execution
                              ↓
                         Raw evidence
                              ↓
                    Versioned evaluation
                              ↓
              Pass / fail / manual review decision
                              ↓
                    Baseline/candidate comparison
```

An aggregate improvement must never hide a critical regression. Evaluation and acceptance are separate:

- an **evaluator** measures one behavior and produces evidence;
- a **case verdict** combines evaluator results for one case;
- an **acceptance policy** determines whether the complete candidate can replace the current champion;
- a **human decision** remains required for promotion.

## Architecture decisions

### AIK remains framework and vendor agnostic

The core remains Python 3.10+ with no third-party runtime dependency.

- LangGraph is only the first executable demonstration.
- OpenAI can be used by an optional external semantic evaluator.
- Promptfoo is not required. A future adapter may import or export its results.
- Langfuse is not required. A future adapter may export runs, traces, and scores to it.
- GEPA is deferred until AIK has stable evaluator feedback and dataset splits.

### Execution no longer owns evaluation

Runner protocol 1.1 makes `passed` optional. A runner returns raw output and observable evidence:

```json
{
  "output": "I handled billing and technical support.",
  "agents": ["billing-agent", "technical-agent"],
  "tool_calls": [],
  "metadata": {}
}
```

Existing protocol 1.0 runners that return `passed`, `scores`, and `contracts` continue to work. Their verdict is recorded as a legacy `runner_verdict` evaluator; AIK does not silently discard it.

### Evaluators are portable records

Every evaluator result uses a common schema:

```json
{
  "evaluator_id": "routing-required-agents",
  "evaluator_version": "1",
  "kind": "deterministic",
  "status": "pass",
  "score": 1.0,
  "reason": "All required agents were observed",
  "evidence": {"required": ["billing-agent"], "observed": ["billing-agent"]}
}
```

Allowed statuses are `pass`, `fail`, `not_applicable`, and `error`. Evaluator errors are never treated as agent failures; they make the case non-comparable or require manual review.

### Semantic evaluators use the same subprocess boundary

AIK will define a JSON-over-stdin/stdout evaluator protocol. This lets a project use OpenAI, another model provider, Promptfoo, Langfuse-managed scores, a local model, or a custom service without importing its SDK into AIK.

The request contains the case, raw result, rubric, and evaluator identity. The response contains status, numeric score, reason, and evidence. Model, prompt/rubric version, and provider metadata must be preserved when relevant.

Sprint 4 delivers the protocol, a deterministic fixture, and an optional real OpenAI judge. The offline demo must not require an API key.

## Dataset v2

### Case schema

Each JSONL case keeps a stable `id` and adds explicit governance fields:

```json
{
  "id": "mixed-request-001",
  "description": "Route a mixed request to both specialists",
  "input": {"messages": [{"role": "user", "content": "..."}]},
  "expected": {
    "required_agents": ["billing-agent", "technical-agent"],
    "forbidden_agents": [],
    "rubric": ["Resolve both user intents"]
  },
  "tags": ["routing", "multi-agent"],
  "risk": "high",
  "source": "known_regression",
  "split": "regression",
  "status": "accepted"
}
```

Required v2 fields are `id`, `description`, `input`, `expected`, `tags`, `risk`, `source`, `split`, and `status`.

Controlled values:

- `risk`: `low`, `medium`, `high`, `critical`;
- `split`: `development`, `regression`, `holdout`, `challenge`;
- `status`: `proposed`, `accepted`, `retired`.

### Dataset manifest

`dataset.json` describes the suite rather than duplicating case content:

```json
{
  "format_version": "2.0",
  "dataset_id": "langgraph-support-demo",
  "version": "1.0.0",
  "cases": "test_cases.jsonl",
  "fingerprint": "sha256:...",
  "created_at": "...",
  "counts": {"development": 2, "regression": 2, "holdout": 1, "challenge": 1}
}
```

The fingerprint is computed from canonical case content. Editing a case changes the fingerprint. A comparison records the dataset fingerprint and refuses to claim a valid trend when fingerprints or evaluator suites are incompatible.

### Dataset construction workflow

1. Capture a real failure, risk, or desired capability.
2. Redact sensitive data and assign a stable source category.
3. Write the expected behavioral contracts before changing the agent.
4. Add boundary, negative, and paraphrased variants when they cover a distinct failure mode.
5. Review the case and move it from `proposed` to `accepted`.
6. Put iteration cases in `development`; put fixed incidents in `regression`.
7. Keep `holdout` cases out of optimizer/developer feedback.
8. Use `challenge` for rare, adversarial, or critical behavior.

Dataset quality in Sprint 4 is validated through actionable checks:

- duplicate IDs;
- exact duplicate inputs;
- missing risk, source, split, or status;
- empty expectations;
- no accepted cases in a required split;
- tags with no coverage;
- critical cases without at least one hard-gate evaluator;
- invalid evaluator references.

Semantic deduplication, coverage heatmaps, discriminative-power analysis, and evaluator variance trends belong to Sprint 5.

## Evaluator suite v1

`evaluation.json` is defined before baseline and candidate execution:

```json
{
  "format_version": "1.0",
  "suite_id": "support-agent-core",
  "version": "1.0.0",
  "evaluators": [
    {"id": "execution-ok", "type": "no_execution_error"},
    {"id": "required-agents", "type": "required_agents"},
    {"id": "forbidden-agents", "type": "forbidden_agents"},
    {"id": "required-tools", "type": "required_tools"},
    {"id": "forbidden-tools", "type": "forbidden_tools"},
    {"id": "max-tool-calls", "type": "max_tool_calls"}
  ],
  "acceptance": {
    "reject_on_evaluator_error": true,
    "no_regression_at_or_above_risk": "high",
    "critical_cases_must_pass": true,
    "minimum_candidate_pass_rate": 0.9,
    "minimum_delta": 0.0
  }
}
```

Built-in evaluators committed in Sprint 4:

1. `no_execution_error`;
2. `required_agents`;
3. `forbidden_agents`;
4. `required_tools`;
5. `forbidden_tools`;
6. `max_tool_calls`;
7. `required_tool_arguments`;
8. `output_contains` and `output_not_contains`;
9. `json_schema_subset` for dependency-free checks of required keys and primitive types;
10. `runner_verdict` for backward compatibility only.

Each evaluator reads its expectation from the test case unless an explicit configuration override is present. Missing applicable evidence produces `error`, not `fail`.

## Acceptance model

The policy produces one of three machine recommendations:

- `eligible`: all hard gates pass and numeric thresholds are met;
- `rejected`: a hard gate fails or a protected regression occurs;
- `manual_review`: evidence is incomplete, an evaluator errors, or a configured ambiguity rule is triggered.

Policy order:

1. validate dataset and evaluator fingerprints;
2. reject execution/evaluator errors when configured;
3. enforce critical-case gates;
4. detect protected regressions by case risk;
5. enforce per-evaluator thresholds;
6. enforce aggregate thresholds;
7. require the existing human decision before promotion.

`eligible` is not automatic acceptance. It means the candidate is safe to present for human approval.

## Artifact layout

Sprint 4 keeps the existing case-directory model and adds explicit raw/evaluated artifacts:

```text
case/
  case.yaml
  dataset.json
  test_cases.jsonl
  evaluation.json
  baseline.raw.jsonl
  baseline.evaluated.jsonl
  candidate.raw.jsonl
  candidate.evaluated.jsonl
  comparison.json
  decision.yaml
  review.html
```

Compatibility rule: existing `baseline.jsonl` and `candidate.jsonl` remain readable as legacy evaluated runs. New runs use the explicit `.raw.jsonl` and `.evaluated.jsonl` names.

## CLI changes

```bash
# Validate cases, manifest, evaluator suite, and acceptance policy
python aik.py validate path/to/case

# Execute only; produces candidate.raw.jsonl
python aik.py run path/to/case --stage candidate -- python my_runner.py

# Apply built-in and configured external evaluators
python aik.py evaluate path/to/case --stage candidate

# Compare evaluated baseline and candidate and calculate policy recommendation
python aik.py review path/to/case
```

Optional selection flags (`--id`, `--tag`) remain supported. Evaluation gets `--resume` and `--overwrite` with the same semantics as execution.

## LangGraph demonstration

The existing supervisor demo becomes the acceptance test for the architecture:

- the LangGraph runner returns output, agents, tool calls, and metadata without `passed`;
- AIK evaluates routing with built-in evaluators;
- baseline fails the mixed-intent high-risk case;
- candidate passes it;
- single-domain and negative cases do not regress;
- the policy returns `eligible`;
- the complete mock workflow remains offline and deterministic;
- optional OpenAI execution remains available but is not necessary to verify AIK.

This proves that someone can use AIK immediately with LangGraph while the AIK core remains independent from LangGraph.

## Work breakdown

### Story 1 — Schemas and fingerprints

- Add dataset and evaluation-suite loaders and validation.
- Canonicalize JSON and calculate SHA-256 fingerprints.
- Add v1-to-v2 compatibility warnings.
- Document dataset construction and split discipline.

Acceptance: invalid configurations fail with a precise path and message; identical content always produces the same fingerprint.

### Story 2 — Raw runner protocol 1.1

- Make runner `passed` optional.
- Write new executions to `<stage>.raw.jsonl`.
- Preserve structured execution errors.
- Continue reading protocol 1.0 outputs.

Acceptance: a runner returning only `output` can complete successfully; old runners and current tests still work.

### Story 3 — Deterministic evaluation engine

- Implement evaluator registry and common result schema.
- Implement the ten v1 evaluator types.
- Write `<stage>.evaluated.jsonl` atomically.
- Add resume, overwrite, ID, and tag filtering.

Acceptance: the same raw evidence and suite produce byte-stable evaluator content except for explicitly excluded timestamps.

### Story 4 — External evaluator protocol

- Define subprocess request/response schemas.
- Validate returned score, status, reason, and metadata.
- Keep external evaluators optional.

Acceptance: an offline fixture validates the protocol and the optional OpenAI adapter demonstrates a real rubric-based judge; errors produce manual review rather than a false agent failure.

### Story 5 — Acceptance policy and comparison

- Compute case verdicts from evaluator outcomes.
- Compare only matching dataset and evaluator fingerprints.
- Enforce risk-aware protected regressions and thresholds.
- Put the machine recommendation and reasons in `comparison.json` and `review.html`.

Acceptance: a higher average score with one protected high-risk regression is rejected.

### Story 6 — Demo, migration, and documentation

- Migrate the LangGraph mock demo to raw evidence.
- Add `dataset.json` and `evaluation.json`.
- Update README, skill workflow, runner protocol, and data format.
- Add a migration note for v0.3 users.

Acceptance: a new user can install demo dependencies and complete run → evaluate → review from documented commands.

## Test matrix

The sprint is complete only when automated tests cover:

- every built-in evaluator in pass, fail, not-applicable, and missing-evidence conditions;
- dataset and evaluator fingerprint stability;
- invalid manifests and invalid policies;
- protocol 1.0 compatibility and protocol 1.1 raw-only responses;
- external evaluator success, timeout, invalid JSON, and missing judge command;
- protected regression rejection despite a better average;
- evaluator error leading to manual review;
- mismatched fingerprints preventing misleading comparison;
- LangGraph mock end-to-end execution, evaluation, and review;
- existing promotion still requiring explicit human acceptance.

Quality gate:

```bash
python -m compileall -q aik.py .agents tests examples
python -m unittest discover -s tests -v
python aik.py validate examples/langgraph-multi-agent/case
```

## Definition of done

- AIK core has no new third-party dependency.
- Evaluators and acceptance policy are defined before a run.
- The runner is no longer required to return `passed`.
- Raw evidence is preserved separately from evaluation results.
- Dataset and evaluator fingerprints are present in comparisons.
- A protected regression cannot be hidden by aggregate improvement.
- Evaluator failures cannot masquerade as agent failures.
- Legacy v0.3 examples and runner outputs remain readable.
- The LangGraph mock demo runs fully offline.
- Documentation explains how to construct and govern a dataset.
- Promotion still requires an explicit human decision.

## Explicit non-goals

- longitudinal dashboard and champion history;
- hosted UI, database, or authentication;
- automatic prompt optimization;
- mandatory Promptfoo or Langfuse integration;
- semantic dataset deduplication;
- automatic production deployment;
- automatic acceptance or promotion.

## Estimated sequence

This is one focused engineering sprint of approximately 6–8 working days:

1. days 1–2: schemas, fingerprints, migration, protocol 1.1;
2. days 3–4: deterministic engine and evaluated artifacts;
3. day 5: policy engine and comparison integration;
4. day 6: external evaluator protocol;
5. days 7–8: LangGraph migration, tests, review UI, and documentation.

If the sprint must fit five days, defer the external evaluator implementation but keep its protocol specification. The deterministic engine, acceptance policy, fingerprints, and migrated demo are the minimum coherent release.

## Sprint 5 dependency

The future dashboard will use immutable copies of these artifacts to show:

- fixed anchor baseline versus current champion;
- candidate delta and accepted-run slope;
- regressions by risk, evaluator, tag, and dataset split;
- dataset coverage and evaluator variance;
- drill-down into the existing human review.

No dashboard metric should be implemented before Sprint 4 makes its dataset and evaluator fingerprints trustworthy.
