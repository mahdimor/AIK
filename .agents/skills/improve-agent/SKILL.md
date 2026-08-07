---
name: improve-agent
description: Turn an AI-agent failure or behavior change into a reproducible improvement case with governed datasets, raw executions, deterministic and optional LLM evaluators, explicit acceptance gates, baseline/candidate comparison, human review, promoted regression tests, immutable champion history, a standalone local dashboard, and repeated-trial statistical reliability analysis. Use when evaluating or comparing agent versions, investigating failed conversations or tool calls, validating prompt/model/routing/tool changes, checking multi-agent behavior, preserving fixed incidents as permanent tests, tracking accepted agent versions over time, or checking whether an observed improvement is repeatable rather than agent/judge noise.
---

# Improve Agent

Create evidence for an agent behavior change. Treat datasets and before/after runs as permanent artifacts, not temporary output.

## Workflow

1. Locate the repository root and the target `improvements/` and `datasets/regression.jsonl` paths.
2. Initialize one improvement case:

   ```bash
   python .agents/skills/improve-agent/scripts/init_case.py <case-name>
   ```

3. Complete `case.yaml` with the problem, hypothesis, intended change, and risks.
4. Define the dataset metadata, stable test IDs, behavioral expectations, risk, source, split, and status. Read `references/data-format.md` and `references/dataset-construction.md`.
5. Define `evaluation.json` before executing the agent. Combine hard behavioral contracts with an optional semantic rubric. Read `references/evaluation.md`.
6. Populate raw baseline and candidate evidence on the same cases. Import evidence, or execute a project-owned runner:

   ```bash
   python .agents/skills/improve-agent/scripts/execute_cases.py improvements/<case-name> --stage baseline -- python path/to/runner.py
   python .agents/skills/improve-agent/scripts/execute_cases.py improvements/<case-name> --stage candidate -- python path/to/runner.py
   ```

   Read `references/runner-protocol.md` before creating or executing a runner. Inspect untrusted runner code first. Use `--resume` after interruption, `--overwrite` for an intentional rerun, and `--id` or `--tag` to select cases.

   For LangGraph projects, read `references/langgraph-adapter.md` and use `scripts/langgraph_runner.py`.
7. Evaluate each stage. Omit the command for deterministic-only evaluation, or pass an external judge after `--`:

   ```bash
   python aik.py evaluate improvements/<case-name> --stage baseline
   python aik.py evaluate improvements/<case-name> --stage candidate -- python path/to/judge.py
   ```

   Use `scripts/openai_judge.py` as the optional OpenAI implementation. AIK core does not depend on OpenAI or any other provider.
8. Validate the case:

   ```bash
   python .agents/skills/improve-agent/scripts/validate_case.py improvements/<case-name>
   ```

9. Generate comparison data, acceptance recommendation, and human review:

   ```bash
   python .agents/skills/improve-agent/scripts/run_review.py improvements/<case-name>
   ```

10. Review every regression, evaluator error, and non-comparable case. Treat `eligible` as ready for human approval, never as automatic acceptance.
11. Record the human decision in `decision.yaml`.
12. After explicit acceptance, promote selected improved cases:

   ```bash
   python .agents/skills/improve-agent/scripts/promote_tests.py improvements/<case-name> datasets/regression.jsonl --status improved
   ```

## Champion History and Dashboard

Once a campaign is evaluated and reviewed, preserve it as immutable history and see
progress over time without opening raw JSON:

```bash
python aik.py history init improvements/<case-name> --stage baseline --label "Initial version" --agent-version v1
python aik.py history record improvements/<case-name> --stage candidate --label "Fix" --agent-version v2
python aik.py history promote <run-id>     # only after decision.yaml is accepted and the recommendation is eligible
python aik.py history list
python aik.py history validate
python aik.py dashboard
```

History recording only archives evidence Sprint 4 already produced; it never reruns
an agent. `history init` runs once per history root and sets the fixed anchor and the
initial champion together. `history record` never moves the champion; only explicit
`history promote` does, and only when the archived run is hash-verified, its role is
`candidate`, it is not stale, its dataset/evaluator cohort matches the champion (or a
recorded `cohort_anchor` rerun bridges a dataset/evaluator change), its recommendation
is `eligible`, and its human decision is accepted. `aik.py dashboard` writes
`aik-history/dashboard.json` and `dashboard.html`, a standalone local report with
level, delta vs champion/anchor, slope, regressions, evaluator scores, coverage, and a
full timeline — coverage growth is never presented as quality improvement, and an
incompatible cohort never produces a fabricated delta. Read
`references/history-dashboard.md` for the full command reference, comparability
rules, metric formulas, and security notes.

## Statistical Reliability

A single baseline/candidate run cannot tell you whether an improvement is repeatable
or just agent/LLM-judge noise. Repeat the same cases and let AIK measure it:

```bash
python aik.py run improvements/<case-name> --stage baseline --repetitions 5 -- RUNNER_COMMAND
python aik.py run improvements/<case-name> --stage candidate --repetitions 5 -- RUNNER_COMMAND
python aik.py evaluate improvements/<case-name> --stage baseline --trials
python aik.py evaluate improvements/<case-name> --stage candidate --trials
python aik.py reliability improvements/<case-name>
python aik.py review improvements/<case-name>
```

