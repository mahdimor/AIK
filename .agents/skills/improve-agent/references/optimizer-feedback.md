# Optimizer feedback and the sealed holdout

A prompt optimizer improves by reading *why* a case failed. The moment a case's
diagnostic text reaches the optimizer, that case stops measuring generalization —
it has become training signal. A score reported on it afterwards is not an
estimate, it is a memory.

`aik feedback` is the only supported way to turn AIK evidence into optimizer input.

```bash
python aik.py feedback improvements/<case-name> --stage candidate
```

It writes `feedback.json` and appends one line to `optimizer-log.jsonl`.

## What it guarantees

- **The `holdout` split is never exported.** There is no flag to include it. The
  exclusion is structural, so there is no setting to get wrong under deadline.
- **The holdout is identified but never described.** `feedback.json` records the
  holdout's case count and a content fingerprint — no IDs, no results, no scores,
  no reasons.
- **Every export is counted.** `optimizer-log.jsonl` records each export with its
  timestamp, stage, visible-case count, dataset fingerprint and holdout seal.
  A holdout consulted after forty optimization rounds is worth less than one
  consulted after three; the ledger makes that visible instead of forgotten.
- **A changed holdout is detected.** If the holdout fingerprint differs from the
  first recorded export, the report warns that any generalization claim measured
  against it is void. Quietly relaxing a holdout case is the easiest way to
  manufacture a good result.

## What it refuses

- A legacy dataset with no `dataset.json`: without splits nothing can be held out.
- A dataset with zero `holdout` cases: the optimized result would be unfalsifiable.
- A case missing its `split` field: unclassified evidence is not exported by default.

## What it does not do

AIK cannot stop a process from reading `test_cases.jsonl` directly, and does not
try to. This is a discipline mechanism, not a sandbox. It makes the honest path
the default path and leaves an audit trail when it is not taken.

## Split semantics for optimization

| Split | Optimizer may see feedback | Role |
|---|---|---|
| `development` | yes | the working set the optimizer improves against |
| `regression` | yes | fixed incidents that must keep passing |
| `challenge` | yes | rare, adversarial, high-impact behavior |
| `holdout` | **never** | the only honest estimate of generalization |

## Record shape

Each item in `feedback.json`:

```json
{
  "case_id": "confirm-before-action",
  "split": "development",
  "risk": "high",
  "tags": ["confirmation"],
  "description": "Require confirmation before a side effect",
  "input": {"messages": []},
  "expected": {"confirmation_before": "create-ticket"},
  "passed": false,
  "verdict": "fail",
  "output": "Ticket created.",
  "failed_evaluations": [
    {"evaluator_id": "confirmation", "status": "fail", "score": 0.0, "reason": "..."}
  ],
  "feedback": "Case confirm-before-action (high risk) -> FAIL.\nExpected behavior: ...\n[confirmation] fail: ..."
}
```

`feedback` is the reflective text an optimizer consumes. Cases with no result for
the stage carry no fabricated verdict: `passed` stays `null`, the feedback says so,
and the report warns.

## Using the holdout afterwards

Optimize against the visible splits, then evaluate the final candidate once on the
full dataset and read the holdout result. If it is materially worse than the
development result, the candidate memorized rather than generalized. Consult it as
few times as possible, and treat each consultation as spending a limited resource.
