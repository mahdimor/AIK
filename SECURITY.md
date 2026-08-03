# Security and data handling

AIK stores agent inputs, outputs, tool arguments, and evaluation evidence as plain local files. These artifacts may contain sensitive data.

- Redact secrets, tokens, personal data, and internal identifiers before committing or sharing a case.
- Use synthetic or anonymized examples in public repositories.
- Review `tool_calls`, `output`, and `metadata` fields before promotion.
- Report a vulnerability privately to the repository maintainer rather than opening a public issue with sensitive evidence.

AIK does not execute agents, call tools, deploy prompts, or connect to production systems.
