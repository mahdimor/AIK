# Optimizer protocol 1.0

AIK does not optimize anything itself. It owns the two ends that must not be
negotiable — what evidence an optimizer may see, and what it may write back — and
talks to whatever sits between them over one subprocess exchanging JSON.

```bash
python aik.py optimize improvements/<case-name> -- python path/to/my_optimizer.py
```

Any process that speaks this protocol qualifies: a local Python adapter, a binary,
another language, a job in a container. Nothing about the optimizer leaks into AIK
core, which stays standard-library only.

## Declare what may be rewritten

Add an `optimization` block to `evaluation.json`. Without it, `aik optimize` refuses
to run:

```json
"optimization": {
  "components": [{"id": "system-prompt", "path": "prompts/system.md"}],
  "max_candidates": 4,
  "max_metric_calls": 150
}
```

Component paths resolve inside the case directory and may not escape it. A case has
to stay self-contained to be reproducible, so an optimizable prompt lives in the case
— and a misconfigured path cannot make AIK read or overwrite something elsewhere.
`aik validate` checks this block.

## Input on stdin

```json
{
  "protocol_version": "1.0",
  "stage": "candidate",
  "objective": {"metric": "pass_rate", "direction": "maximize"},
  "budget": {"max_candidates": 4, "max_metric_calls": 150},
  "components": [{"id": "system-prompt", "path": "prompts/system.md", "text": "..."}],
  "feedback": {"summary": {}, "dataset_fingerprint": "sha256:...", "items": []}
}
```

`feedback` is produced by `aik feedback` inside the command; it cannot be supplied by
the caller. **An optimizer reached this way cannot be handed the holdout.**

## Output on stdout

Exactly one JSON object:

```json
{
  "protocol_version": "1.0",
  "candidates": [
    {
      "id": "gepa-best",
      "components": {"system-prompt": "rewritten text"},
      "rationale": "why this change",
      "metadata": {}
    }
  ],
  "metadata": {"optimizer": "gepa", "metric_calls": 87}
}
```

AIK rejects a response that:

- is not one JSON object, or has no candidates;
- returns more candidates than `max_candidates`;
- uses a duplicate or unsafe candidate id;
- proposes a component that was not offered — an optimizer may rewrite what it was
  given and nothing else;
- returns empty text for a component.

## What AIK writes

```text
optimization/
  proposal.json                          provenance, budget, holdout seal, candidates
  candidates/<candidate-id>/<component-id>.txt
```

Each run also appends to `optimizer-log.jsonl` with `"kind": "optimize"`, so the
number of optimization rounds against a dataset stays visible next to the feedback
exports. Read `references/optimizer-feedback.md`.

## What AIK does not do

Proposals are text on disk. Nothing is executed, evaluated, compared or promoted by
`aik optimize`. Run each candidate through `aik run` and `aik evaluate`, then
`aik pareto` or `aik review`, and let a human decide. An optimizer never reaches
production through this command.

## Implementations

- `examples/optimizers/rule_optimizer.py` — dependency-free reference. Not a good
  optimizer; it exists so the loop runs offline with no key and no package.
- `examples/optimizers/gepa_optimizer.py` — GEPA. Optional, needs
  `pip install gepa litellm` and a reflection model. Its scoring half runs the real
  AIK runner and evaluator suite in a throwaway copy of the case, so the case being
  optimized is never mutated during the search. The search loop is not exercised in
  CI, but it has been run end to end against gepa 0.1.4.

```bash
python aik.py optimize improvements/my-case -- \
  python examples/optimizers/gepa_optimizer.py \
    --case-dir /abs/path/to/improvements/my-case \
    --reflection-lm openai/gpt-4.1 \
    --max-metric-calls 60 \
    --runner python /abs/path/to/my_runner.py
```

Notes earned the hard way:

- `--case-dir` is required rather than read from the payload. The protocol carries no
  local path, because an optimizer running anywhere else could not use one.
- Use **absolute paths** in `--runner`. During the search the runner's working
  directory is the throwaway workspace, which is how it reads the candidate's
  component files (`prompts/system.md` and friends) relative to its own cwd.
- `reflection_lm` given as a string needs `litellm`; `pip install gepa` alone does not
  pull it in.
- GEPA catches its own reflection failures, logs "did not propose a new candidate",
  spends the whole budget and returns the seed. That is indistinguishable from an
  honest negative result, so the adapter fails loudly when the search finished with a
  single candidate instead of reporting "no improvement".
