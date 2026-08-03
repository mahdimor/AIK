# Security and data handling

AIK stores agent inputs, outputs, tool arguments, and evaluation evidence as plain local files. These artifacts may contain sensitive data.

- Redact secrets, tokens, personal data, and internal identifiers before committing or sharing a case.
- Use synthetic or anonymized examples in public repositories.
- Review `tool_calls`, `output`, and `metadata` fields before promotion.
- Report a vulnerability privately to the repository maintainer rather than opening a public issue with sensitive evidence.

AIK does not directly call an SDK, MCP server, tool, model, or production system. The optional `aik run` command executes a user-supplied subprocess with the current user's permissions. Inspect runner code before execution, avoid shell wrappers, and keep credentials in the runner environment rather than AIK artifacts.
