# Security policy and data handling

## Supported versions

AIK is pre-`1.0`. Security fixes are applied to the latest code on `main`; older
snapshots are not maintained as separate support lines.

| Version | Supported |
| --- | --- |
| Latest `main` / latest release | Yes |
| Older versions | No |

## Reporting a vulnerability

Do not disclose a vulnerability or sensitive reproduction in a public issue.

When the repository is public and private vulnerability reporting is enabled, use
GitHub's **Security → Advisories → Report a vulnerability** flow. Include the affected
version or commit, impact, reproduction steps, and any proposed mitigation. If that
button is unavailable, open a public issue containing no vulnerability details and
ask the maintainer to establish a private channel.

Reports are handled on a best-effort basis. The maintainer will aim to acknowledge a
complete report within seven days, validate it, coordinate a fix and disclosure, and
credit the reporter unless anonymity is requested. Please allow a reasonable period
for remediation before public disclosure.

## Data handling

AIK stores agent inputs, outputs, tool arguments, and evaluation evidence as plain local files. These artifacts may contain sensitive data.

- Redact secrets, tokens, personal data, and internal identifiers before committing or sharing a case.
- Use synthetic or anonymized examples in public repositories.
- Review `tool_calls`, `output`, and `metadata` fields before promotion.
- Follow the private reporting process above rather than opening a public issue with sensitive evidence.

AIK core does not directly call an SDK, MCP server, tool, model, or production system. `aik run` and external `aik evaluate` judges execute user-supplied subprocesses with the current user's permissions. Inspect them before execution, avoid shell wrappers, and keep credentials in the environment rather than AIK artifacts.

`aik history init`/`record` archive a copy of the same evidence into `aik-history/runs/`; treat that directory with the same care as the source case, since it is never rewritten or redacted after archiving. `aik dashboard` renders locally with escaped HTML and no remote asset or network request; it never embeds raw agent outputs, only links back to the archived `review.html`.

`aik run --repetitions N` executes the same runner command N times per case with the current user's permissions; it never changes the runner protocol or sandboxes the command. `reliability.json` contains only aggregated statistics (counts, rates, confidence intervals) derived from evidence already covered by the notes above, and it is archived and hash-verified the same way as `comparison.json`.

An external semantic judge receives the case input, expectations, rubric, agent output, agents, and tool calls. Using `scripts/openai_judge.py` sends that material to the OpenAI API with `store=false`. Do not enable any remote judge until the dataset is approved for that provider. Provider-side retention and account policies remain outside AIK's control.
