# Statistical reliability and flakiness

Sprint 6 answers the question one-shot evaluation cannot: *is an observed
improvement repeatable, or could it be agent or LLM-judge variability?* It
extends the existing runner bridge and evaluator engine — it does not
replace them, and one-shot evaluation remains fully supported and the
default.

## Workflow

```bash
python aik.py run CASE --stage baseline --repetitions 5 -- RUNNER_COMMAND
python aik.py run CASE --stage candidate --repetitions 5 -- RUNNER_COMMAND
python aik.py evaluate CASE --stage baseline --trials
python aik.py evaluate CASE --stage candidate --trials
python aik.py reliability CASE
python aik.py review CASE
python aik.py dashboard --history-root HISTORY
```

`--repetitions` defaults to `1` and, at `1`, behaves exactly like Sprint 1–5:
the same `baseline.raw.jsonl`/`candidate.raw.jsonl` and
`baseline.evaluated.jsonl`/`candidate.evaluated.jsonl` files, byte-for-byte.
At `N > 1`, `aik run` independently executes every `(case_id, repetition)`
pair through the same external-process runner protocol — the JSON stdin/stdout
contract never changes — and writes:

```text
baseline.trials.raw.jsonl        candidate.trials.raw.jsonl
baseline.trials.evaluated.jsonl  candidate.trials.evaluated.jsonl
reliability.json
```

Every observation has a stable `observation_id` (`"<case_id>#<repetition>"`).
`--resume` skips only the observations that already exist; `--overwrite`
replaces the selected observations. Execution order never affects the
aggregated statistics — every aggregation sorts by case ID (and, for the
bootstrap, by a fully deterministic draw order) before computing anything.
Failures and timeouts are recorded as evidence; they are never silently
retried and never converted into a passing or failing score.

`aik evaluate --trials` applies the exact same deterministic and semantic
evaluators used by single-run `aik evaluate` to every observation
independently — no separate evaluation engine.

`aik reliability CASE` reads the archived `*.trials.evaluated.jsonl` files
and computes `reliability.json`. It performs no agent, evaluator, judge, or
network call — only arithmetic over evidence that already exists.

## Statistics (Python standard library only)

For each stage, case, and evaluator:

- observation count, and pass/fail/error/not-applicable counts;
- pass rate over boolean (`pass`/`fail`) observations only — `error` and
  `not_applicable` observations are excluded from the denominator, never
  converted to a zero score;
- mean of numeric scores when any exist, and sample standard deviation when
  at least two exist (`statistics.stdev`);
- a 95% (or configured) **Wilson score interval** for the pass rate, computed
  with `statistics.NormalDist().inv_cdf(...)` — no interval is fabricated
  when there is no boolean evidence (`n == 0`);
- **flaky**: both a pass and a fail were observed across repetitions.

For the baseline/candidate comparison:

- **paired per-case delta**: `candidate_pass_rate - baseline_pass_rate` for
  every case with boolean evidence in both stages;
- a **deterministic, seeded paired bootstrap** over those per-case deltas —
  same seed, same evidence, same order ⇒ byte-identical statistics. The seed,
  sample count, confidence level, and method version are written into
  `reliability.json["method"]`;
- **P(candidate > baseline)**: the fraction of bootstrap resample means that
  are strictly positive — a deterministic, reproducible proxy for "the
  candidate is probably better," not a formal significance claim;
- **incompatible evaluators**: an evaluator ID whose *version* differs
  between baseline and candidate is never aggregated across that change —
  two evaluators are comparable only when both the ID and the version match;
- **changed-outcome cases**: cases whose majority pass/fail verdict flipped
  between stages.

## Optional statistical acceptance

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

Without this section, `aik review`/`aik history`/`aik dashboard` behave
exactly as in Sprint 4/5 — nothing changes. With it:

- fewer than `minimum_repetitions` observations on any paired case forces
  `manual_review`, never `eligible`;
- an evaluator version change between baseline and candidate forces
  `manual_review`;
- exceeding `maximum_flaky_case_rate` forces `manual_review`;
- a probability of improvement below `minimum_probability_of_improvement`
  is never `eligible` — it is `manual_review`, or `rejected` when the
  bootstrap confidence interval for the delta is entirely negative;
- **the deterministic Sprint 4 policy is always checked first and always
  wins when more conservative.** A statistical result can only confirm a
  deterministic recommendation or make it more conservative — it can never
  turn a deterministic `rejected` into `eligible`
  (`reliability.combine_recommendations`).

`comparison.json`'s `acceptance` block gains `deterministic_recommendation`
(the Sprint 4 verdict alone, for audit) and `statistical_reliability`
(the isolated statistical verdict, reasons, and headline numbers) whenever a
`reliability.json` was present when `aik review` ran.

## Champion history and promotion

`aik history record`/`init` archive `reliability.json` alongside
`comparison.json` and `decision.yaml` whenever it exists next to the case,
hash it like every other artifact, and cache a small, re-derivable summary
in `manifest.json["reliability"]` (recommendation, paired cases, probability
of improvement, delta, flaky-case count).

`history validate`/`history promote` re-derive that cached summary from the
archived `reliability.json` on every check. Editing `reliability.json` after
archiving, or editing only the manifest's cached copy, is always detected
and named as the exact affected artifact — promotion of a tampered run is
refused. Because `manifest.json["acceptance_recommendation"]` already
reflects the *combined* (deterministic + statistical) recommendation
whenever `aik reliability` ran before `aik review`, an insufficiently
repeated or statistically uncertain candidate cannot become champion: the
existing `acceptance_recommendation == "eligible"` promotion gate already
enforces it.

## Review and dashboard

The human review (`review.html`) and the local dashboard both show, for the
selected run: `n` beside every aggregate, the Wilson interval beside pass
rate, the bootstrap interval beside the delta, the configured probability
threshold next to the calculated probability, flaky cases ordered by risk
then case ID, evaluator instability (an evaluator ID whose repeated
observations disagree), incompatible evaluator versions, and a
plain-language reason for the recommendation. When no repeated trials were
archived for the selected run, both surfaces show a clear
**"one run only — uncertainty unavailable"** state instead of a fabricated
confidence interval.

## Dataset health (advisory only)

`reliability.json["dataset_health"]` flags cases that are consistently easy,
consistently failing, flaky, show evaluator disagreement, belong to
under-observed tags, or distinguish baseline from candidate. This is
guidance for a human to review or add cases — AIK never rewrites, retires,
accepts, or generates a test case automatically.

## Non-goals (see the Sprint 6 plan for the full list)

No production user-satisfaction telemetry, no hosted dashboard, no automatic
prompt optimization, no GEPA/Langfuse/Promptfoo dependency, no automatic
dataset mutation, and no claim of formal statistical significance for tiny
samples.
