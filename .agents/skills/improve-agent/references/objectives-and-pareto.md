# Objectives and the constrained Pareto front

Quality alone cannot decide whether to move a task to a cheaper model. Cost and
latency are objectives too, and they pull the other way. `aik pareto` ranks
evaluated variants on all three at once.

```bash
python aik.py pareto improvements/<case-name> \
  --variant "gpt-4o=improvements/<case-name>/gpt-4o.evaluated.jsonl" \
  --variant "mini=improvements/<case-name>/mini.evaluated.jsonl" \
  --variant "mini-optimized=improvements/<case-name>/mini-optimized.evaluated.jsonl"
```

It writes `pareto.json`.

## Feasibility first, ranking second

An unconstrained front happily surfaces a variant that is very cheap and fails a
critical case. So feasibility is decided before anything is ranked:

1. **Against the baseline.** Each variant runs through the same deterministic
   acceptance policy the review uses — protected-risk regressions, critical cases,
   `maximum_regressions`, `minimum_candidate_pass_rate`, `minimum_delta`. A
   `rejected` variant never reaches the front, however cheap it is. A
   `manual_review` variant does not reach it either: incomplete evidence never
   becomes eligible on its own.
2. **Against absolute gates**, from the optional `objectives` block below.

Only variants that survive both are ranked against each other.

## The objectives block

Add to `evaluation.json`, all keys optional:

```json
"objectives": {
  "minimum_pass_rate": 0.90,
  "maximum_cost_per_case_usd": 0.01,
  "maximum_latency_p95_ms": 3000,
  "require_cost": true,
  "require_latency": false
}
```

`aik validate` checks this block. A gate configured against a value that was never
measured is a failure, not a pass — asking for a cost ceiling on an unpriced run
rejects it rather than waving it through.

## Objectives on the front

| Metric | Direction | Source |
|---|---|---|
| `quality.pass_rate` | maximize | evaluated verdicts |
| `cost.per_case_usd` | minimize | runner-reported `cost.total_usd` |
| `latency.p95_ms` | minimize | AIK-measured `execution.duration_ms` |

A variant is **on the front** when no other feasible variant is at least as good on
every objective and strictly better on at least one. Two variants that each win on a
different objective both stay on the front: that is the trade-off the front exists to
show, and choosing between them is a human decision.

`cheapest_feasible` names the least expensive variant that survived every gate. It is
a recommendation for a reviewer, never an automatic migration.

## Missing evidence is never favourable

- A case with no `cost` is unpriced, not free.
- A run with any unpriced case has `cost.per_case_usd = None` and `complete = false`.
- A variant missing any of the three objectives is `not_comparable`: it is reported
  with its measured metrics and excluded from the ranking, rather than being ranked
  on a number that was invented for it.

This is the same rule the reliability layer follows: a missing observation never
becomes a zero score.

## Comparability

All variants must be evaluated on the same dataset fingerprint. A variant evaluated
on a different one is rejected with that reason, so a front is never built across
two different case sets.

## Statuses

| Status | Meaning |
|---|---|
| `on_front` | feasible and not dominated |
| `dominated` | feasible, but another variant beats it everywhere |
| `rejected` | failed the acceptance policy or an absolute gate |
| `manual_review` | evidence incomplete or incompatible; a human decides |
| `not_comparable` | an objective was not measured for every case |
