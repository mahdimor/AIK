# AIK — Agent Improvement Kit

AIK is a small, local-first Agent Skill that turns an AI-agent failure into a reproducible before/after review and a permanent regression test.

It is not an evaluation platform and does not embed an agent SDK. It can compare existing JSONL evidence or execute project-owned runners through a small JSON-over-stdin/stdout protocol. No cloud account, database, framework SDK, or third-party Python dependency is required by AIK.

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

3. Record the same cases before and after the change. Files can be written by an existing test harness, or generated through any project-owned command:

   ```bash
   python aik.py run improvements/my-agent-failure --stage baseline -- python path/to/my_runner.py
   # Change the prompt, model, tools, routing, or agent code.
   python aik.py run improvements/my-agent-failure --stage candidate -- python path/to/my_runner.py
   ```

   The runner may internally use MCP, an SDK, HTTP, a local model, or a deterministic mock. It also owns the `passed` verdict; AIK does not guess correctness from free-form text. See [the runner protocol](.agents/skills/improve-agent/references/runner-protocol.md).

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

To use OpenAI, set `OPENAI_API_KEY` and replace `--mode mock` with `--mode openai`. See [the demo guide](examples/langgraph-multi-agent/README.md) and [the reusable adapter contract](.agents/skills/improve-agent/references/langgraph-adapter.md).

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

Compatible agents can discover `.agents/skills/improve-agent/SKILL.md`. A user or agent can also run `aik.py` directly; the workflow and runner protocol do not depend on a specific host or SDK.

## Safety boundary

AIK never embeds credentials, deploys a prompt, or decides acceptance automatically. `aik run` executes the command you explicitly provide with your current permissions; inspect it first. Promotion is blocked until `decision.yaml` contains `accepted` or `accepted_with_conditions`. Read [SECURITY.md](SECURITY.md) before running third-party code or committing real conversations.

## Development

```bash
python -m compileall -q aik.py .agents tests
python -m unittest discover -s tests -v
python aik.py validate examples/support-agent/confirmation-before-action
```

MIT licensed. Python 3.10+.
