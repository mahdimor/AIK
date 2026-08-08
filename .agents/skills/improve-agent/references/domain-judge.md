# Domain judge: a verdict on one case

The rest of AIK judges a *version*: is this candidate better than the champion?
A business reviewer needs the other object — a verdict on one dossier, the criteria
that decided it, what was violated, and what to do next.

```bash
python aik.py judge improvements/<case-name> --case-id dossier-4711
python aik.py judge improvements/<case-name>            # every case
```

No evaluator runs here. `aik judge` reads evidence `aik evaluate` already produced
and maps it onto weighted criteria and a verdict vocabulary. There is no second
evaluation engine.

## The judge block

```json
"judge": {
  "id": "refund-policy-judge",
  "domain": "commerce.refunds",
  "version": "0.1.0",
  "criteria": {
    "policy_compliance": {"evaluators": ["delay-window"], "weight": 0.5},
    "evidence_quality": {"evaluators": ["proof-of-damage"], "weight": 0.3},
    "consistency": {"evaluators": ["rationale-matches"], "weight": 0.2}
  },
  "hard_rules": ["amount-ceiling"],
  "abstention": {"enabled": true, "minimum_confidence": 0.7},
  "verdicts": {"approve_at": 0.85, "escalate_at": 0.5},
  "recommended_actions": {"evidence_quality": "request_missing_evidence"}
}
```

Weights are normalized, so they need not sum to 1. `aik validate` checks the block
and refuses an evaluator id that is not in the suite.

## Hard rules are not weighed

A hard rule is a policy limit, not a quality signal: a delay window, an amount
ceiling, a mandatory approval. One violation decides the verdict — `reject` — no
matter how good every criterion looks. Weighing a legal limit against a rubric score
is how a system approves something it was never allowed to approve.

A hard rule that could **not** be checked (`error`, `not_applicable`, or missing) does
not pass by default either: the verdict becomes `insufficient_evidence`. You cannot
approve on a rule you did not evaluate.

## Confidence is coverage, not correctness

`confidence` is the share of criterion weight that produced a definite `pass`/`fail`.
A judge that could only check half of its criteria reports `0.5`, and that is the only
claim it makes. It is not a probability that the verdict is right, and it is never a
model's self-reported certainty.

With `abstention.enabled`, a confidence below `minimum_confidence` turns the verdict
into `insufficient_evidence` rather than a guess made on partial evidence.

## Verdicts

| Verdict | When |
|---|---|
| `reject` | a hard rule failed, or the score is below `escalate_at` |
| `escalate` | the score is between `escalate_at` and `approve_at` |
| `approve` | the score is at or above `approve_at` |
| `insufficient_evidence` | a hard rule was unchecked, nothing was measured, or confidence is below the floor |

A criterion is `pass` when all its evaluators passed, `fail` when all failed,
`partial` when they disagree, and `unknown` when none produced a definite status.
An `unknown` criterion is excluded from the score rather than counted as zero, and
its weight is subtracted from confidence.

## Output

```yaml
case_id: dossier-4711
judge: refund-policy-judge@0.1.0
verdict: escalate
score: 0.7
confidence: 1
criteria:
  policy_compliance: pass
  evidence_quality: fail
  consistency: pass
critical_violations: []
recommended_action: request_missing_evidence
```

`judgement.json` (single case) or `judgements.jsonl` (all cases) carries the full
record: per-criterion scores and weights, critical violations with their reasons,
abstention reasons, evidence citations, every unmeasured evaluator, and the dataset
and evaluator fingerprints the verdict was produced under.

`recommended_action` defaults per verdict and is overridden by the first entry in
`recommended_actions` matching the weakest failing criterion.

## Building a domain pack

A pack is data and configuration, not code:

1. Collect 30–100 real cases with the decision an expert made, as `test_cases.jsonl`.
2. Put the policy limits in deterministic evaluators and list them as `hard_rules`.
3. Put the interpretation — is this evidence sufficient, is this an exception — in
   `llm_rubric` evaluators grouped into criteria.
4. Hold out a representative slice so a calibrated judge can be shown to generalize.
5. Improve the rubric with `aik optimize`, never the hard rules.

Read `references/optimizer-protocol.md` and `references/optimizer-feedback.md`.
