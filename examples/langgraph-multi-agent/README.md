# LangGraph multi-agent demo

This demo adapts LangChain's official tool-based subagents pattern: one supervisor delegates to two stateless specialists and synthesizes their results. It intentionally avoids the older `langgraph-supervisor` package because its maintainers now recommend the direct tool-based pattern for most use cases.

## Setup

Use Python 3.11–3.13:

```bash
python -m venv .venv
python -m pip install -r examples/langgraph-multi-agent/requirements.txt
```

Run locally without an API key or network calls:

```bash
python examples/langgraph-multi-agent/run_demo.py --mode mock
```

Run with OpenAI:

```bash
export OPENAI_API_KEY="..."
python examples/langgraph-multi-agent/run_demo.py --mode openai
```

Run the deterministic mock agent while using OpenAI only as the semantic task-completion judge:

```bash
python examples/langgraph-multi-agent/run_demo.py --mode mock --judge openai
```

PowerShell:

```powershell
$env:OPENAI_API_KEY="..."
python examples/langgraph-multi-agent/run_demo.py --mode openai
```

Override the model with `--model` or `AIK_DEMO_MODEL`. The default is `gpt-5.6-luna` for a cost-sensitive demo.

The demo runs the same four governed cases against an intentionally flawed baseline and a corrected candidate. The runner records raw LangGraph evidence; AIK evaluates routing independently, optionally applies the LLM rubric, enforces the acceptance policy, and generates `case/comparison.json` and `case/review.html`.

Expected offline result:

```text
baseline pass rate: 0.75
candidate pass rate: 1.00
delta: +0.25
recommendation: eligible
```

## Architecture source

- Official pattern: <https://docs.langchain.com/oss/python/langchain/multi-agent/subagents>
- Official tutorial: <https://docs.langchain.com/oss/python/langchain/multi-agent/subagents-personal-assistant>
- Historical supervisor package note: <https://github.com/langchain-ai/langgraph-supervisor-py>
