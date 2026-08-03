# AIK — Agent Improvement Kit

AIK is a small, local-first Agent Skill that turns an AI-agent failure into a reproducible before/after review and a permanent regression test.

It is not an evaluation platform or an agent runner. It compares open JSONL evidence produced by any model, framework, script, or human test. No cloud account, database, SDK, or third-party Python dependency is required.

## Try it in one command

Requires Python 3.10 or newer.

```bash
python aik.py demo
```

Then open:

```text
examples/support-agent/confirmation-before-action/review.html
```

The example intentionally contains one improvement and one regression, demonstrating that AIK never hides a regression behind a better average score.

## Use it on an agent change

1. Create a case:

   ```bash
   python aik.py init my-agent-failure
   ```

2. Describe the issue in `improvements/my-agent-failure/case.yaml` and add stable cases to `test_cases.jsonl`.

3. Record the same cases before and after the change in `baseline.jsonl` and `candidate.jsonl`.

4. Validate and generate the review:

   ```bash
   python aik.py validate improvements/my-agent-failure
   python aik.py review improvements/my-agent-failure
   ```

5. Open `review.html`, inspect every regression, and record the human decision in `decision.yaml`.

6. After acceptance, preserve improved cases as permanent regression tests:

   ```bash
   python aik.py promote improvements/my-agent-failure
   ```

Run `python aik.py --help` for all commands.

## What is tracked

Every test case is classified as:

- `improved`: failed before and passes now;
- `regressed`: passed before and fails now;
- `unchanged`: the pass/fail outcome is unchanged;
- `not_comparable`: evidence is missing or invalid.

Optional fields can capture numeric scores, tool calls, participating agents, latency, cost, and behavioral contracts. See [the data format](.agents/skills/improve-agent/references/data-format.md).

## Repository layout

```text
aik.py                                  Single user-facing CLI
.agents/skills/improve-agent/           Portable Agent Skill
datasets/regression.jsonl               Permanent accepted tests
examples/                               Anonymized working example
tests/                                  Dependency-free test suite
```

Compatible agents can discover `.agents/skills/improve-agent/SKILL.md`. A user or agent can also run `aik.py` directly; the workflow does not depend on a specific host.

## Safety boundary

AIK never executes an agent, calls a production tool, deploys a prompt, or decides acceptance automatically. Promotion is blocked until `decision.yaml` contains `accepted` or `accepted_with_conditions`. Read [SECURITY.md](SECURITY.md) before committing real conversations.

## Development

```bash
python -m compileall -q aik.py .agents tests
python -m unittest discover -s tests -v
python aik.py validate examples/support-agent/confirmation-before-action
```

MIT licensed. Python 3.10+.
