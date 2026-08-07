# Changelog

All notable changes to AIK are documented here. The project uses semantic versioning
for release intent while remaining pre-`1.0`, where compatibility may still change.

## Unreleased

- Added `aik agreement`: Cohen's kappa between the judge and expert decisions read
  from `expert_decisions.jsonl`, with a deterministic seeded bootstrap interval.
- It also computes every expert-vs-expert pair — the human ceiling — because a judge
  cannot be more right than the humans are consistent, and reports which side of that
  band the judge is on. With a single rater it says there is no ceiling rather than
  implying the kappa stands alone.
- Contested cases (experts disagree) and judge abstentions are excluded from kappa and
  reported by id, with the coverage that remains. Kappa is `null` with a reason,
  never `1.0`, when chance agreement is total.
- Added `aik judge`, a domain verdict on one case (or every case) built from the
  evidence `aik evaluate` already produced. No evaluator runs and there is no second
  evaluation engine: a new `judge` block in `evaluation.json` maps existing evaluators
  onto weighted criteria, hard rules, abstention and a verdict vocabulary, validated
  by `aik validate`.
- A hard rule is not weighed against anything: one violation forces `reject`, and a
  hard rule that could not be checked forces `insufficient_evidence` rather than
  passing by default.
- `confidence` is the share of criterion weight that produced a definite status —
  coverage, not a probability of correctness. A criterion with no definite status is
  excluded from the score rather than counted as zero.
- Added `aik optimize` and optimizer protocol 1.0: AIK drives an external optimizer
  over one subprocess exchanging JSON, so no optimizer dependency enters the core.
  The feedback payload is built by `aik feedback` inside the command, so an optimizer
  reached this way cannot be handed the holdout.
- Added an optional `optimization` block to `evaluation.json` declaring which
  components an optimizer may rewrite and the candidate budget, validated by
  `aik validate`. Component paths may not escape the case directory.
- `aik optimize` rejects a response that proposes an unknown component, exceeds the
  candidate budget, reuses an id, or returns empty text. Candidates are written as
  text with a `proposal.json`; nothing is executed, evaluated or promoted.
- Added `examples/optimizers/rule_optimizer.py`, a dependency-free reference
  implementation, and `examples/optimizers/gepa_optimizer.py`, an optional GEPA
  implementation whose scoring half runs the real runner and evaluator suite in a
  throwaway copy of the case. GEPA's search loop is not exercised in CI.
- Added `aik pareto`, a constrained quality/cost/latency Pareto front over evaluated
  variants. Feasibility is decided first by the existing deterministic acceptance
  policy plus optional absolute gates, so a cheap variant that fails a critical case
  is rejected rather than ranked.
- Added an optional `objectives` block to `evaluation.json` (`minimum_pass_rate`,
  `maximum_cost_per_case_usd`, `maximum_latency_p95_ms`, `require_cost`,
  `require_latency`), validated by `aik validate`.
- Runner protocol 1.2: runners may report `cost` with a non-negative `total_usd` per
  case. A case with no cost is unpriced, never free, and a variant with any unpriced
  case is excluded from the ranking rather than ranked on an invented number.
- Added `aik feedback`, the optimizer-facing feedback export. It emits per-case
  diagnostic text for the `development`, `regression` and `challenge` splits and never
  the `holdout` split; there is no flag to include it.
- The holdout is sealed rather than described: `feedback.json` records only its case
  count and a content fingerprint.
- Every export appends to `optimizer-log.jsonl`, so the number of optimization rounds
  run against a dataset stays visible, and a holdout changed mid-optimization is
  detected and reported as a warning.
- `aik feedback` refuses to run on a legacy dataset, on a dataset with no holdout
  cases, or when a case is missing its `split`.

## 0.5.1 — 2026-08-05

- Public-project documentation and contribution workflow.
- Hardened statistical promotion gates and archived trial-evidence verification.
- Added an offline visual tutorial, real report screenshots, a static showcase build,
  portable dashboard paths, and a manually triggered GitHub Pages workflow.

## 0.5.0 — 2026-08-04

- Added repeated baseline/candidate trials.
- Added Wilson intervals, paired hierarchical bootstrap analysis, flaky-case
  detection, and evaluator-version compatibility checks.
- Added optional statistical acceptance gates that cannot override deterministic
  rejection.

## 0.4.0 — 2026-08-04

- Added immutable champion history and the standalone local dashboard.

## 0.3.0 — 2026-08-04

- Added governed Dataset v2 evaluation, deterministic and optional LLM evaluators,
  acceptance policies, and human-readable reviews.

## 0.2.0 — 2026-08-03

- Added the provider-neutral subprocess runner and LangGraph multi-agent demo.

## 0.1.0 — 2026-08-03

- Initial portable Agent Skill and local before/after comparison workflow.
