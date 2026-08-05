# Contributing to AIK

Thanks for helping make agent improvements more reproducible and reviewable.

## Scope

AIK is local-first, framework-agnostic, and dependency-free at its core. A proposed
change should preserve these boundaries unless a design discussion explicitly agrees
otherwise. Provider and framework integrations belong behind subprocess adapters or
in optional examples.

Good contributions include:

- evaluator and acceptance-gate correctness;
- runner adapters that preserve the provider-neutral protocol;
- dataset construction and review usability;
- regression, reliability, and history integrity tests;
- documentation and anonymized examples.

Please open a feature request before a large architectural change.

## Development setup

Requirements: Git and Python 3.10 or newer. The core test suite has no third-party
dependency.

```bash
git clone https://github.com/mahdimor/AIK.git
cd AIK
python -m unittest discover -s tests -v
python aik.py demo
```

The optional LangGraph demo has isolated dependencies:

```bash
python -m venv .venv
python -m pip install -r examples/langgraph-multi-agent/requirements.txt
python examples/langgraph-multi-agent/run_demo.py --mode mock
```

## Pull requests

1. Create a focused branch from `main`.
2. Add or update tests for behavior changes.
3. Keep generated evidence synthetic and free of credentials, personal data, and
   internal identifiers.
4. Update the relevant reference document when changing a protocol, format,
   evaluator, acceptance rule, or statistical method.
5. Run the validation commands below.
6. Explain the user-visible behavior and any compatibility tradeoff in the PR.

```bash
python -m compileall -q aik.py .agents tests examples
python -m unittest discover -s tests -v
python aik.py validate examples/support-agent/confirmation-before-action
python aik.py history validate --history-root examples/langgraph-multi-agent/history-demo/aik-history
python scripts/build_showcase.py
git diff --check
```

Statistical changes must include an adversarial test and must not turn insufficient
or errored evidence into eligibility. Security fixes should follow
[SECURITY.md](SECURITY.md), not a public issue.

## Code style

- Prefer the Python standard library in AIK core.
- Keep functions small, deterministic where practical, and explicit about malformed
  evidence.
- Escape all untrusted content rendered into HTML.
- Do not hide regressions behind aggregate scores.
- Keep raw execution evidence separate from evaluated evidence.
- Use clear commit messages; Conventional Commit prefixes are welcome but optional.

## Review and licensing

Maintainers may ask for changes, additional evidence, or a smaller scope. By
submitting a contribution, you agree that it is licensed under the repository's MIT
License and that you have the right to contribute it.
