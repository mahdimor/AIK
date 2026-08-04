# AIK — Agent Improvement Kit

AIK is a small, local-first Agent Skill that turns an AI-agent failure into governed test data, independently evaluated before/after evidence, an explicit acceptance recommendation, and a permanent regression test.

It is not a hosted observability platform and does not embed an agent SDK. The core executes project-owned runners, applies deterministic evaluators, delegates optional semantic judgment through a provider-neutral subprocess protocol, and enforces acceptance gates. No cloud account, database, framework SDK, or third-party Python dependency is required by AIK core.

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

2. Describe the issue, add governed cases to `test_cases.jsonl`, and define evaluators and acceptance gates in `evaluation.json` before changing the agent.

3. Record the same cases before and after the change. Files can be written by an existing test harness, or generated through any project-owned command:

   ```bash
   python aik.py run improvements/my-agent-failure --stage baseline -- python path/to/my_runner.py
   # Change the prompt, model, tools, routing, or agent code.
   python aik.py run improvements/my-agent-failure --stage candidate -- python path/to/my_runner.py
   ```

   The runner may use MCP, an SDK, HTTP, a local model, or a deterministic mock. It returns raw evidence and does not need to decide `passed`. See [the runner protocol](.agents/skills/improve-agent/references/runner-protocol.md).

4. Evaluate both stages. Deterministic evaluation needs no external command:

   ```bash
   python aik.py evaluate improvements/my-agent-failure --stage baseline
   python aik.py evaluate improvements/my-agent-failure --stage candidate
   ```

   For semantic rubrics, pass any compatible judge command after `--`. The bundled optional OpenAI judge is one implementation:

   ```bash
   python aik.py evaluate improvements/my-agent-failure --stage candidate -- \
     python .agents/skills/improve-agent/scripts/openai_judge.py
   ```

5. Validate and generate the review:

   ```bash
   python aik.py validate improvements/my-agent-failure
   python aik.py review improvements/my-agent-failure
   ```

6. Open `review.html`, inspect the policy recommendation and every regression, then record the human decision in `decision.yaml`.

7. After acceptance, preserve improved cases as permanent regression tests:

   ```bash
   python aik.py promote improvements/my-agent-failure
   ```

Run `python aik.py --help` for all commands.

## Try the execution bridge

The bundled fake runner is deterministic and makes no network call:

```bash
python aik.py run examples/execution-bridge --stage baseline --overwrite -- python examples/runners/fake_agent.py
python aik.py run examples/execution-bridge --stage candidate --overwrite -- python examples/runners/fake_agent.py
python aik.py review examples/execution-bridge
```

Useful execution options:

- `--timeout 30`: limit each case;
- `--resume`: keep completed cases and run missing ones;
- `--overwrite`: intentionally replace the selected stage file;
- `--id case-id`: select an ID, repeatable;
- `--tag routing`: select cases sharing a tag, repeatable;
- `--cwd path`: run the command from a project directory.

## LangGraph multi-agent demo

AIK includes a real LangGraph example based on LangChain's official tool-based subagents pattern: one supervisor coordinates billing and technical specialists. The mock mode runs the complete graph and AIK workflow without an API key:

```bash
python -m pip install -r examples/langgraph-multi-agent/requirements.txt
python examples/langgraph-multi-agent/run_demo.py --mode mock
```

To use OpenAI for the agent, set `OPENAI_API_KEY` and replace `--mode mock` with `--mode openai`. To keep the mock agent but evaluate its semantic rubric with OpenAI, add `--judge openai`. See [the demo guide](examples/langgraph-multi-agent/README.md) and [the reusable adapter contract](.agents/skills/improve-agent/references/langgraph-adapter.md).

## Champion history and local dashboard

Preserve an evaluated campaign as immutable history, then generate a standalone local
dashboard that answers "are we better than before, is the candidate eligible, and did
anything regress" without opening raw JSON:

```bash
python aik.py history init improvements/my-agent-failure --stage baseline --label "Initial version" --agent-version v1
python aik.py history record improvements/my-agent-failure --stage candidate --label "Fix" --agent-version v2
python aik.py history promote <run-id>
python aik.py dashboard
```

