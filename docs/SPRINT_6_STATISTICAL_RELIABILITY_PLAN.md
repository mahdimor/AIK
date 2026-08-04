# Sprint 6 — Statistical Reliability and Flakiness

## Decision

AIK already answers: “Did the candidate pass the defined evaluators on this run?”

Sprint 6 must answer the next question:

> Is the observed improvement repeatable, or could it be caused by agent or LLM-judge variability?

This sprint remains local, open, framework-agnostic, and dependency-free at runtime. It must extend the existing runner bridge rather than introduce an Azure, LangGraph, OpenAI, Langfuse, Promptfoo, or GEPA dependency.

Target release: `v0.5.0`. This is a capability increment, not a `v1.0` stability claim.

## User outcome

A developer can execute the same baseline and candidate several times, evaluate every observation, and receive a human-readable answer containing:

- the observed success rate;
- the sample size;
- a confidence interval;
- the probability that the candidate is better than the baseline;
- unstable cases and unstable evaluators;
- a conservative acceptance recommendation;
- the evidence required to reproduce the calculation.

One-shot evaluation remains supported and remains the default.

## Scope

### 1. Repeated execution

Extend the existing command:

```text
python aik.py run CASE --stage baseline --repetitions 5 -- RUNNER_COMMAND
python aik.py run CASE --stage candidate --repetitions 5 -- RUNNER_COMMAND
```

Requirements:

- `--repetitions` defaults to `1`;
- every `(case_id, repetition)` is executed independently;
- resume and overwrite behavior remains explicit;
- evidence records contain `case_id`, `repetition`, and a stable `observation_id`;
- execution order does not affect aggregation;
- failures and timeouts remain evidence and are never silently retried;
- the external runner protocol does not change.

Recommended files:

```text
baseline.trials.raw.jsonl
candidate.trials.raw.jsonl
baseline.trials.evaluated.jsonl
candidate.trials.evaluated.jsonl
reliability.json
```

The existing single-run files remain valid and unchanged.

### 2. Evaluation of every observation

Apply the existing deterministic and semantic evaluators independently to every observation.

An LLM judge result must retain:

- evaluator ID and version;
- judge/model identity when supplied by the adapter;
- numeric score or categorical status;
- reason and evidence;
- error or not-applicable state;
- observation ID.

Do not convert missing or errored judgments into zero scores.

### 3. Reliability aggregation

Produce `reliability.json` using deterministic, documented calculations implemented with the Python standard library.

For each stage, case, and evaluator report:

- number of observations;
- pass, fail, error, and not-applicable counts;
- pass rate over boolean observations;
- mean numeric score when numeric observations exist;
- standard deviation when at least two numeric observations exist;
- 95% Wilson confidence interval for a boolean pass rate;
- flaky status when both pass and fail are observed.

For baseline versus candidate report:

- pass-rate delta;
- paired observations count;
- deterministic seeded bootstrap confidence interval for the delta;
- deterministic seeded estimate of `P(candidate > baseline)`;
- cases whose outcome changed across repetitions;
- evaluator versions that cannot be compared.

The bootstrap seed, number of samples, and method version must be written into the artifact. Identical input evidence must produce identical statistics.

### 4. Explicit statistical acceptance

Add an optional `reliability` section to `evaluation.json`:

```json
{
  "reliability": {
    "minimum_repetitions": 3,
    "confidence_level": 0.95,
    "bootstrap_samples": 5000,
    "minimum_probability_of_improvement": 0.90,
    "maximum_flaky_case_rate": 0.10
  }
}
```

Rules:

- without this section, current Sprint 4/5 acceptance behavior is unchanged;
- insufficient observations produce `manual_review`, never `eligible`;
- deterministic hard gates still have priority over statistical scores;
- evaluator errors still follow `reject_on_evaluator_error`;
- a statistical result cannot make a deterministically rejected candidate eligible;
- promotion records the reliability artifact and its hash;
- baseline and candidate must use the same dataset and evaluator fingerprints;
- evaluator statistics are comparable only for the same evaluator ID and version.

### 5. Human review and dashboard

Extend the local review and dashboard with:

- `n` beside every aggregate;
- confidence interval beside pass rate and delta;
- a visible `one run only — uncertainty unavailable` state;
- a list of flaky cases ordered by risk, then case ID;
- evaluator instability and judge disagreement;
- the configured probability threshold;
- the calculated probability of improvement;
- a plain-language recommendation reason.

Never display statistical confidence when the minimum sample requirement is not met.

### 6. Dataset feedback, not automatic generation

Generate a dataset-health section identifying:

- cases that are consistently easy for both versions;
- cases that are consistently failing;
- flaky cases;
- cases with evaluator disagreement;
- tags or risks with too few observations;
- cases that distinguish baseline from candidate.

This section recommends where humans should add or review cases. It must not automatically rewrite, accept, retire, or generate test cases.

## Required CLI behavior

Add or extend commands without breaking existing usage:

```text
aik run ... --repetitions N
aik evaluate ... --trials
aik reliability CASE
aik review CASE
aik dashboard ...
```

`aik reliability CASE` reads existing evaluated trial evidence and performs no agent or judge calls.

## Required tests

At minimum, tests must prove:

1. one repetition preserves the current workflow;
2. repeated executions generate unique stable observation IDs;
3. resume skips completed observations only;
4. aggregation is independent of input ordering;
5. Wilson intervals match known examples;
6. bootstrap output is reproducible with the recorded seed;
7. pass/fail variation marks a case flaky;
8. missing scores are excluded, not converted to zero;
9. evaluator ID equality with version inequality is not comparable;
10. insufficient repetitions force manual review;
11. a deterministic rejection cannot be overridden statistically;
12. a likely improvement below the configured probability threshold is not eligible;
13. archived reliability tampering is detected;
14. review and dashboard escape all imported text;
15. all existing Sprint 1–5 tests remain green.

Use fixed synthetic observations in unit tests. Tests must not require network access or paid LLM calls.

## Non-goals

Do not add in Sprint 6:

- production user-satisfaction telemetry;
- hosted dashboards or authentication;
- automatic prompt optimization;
- GEPA integration;
- Langfuse or Promptfoo as required dependencies;
- automatic dataset generation;
- claims of formal statistical significance for tiny samples;
- a new agent framework adapter.

User satisfaction belongs to a later production-observability sprint because it requires real interaction signals, privacy rules, sampling, and bias controls. Sprint 6 concerns controlled pre-release evaluation.

## Definition of done

Sprint 6 is complete only when:

- a repeated baseline/candidate experiment works through the generic runner bridge;
- statistical artifacts are reproducible and validated;
- one-shot projects remain fully compatible;
- promotion cannot bypass configured reliability gates;
- the dashboard clearly separates observed performance from uncertainty;
- flaky cases and evaluator disagreement are visible to a non-specialist;
- the LangGraph demo includes an offline repeated-run example;
- the full test suite passes without network access;
- CLI and documentation use version `0.5.0`.

## Recommended implementation order

1. Define trial and reliability schemas with fixtures.
2. Implement repeated execution and resume behavior.
3. Evaluate observations using existing evaluators.
4. Implement pure statistical aggregation functions and unit tests.
5. Add optional reliability acceptance gates.
6. Archive reliability evidence in champion history.
7. Extend review and dashboard.
8. Update the offline LangGraph demo and documentation.

Stop after each step with a green repository. Do not implement optimization integrations until the measurements are reliable.
