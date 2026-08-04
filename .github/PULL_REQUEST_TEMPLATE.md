## What changed

<!-- Describe the user-visible behavior and why this change is needed. -->

## Evidence

<!-- Link an issue or include a minimal synthetic reproduction. -->

## Validation

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m compileall -q aik.py .agents tests examples`
- [ ] Relevant example/case validation passes
- [ ] `git diff --check`

## Safety and compatibility

- [ ] No credentials, personal data, private prompts, or internal traces are included
- [ ] Protocol/format/statistical changes include tests and documentation
- [ ] The change does not let aggregate or statistical scores hide a regression
- [ ] I documented any backward-compatibility impact
