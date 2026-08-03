---
name: improve-agent
description: Turn an AI-agent failure or behavior change into a reproducible improvement case with versioned tests, optional framework-agnostic command execution, baseline and candidate runs, deterministic improvement/regression tracking, a human-readable review, and promoted regression tests. Use when running or comparing agent versions, investigating a failed conversation or tool call, validating a prompt/model/routing/tool change, checking multi-agent behavior, or preserving a fixed incident as a permanent test.
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
4. Add stable test IDs and behavioral expectations to `test_cases.jsonl`. Read `references/data-format.md` when creating or converting data.
5. Populate `baseline.jsonl` and `candidate.jsonl` on the same cases. Import existing evidence, or execute a project-owned runner:

   ```bash
   python .agents/skills/improve-agent/scripts/execute_cases.py improvements/<case-name> --stage baseline -- python path/to/runner.py
   python .agents/skills/improve-agent/scripts/execute_cases.py improvements/<case-name> --stage candidate -- python path/to/runner.py
   ```

   Read `references/runner-protocol.md` before creating or executing a runner. Inspect untrusted runner code first. Use `--resume` after interruption, `--overwrite` for an intentional rerun, and `--id` or `--tag` to select cases.
7. Validate the case:

   ```bash
   python .agents/skills/improve-agent/scripts/validate_case.py improvements/<case-name>
   ```

8. Generate comparison data and the human review:

   ```bash
   python .agents/skills/improve-agent/scripts/run_review.py improvements/<case-name>
   ```

9. Review every `regressed` and `not_comparable` case. Do not recommend acceptance from aggregate scores alone.
10. Record the human decision in `decision.yaml`.
11. After explicit acceptance, promote selected improved cases:

   ```bash
   python .agents/skills/improve-agent/scripts/promote_tests.py improvements/<case-name> datasets/regression.jsonl --status improved
   ```

## Evaluation Rules

- Classify `failed -> passed` as `improved`.
- Classify `passed -> failed` as `regressed`.
- Classify equal pass/fail outcomes as `unchanged`; preserve metric deltas for review.
- Classify missing baseline or candidate results as `not_comparable`.
- Never hide regressions behind a better average score.
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
baseline.jsonl
candidate.jsonl
comparison.json
review.html
decision.yaml
```

Report the counts of `improved`, `regressed`, `unchanged`, and `not_comparable`, then link the human-readable `review.html`.
