---
name: improve-agent
description: Turn an AI-agent failure or behavior change into a reproducible improvement case with governed datasets, raw executions, deterministic and optional LLM evaluators, explicit acceptance gates, baseline/candidate comparison, human review, promoted regression tests, immutable champion history, and a standalone local dashboard. Use when evaluating or comparing agent versions, investigating failed conversations or tool calls, validating prompt/model/routing/tool changes, checking multi-agent behavior, preserving fixed incidents as permanent tests, or tracking accepted agent versions over time.
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
