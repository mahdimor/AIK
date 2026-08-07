# Changelog

All notable changes to AIK are documented here. The project uses semantic versioning
for release intent while remaining pre-`1.0`, where compatibility may still change.

## Unreleased

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
