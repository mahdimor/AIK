# Optimizing a prompt end to end

A worked example of the full loop: seal a holdout, export feedback, let an
optimizer propose a prompt, evaluate it, and rank it on cost as well as quality.

Reproducing it calls a real model, so it is the only example that needs an API key —
but the evidence from an actual run is committed, so `validate`, `review`, `feedback`
and `pareto` all work here offline and for free.

## The task

Ten customer messages asking for a refund must become strict JSON with an
`order_id` and a `reason_code` from a closed vocabulary. Six cases are
`development`, four are `holdout`.

The seed prompt in `prompts/system.md` is one line:

```text
Extract the refund details from the customer message.
```

It names neither the schema nor the vocabulary, so nothing downstream can consume
the output.

## What a real run produced

| | development (optimizer saw these) | holdout (never seen) |
|---|---|---|
| seed prompt | 0/6 | 0/4 |
| GEPA candidate | 6/6 | 3/4 |

`recorded/gepa-system-prompt.md` is what the optimizer wrote: about forty lines
carrying the JSON schema, the closed list of reason codes and six mapping examples.
`baseline.evaluated.jsonl` is the seed run and `candidate.evaluated.jsonl` the
optimized one, so the whole thing is explorable with no key:

```bash
python aik.py validate examples/refund-extraction
python aik.py review   examples/refund-extraction     # improved=9 unchanged=1
python aik.py feedback examples/refund-extraction --stage baseline
python aik.py pareto   examples/refund-extraction \
  --variant "seed=examples/refund-extraction/baseline.evaluated.jsonl" \
  --variant "optimized=examples/refund-extraction/candidate.evaluated.jsonl"
```

The `feedback` call is worth reading: `visible_cases=6 visible_failed=6
withheld_holdout_cases=4`. Those six failures, with their reasons, are the entire
input GEPA had. The four holdout cases are counted and sealed, never described.

**Read the remaining holdout failure — it is the point of the example.** On case
`r7` the model answered `"reason_code": "incorrect_item"` where the expected code
was `wrong_item`. The optimizer never saw that case, so it could not know the exact
vocabulary and invented a plausible synonym. On the split it optimized against, the
prompt scores 100%. The holdout is the only place that gap is visible, and reporting
the development number alone would have been a lie.

## Running it yourself

Needs `OPENAI_API_KEY`, plus `pip install gepa litellm` for the optimizer. A run at
this size costs a few cents.

Running the steps below overwrites the committed evidence; `git restore
examples/refund-extraction` puts it all back.

```bash
# 1. Baseline: the seed prompt against the real model.
python aik.py run   examples/refund-extraction --stage baseline --overwrite \
  --cwd examples/refund-extraction -- python /abs/path/to/examples/refund-extraction/runner.py
python aik.py evaluate examples/refund-extraction --stage baseline --overwrite

# 2. What the optimizer is allowed to see. The holdout is not in it.
python aik.py feedback examples/refund-extraction --stage baseline

# 3. Propose a candidate. Absolute paths: during the search the runner's working
#    directory is a throwaway copy of this case.
python aik.py optimize examples/refund-extraction --overwrite -- \
  python /abs/path/to/examples/optimizers/gepa_optimizer.py \
    --case-dir /abs/path/to/examples/refund-extraction \
    --reflection-lm openai/gpt-4.1 \
    --max-metric-calls 60 \
    --runner python /abs/path/to/examples/refund-extraction/runner.py

# 4. Adopt the candidate and re-measure, holdout included.
cp examples/refund-extraction/optimization/candidates/gepa-best/system-prompt.txt \
   examples/refund-extraction/prompts/system.md
python aik.py run examples/refund-extraction --stage candidate --overwrite \
  --cwd examples/refund-extraction -- python /abs/path/to/examples/refund-extraction/runner.py
python aik.py evaluate examples/refund-extraction --stage candidate --overwrite

# 5. Rank the result on quality, cost and latency together.
python aik.py pareto examples/refund-extraction \
  --variant "seed=examples/refund-extraction/baseline.evaluated.jsonl" \
  --variant "optimized=examples/refund-extraction/candidate.evaluated.jsonl"
```

## Why step 5 is not optional

The recorded run on the Pareto front:

```text
seed         rejected    pass_rate=0.0  cost_per_case=1.1e-05  p95_ms=1840
optimized    on_front    pass_rate=0.9  cost_per_case=8.1e-05  p95_ms=936
front=optimized
```

The optimized prompt is forty times longer, so it costs about seven times more per
case. Without the `minimum_pass_rate` gate in the `objectives` block of
`evaluation.json`, the seed comes out cheapest and feasible — at a pass rate of zero.
A cost objective without a quality floor will always recommend the prompt that says
nothing.

## The runner

`runner.py` implements runner protocol 1.2: one JSON payload on stdin, one JSON
object on stdout. It reads `prompts/system.md` **relative to its working directory**,
which is what lets AIK point it at the candidate under test during the search, and it
reports `cost.total_usd` per case so the Pareto front has a price to work with.

It is ordinary example code with no error handling to speak of. Read it before you
run it, as with any runner.