`--repetitions` defaults to `1` and is fully backward compatible at that default.
Above `1`, every `(case_id, repetition)` executes independently through the same
external-process runner protocol and is evaluated by the same evaluators used for a
single run — there is no separate evaluation engine. `aik reliability` reads that
evidence and writes `reliability.json`: pass rate, Wilson confidence interval, a
deterministic seeded bootstrap for the baseline/candidate delta and `P(candidate >
baseline)`, flaky cases, and evaluator version incompatibilities, using only the
Python standard library. Missing or errored observations are never converted into a
zero score. Add an optional `reliability` section to `evaluation.json` to gate
acceptance on this evidence — without it, behavior is unchanged from Sprint 4/5. With
it, insufficient repetitions or a probability below the configured threshold can only
push a recommendation toward `manual_review`/`rejected`, never toward `eligible`: a
statistical result can never override a deterministic rejection. `aik history`
archives `reliability.json` like any other artifact (hashed, tamper-detected), so an
unrepeatable candidate cannot become champion. Read
`references/statistical-reliability.md` for the exact formulas, config schema, and
promotion interaction.

## Objectives and the Constrained Pareto Front

Quality alone cannot decide whether a task can move to a cheaper model. Rank
evaluated variants on quality, cost and latency together:

```bash
python aik.py pareto improvements/<case-name> \
  --variant "gpt-4o=improvements/<case-name>/gpt-4o.evaluated.jsonl" \
  --variant "mini=improvements/<case-name>/mini.evaluated.jsonl"
```

Feasibility is decided before anything is ranked. Each variant first runs through the
same deterministic acceptance policy the review uses, then through the optional
absolute gates in the `objectives` block of `evaluation.json`
(`minimum_pass_rate`, `maximum_cost_per_case_usd`, `maximum_latency_p95_ms`,
`require_cost`, `require_latency`). A `rejected` or `manual_review` variant never
reaches the front, however cheap it is — an unconstrained front would happily
surface a variant that is very cheap and fails a critical case.

`cost.total_usd` per case comes from the runner (protocol 1.2); AIK measures
`execution.duration_ms` itself. A case with no cost is unpriced, never free: a variant
with any unpriced case is `not_comparable` and is excluded from the ranking rather
than ranked on an invented number. All variants must share one dataset fingerprint.
`cheapest_feasible` is a recommendation for a reviewer, never an automatic migration.
Read `references/objectives-and-pareto.md`.

## Optimizer Feedback

A prompt optimizer improves by reading why a case failed, so any case it sees stops
measuring generalization. Export optimizer input only through:

```bash
python aik.py feedback improvements/<case-name> --stage candidate
```

This writes `feedback.json` with per-case diagnostic text for the optimizer-visible
splits (`development`, `regression`, `challenge`) and **never** the `holdout` split —
there is no flag to include it. The holdout appears only as a count and a content
fingerprint. Every export appends to `optimizer-log.jsonl`, so the number of rounds
run against a dataset stays visible, and a holdout changed mid-optimization is
detected and reported as a warning. The command refuses to run on a legacy dataset,
on a dataset with no holdout cases, or when a case is missing its `split`.

AIK does not sandbox the optimizer: it makes the honest path the default and leaves
an audit trail. Read `references/optimizer-feedback.md`.

## Evaluation Rules

- Classify `failed -> passed` as `improved`.
- Classify `passed -> failed` as `regressed`.
- Classify equal pass/fail outcomes as `unchanged`; preserve metric deltas for review.
- Classify missing baseline or candidate results as `not_comparable`.
- Never hide regressions behind a better average score.
- Keep raw evidence separate from evaluated evidence.
- Define evaluator versions and acceptance thresholds before running the candidate.
- Treat evaluator errors as missing evidence, not agent failures.
- Label LLM-estimated satisfaction as predicted satisfaction; do not present it as real user feedback.
- Require stable result IDs across runs. When dataset, evaluator, or trial metadata is managed outside this Sprint 1 format, verify it before interpreting the comparison.
- Prefer deterministic contracts for tool counts, routing, confirmations, forbidden data, and turn limits. Read `references/behavioral-contracts.md` when specifying contracts.
- Use the failure categories in `references/failure-taxonomy.md` when diagnosing cases.
- Never deploy an agent or mutate a production system as part of this workflow.
- Treat runner commands as project code with the current user's permissions. AIK does not sandbox them.
- Never promote tests before a human explicitly accepts the candidate.
- Never hand `holdout` cases, results, or reasons to a prompt optimizer. Use `aik feedback`.
- Never treat an unpriced or untimed case as free or instant; it is unmeasured evidence.
- Never rank a variant on the Pareto front before it clears the acceptance policy.
- Redact secrets and personal or internal identifiers before sharing artifacts.

## Minimum Deliverables

An improvement case is complete only when it contains:

```text
case.yaml
test_cases.jsonl
dataset.json
evaluation.json
baseline.raw.jsonl
baseline.evaluated.jsonl
candidate.raw.jsonl
candidate.evaluated.jsonl
comparison.json
review.html
decision.yaml
```

Report the counts of `improved`, `regressed`, `unchanged`, and `not_comparable`, then link the human-readable `review.html`.
