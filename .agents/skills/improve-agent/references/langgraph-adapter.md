# LangGraph adapter

Use the adapter when a project exposes a compiled graph or a graph factory plus an evaluator.

```bash
python aik.py run improvements/my-case --stage baseline --cwd my-project -- \
  python .agents/skills/improve-agent/scripts/langgraph_runner.py \
  --graph my_agent.graph:build_graph \
  --evaluator my_agent.evals:evaluate
```

The graph reference must resolve to an object with `invoke(input)`, or to a zero-argument factory. A factory may optionally accept `stage`:

```python
def build_graph(stage: str):
    return compiled_graph
```

The evaluator converts framework-specific state into the portable AIK result:

```python
def evaluate(*, case, result, stage):
    return {
        "passed": result["status"] == "ok",
        "output": result["final_answer"],
        "agents": result.get("agents_called", []),
        "tool_calls": result.get("tool_calls", []),
        "contracts": {"correct-routing": "pass"},
    }
```

The evaluator owns correctness. Keep it deterministic when the expected behavior can be checked in code. Use an LLM evaluator only for behavior that genuinely requires semantic judgment.

AIK imports project modules from `--cwd`. The adapter captures incidental standard output and forwards it to standard error so the AIK JSON protocol remains valid.
