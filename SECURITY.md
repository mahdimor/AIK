# Security and data handling

AIK stores agent inputs, outputs, tool arguments, and evaluation evidence as plain local files. These artifacts may contain sensitive data.

- Redact secrets, tokens, personal data, and internal identifiers before committing or sharing a case.
- Use synthetic or anonymized examples in public repositories.
- Review `tool_calls`, `output`, and `metadata` fields before promotion.
- Report a vulnerability privately to the repository maintainer rather than opening a public issue with sensitive evidence.

AIK core does not directly call an SDK, MCP server, tool, model, or production system. `aik run` and external `aik evaluate` judges execute user-supplied subprocesses with the current user's permissions. Inspect them before execution, avoid shell wrappers, and keep credentials in the environment rather than AIK artifacts.

An external semantic judge receives the case input, expectations, rubric, agent output, agents, and tool calls. Using `scripts/openai_judge.py` sends that material to the OpenAI API with `store=false`. Do not enable any remote judge until the dataset is approved for that provider. Provider-side retention and account policies remain outside AIK's control.
