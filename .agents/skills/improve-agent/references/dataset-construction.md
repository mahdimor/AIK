# Dataset construction

Build cases from anonymized production incidents, known regressions, business-critical scenarios, boundaries, adversarial inputs, and distinct paraphrases.

1. Record the failure or desired capability.
2. Redact sensitive data.
3. Define expected contracts and rubric before changing the agent.
4. Assign risk, source, split, tags, and stable ID.
5. Review `proposed` cases before marking them `accepted`.
6. Move every accepted fix into `regression`.
7. Keep `holdout` cases outside developer and optimizer feedback. Export optimizer input with `aik feedback`, which excludes the holdout by construction, records every export, and detects a holdout changed mid-optimization. Read `references/optimizer-feedback.md`.
8. Use `challenge` for rare, adversarial, or high-impact behavior.

Optimize for risk coverage, discriminative power, diversity, stability, and low redundancy—not for a high headline score. `aik validate` detects missing governance fields and exact duplicate inputs. Semantic deduplication and coverage trends belong to the dashboard sprint.
