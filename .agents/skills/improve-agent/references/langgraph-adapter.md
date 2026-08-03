# LangGraph adapter

Use the adapter when a project exposes a compiled graph or graph factory:

```bash
python aik.py run improvements/my-case --stage baseline --cwd my-project -- \
  python .agents/skills/improve-agent/scripts/langgraph_runner.py \
  --graph my_agent.graph:build_graph \
  --transform my_agent.aik:to_evidence
```

The graph reference resolves to an object with `invoke(input)` or a factory. A factory may accept `stage`.

The optional transform maps framework state to raw portable evidence; it does not judge correctness:

```python
def to_evidence(*, case, result, stage):
    return {
        "output": result["final_answer"],
        "agents": result.get("agents_called", []),
        "tool_calls": result.get("tool_calls", []),
        "metadata": {"stage": stage},
    }
```

Then run `aik evaluate` against `evaluation.json`. Keep deterministic contracts in AIK and use an external LLM judge only for semantic rubrics.

AIK imports project modules from `--cwd`. The adapter forwards incidental stdout to stderr to preserve the JSON protocol. `--evaluator` remains a deprecated alias for `--transform` for v0.3 compatibility.