Then open `aik-history/dashboard.html`. History recording never reruns an agent; it
only archives evidence Sprint 4 (`aik evaluate` / `aik review`) already produced.
Promotion requires a hash-verified, non-stale, cohort-compatible candidate with an
`eligible` recommendation and an accepted human decision — there is no bypass. See
[the history and dashboard reference](.agents/skills/improve-agent/references/history-dashboard.md).
Try the included demo:

```bash
python examples/langgraph-multi-agent/history-demo/build_history_demo.py
```

which archives the anchor, shows a pending decision correctly blocking promotion,
accepts and promotes the candidate, and generates
`examples/langgraph-multi-agent/history-demo/aik-history/dashboard.html`.

## Statistical reliability

One run cannot tell you whether an improvement is repeatable or agent/LLM-judge
noise. Repeat it and measure:

```bash
python aik.py run improvements/my-agent-failure --stage baseline --repetitions 5 -- python path/to/my_runner.py
python aik.py run improvements/my-agent-failure --stage candidate --repetitions 5 -- python path/to/my_runner.py
python aik.py evaluate improvements/my-agent-failure --stage baseline --trials
python aik.py evaluate improvements/my-agent-failure --stage candidate --trials
python aik.py reliability improvements/my-agent-failure
python aik.py review improvements/my-agent-failure
```

`--repetitions` defaults to `1` and is unchanged from Sprint 4/5 at that default.
`aik reliability` computes pass rate, a Wilson confidence interval, a deterministic
seeded bootstrap for the delta and `P(candidate > baseline)`, flaky cases, and
evaluator version incompatibilities — standard-library only, and it never converts a
missing or errored observation into a zero score. An optional `reliability` block in
`evaluation.json` can gate acceptance on this evidence, but a statistical result can
never override a deterministic rejection, and insufficient evidence can only push a
recommendation toward `manual_review`, never toward `eligible`. See
[the reliability reference](.agents/skills/improve-agent/references/statistical-reliability.md).
Try the included offline demo:

```bash
python examples/langgraph-multi-agent/history-demo/build_reliability_demo.py
```

which runs a small suite through the real runner bridge with `--repetitions`, computes
`reliability.json`, and shows a case that looks like a clean improvement on a single
run but is correctly withheld from promotion because the repeated trials do not meet
the configured probability threshold.

## What is tracked

Every test case is classified as:

- `improved`: failed before and passes now;
- `regressed`: passed before and fails now;
- `unchanged`: the pass/fail outcome is unchanged;
- `not_comparable`: evidence is missing or invalid.

Each comparison also contains `eligible`, `rejected`, or `manual_review`, policy reasons, baseline and candidate pass rates, delta, and dataset/evaluator fingerprints. Optional fields capture semantic scores, evidence, tool calls, agents, latency, and cost. See [the data format](.agents/skills/improve-agent/references/data-format.md) and [evaluation guide](.agents/skills/improve-agent/references/evaluation.md).

## Repository layout

```text
aik.py                                  Single user-facing CLI
.agents/skills/improve-agent/           Portable Agent Skill
datasets/regression.jsonl               Permanent accepted tests
aik-history/                            Immutable champion history and local dashboard (generated)
examples/                               Anonymized working example
tests/                                  Dependency-free test suite
```

Compatible agents can discover `.agents/skills/improve-agent/SKILL.md`. A user or agent can also run `aik.py` directly; the workflow and runner protocol do not depend on a specific host or SDK.

## Safety boundary

AIK never embeds credentials, deploys a prompt, or decides acceptance automatically. `aik run` executes the command you explicitly provide with your current permissions; inspect it first. Promotion is blocked until `decision.yaml` contains `accepted` or `accepted_with_conditions`. Read [SECURITY.md](SECURITY.md) before running third-party code or committing real conversations.

## Development

```bash
python -m compileall -q aik.py .agents tests examples
python -m unittest discover -s tests -v
python aik.py validate examples/support-agent/confirmation-before-action
python aik.py history validate
python aik.py dashboard
python aik.py --version
```

MIT licensed. Python 3.10+.
