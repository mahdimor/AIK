# Agreement: does the judge decide like your experts?

```bash
python aik.py agreement improvements/<case-name>
```

Reads `expert_decisions.jsonl`, judges every case fresh, and writes `agreement.json`.

## Why not raw agreement

Raw agreement flatters any judge on a skewed dataset. If 90% of dossiers are
approvals, a judge that approves everything agrees 90% of the time and has learned
nothing. Cohen's kappa subtracts the agreement two raters would reach by chance, so
that judge scores 0.

## The number nobody reports: the human ceiling

Two numbers matter.

1. How well the judge agrees with the experts.
2. **How well the experts agree with each other.**

The second is the ceiling. A judge cannot be more right than the humans are
consistent. A kappa of 0.72 is a failure if two experts agree at 0.95, and it is the
end of the road if they agree at 0.70 — further calibration would be fitting noise.
`aik agreement` computes every expert pair and says which side of the band the judge
is on.

This is why a subsample rated independently by a second expert is worth the cost. With
one rater there is no ceiling, and the report says so instead of implying the judge's
kappa means something on its own.

## Expert decisions

`expert_decisions.jsonl`, one line per expert per case:

```json
{"case_id": "dossier-4711", "expert": "amina", "verdict": "escalate", "rationale": "..."}
```

`verdict` uses the judge vocabulary: `approve`, `escalate`, `reject`,
`insufficient_evidence`. Several experts may rate the same case; the same expert may
not rate it twice.

## What is counted, and what is not

**Contested cases** — where experts disagree — are excluded from the judge's kappa and
listed separately. A contested case is evidence that the *policy* is ambiguous, not
that the judge is wrong. Resolve the policy first; those cases are the highest-value
thing the exercise produces.

**Abstentions** — where the judge returned `insufficient_evidence` — are excluded from
kappa and reported with the share of cases that remain. This matters: a judge that
abstains on everything hard scores beautifully on what is left. The report always
states the coverage next to the number.

**Undefined kappa.** When both raters used a single identical category, chance
agreement is 1.0 and kappa carries no information. The report returns `null` with the
reason rather than `1.0`.

**Small samples.** Below 20 paired decisions the report warns and asks you to read the
interval, not the point estimate. Every kappa carries a deterministic seeded bootstrap
percentile interval.

## What comes out

- `judge_vs_consensus` — the headline, with its interval;
- `judge_vs_each_expert` — where the judge tracks one rater more than another;
- `expert_vs_expert` — the ceiling, per pair;
- `confusion_matrix` — which verdict is being confused with which;
- `disagreements` — each mismatch with the judge's score, confidence, failing criteria
  and critical violations. This is the working list for the next calibration round;
- `contested` and `judge_abstained_cases`, listed by id;
- `interpretation` — plain sentences saying whether the judge is inside the human band
  and what the coverage was.

## Where it sits

Calibrate with `aik optimize` against the optimizer-visible splits, measure here, and
keep the holdout for the end. Kappa measured on cases an optimizer has already seen is
a training score. Read `references/optimizer-feedback.md` and `references/domain-judge.md`.
