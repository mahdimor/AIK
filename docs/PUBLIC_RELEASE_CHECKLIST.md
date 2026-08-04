# Public release checklist

Use this checklist immediately before changing the repository visibility. It is not
an instruction to publish automatically.

## Repository content

- [ ] `main` contains only reviewed commits and the working tree is clean.
- [ ] Full tests, compilation, example validation, and history integrity checks pass.
- [ ] No credential, private key, token, personal data, customer content, internal
  hostname, or private prompt exists in the current tree or Git history.
- [ ] Example conversations and generated histories are synthetic and approved for
  public use.
- [ ] Commit author names and emails in the complete Git history are acceptable for
  public disclosure. If not, rewrite them while the repository is still private,
  then force-push all affected refs deliberately.
- [ ] LICENSE, README, SECURITY, CONTRIBUTING, SUPPORT, CHANGELOG, and Code of Conduct
  are current.

## GitHub settings

- [ ] Set a concise repository description and topics such as `ai-agents`,
  `agent-evaluation`, `llm-evaluation`, `regression-testing`, and `agent-skills`.
- [ ] Enable Issues.
- [ ] Enable private vulnerability reporting under **Settings → Security**.
- [ ] Protect `main`: require pull requests, require the CI checks, block force pushes,
  and require the branch to be current before merging.
- [ ] Enable Dependabot alerts and security updates.
- [ ] Confirm GitHub Actions permissions are read-only by default.
- [ ] Decide whether Discussions are useful; do not enable them without a moderation
  plan.

## First public release

- [ ] Merge the reviewed readiness PR.
- [ ] Create an annotated pre-`1.0` tag only after the commit is final.
- [ ] Publish release notes from CHANGELOG and repeat the experimental-status warning.
- [ ] Verify the README badges, issue forms, security link, and CI from a signed-out
  browser session.
- [ ] Run GitHub's community profile check and resolve unintended gaps.

## Positioning

Public descriptions must not claim that AIK proves production safety or measures real
user satisfaction. AIK produces reproducible evaluation evidence and conservative
promotion recommendations; production monitoring, real feedback, security review,
and accountable human release decisions remain separate responsibilities.
