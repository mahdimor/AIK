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

PowerShell:

```powershell
$env:OPENAI_API_KEY="..."
python examples/langgraph-multi-agent/run_demo.py --mode openai
```

Override the model with `--model` or `AIK_DEMO_MODEL`. The default is `gpt-5.4-mini`.

The demo runs the same four cases against an intentionally flawed baseline and a corrected candidate, then generates `case/comparison.json` and `case/review.html`.

## Architecture source

- Official pattern: <https://docs.langchain.com/oss/python/langchain/multi-agent/subagents>
- Official tutorial: <https://docs.langchain.com/oss/python/langchain/multi-agent/subagents-personal-assistant>
- Historical supervisor package note: <https://github.com/langchain-ai/langgraph-supervisor-py>
