# Sprint 5 — Champion History and Local Dashboard

## Implementation brief

This document is the complete implementation specification for Sprint 5 of AIK. It is intended to be handed directly to an AI coding agent that has no prior conversation context.

Start from repository release `v0.4.0` / commit `597a8d2`.

Do not redesign the Sprint 4 evaluation engine. Consume its generated artifacts and preserve backward compatibility.

## Product outcome

At the end of this sprint, a user can preserve evaluated agent versions as immutable history, identify the fixed anchor and current champion, compare a candidate only on compatible evidence, and open a standalone local dashboard showing level, delta, slope, coverage, and regressions.

The complete AIK workflow becomes:

```text
Define dataset + evaluators + acceptance policy
                    ↓
              Run raw evidence
                    ↓
                 Evaluate
                    ↓
          Compare candidate/champion
                    ↓
              Human decision
                    ↓
       Record immutable history entry
                    ↓
        Promote accepted champion pointer
                    ↓
          Generate local dashboard
```

The dashboard must never imply progress when the underlying dataset or evaluator suite is incompatible.

## Current system at v0.4.0

AIK already provides:

- `aik run` for raw runner evidence;
- `aik evaluate` for deterministic and optional external LLM evaluators;
- Dataset v2 governance fields;
- dataset and evaluator SHA-256 fingerprints;
- `aik review` for baseline/candidate comparison;
- machine recommendations: `eligible`, `rejected`, `manual_review`;
- `decision.yaml` for explicit human acceptance;
- `aik promote` for adding accepted cases to the permanent regression dataset;
- a working offline LangGraph multi-agent demo.

One current comparison has this shape:

```json
{
  "format_version": "1.0",
  "dataset_fingerprint": "sha256:...",
  "evaluator_fingerprint": "sha256:...",
  "summary": {
    "improved": 1,
    "regressed": 0,
    "unchanged": 3,
    "not_comparable": 0,
    "total": 4
  },
  "acceptance": {
    "recommendation": "eligible",
    "metrics": {
      "baseline_pass_rate": 0.75,
      "candidate_pass_rate": 1.0,
      "delta": 0.25
    }
  }
}
```

AIK does not yet retain a sequence of these experiments. `review.html` only explains one before/after campaign.

## Scope decisions

### Local-first and dependency-free

The AIK core must remain Python 3.10+ standard library only.

- No database.
- No web server.
- No Node.js build.
- No React/Vue dashboard.
- No CDN or remote JavaScript.
- No required Promptfoo, Langfuse, LangSmith, or cloud account.

Generate standalone HTML, CSS, and inline SVG. The dashboard must open directly from disk.

### History records evidence; it does not rerun agents

Sprint 5 archives artifacts already produced by Sprint 4. Recording history must not invoke the agent, evaluator, API, SDK, MCP server, or production system.

### Two different promotions remain separate

- Existing `aik promote` promotes test cases into `datasets/regression.jsonl`.
- New `aik history promote` changes the pointer to the current champion.

Do not overload or change the meaning of existing `aik promote`.

### Human approval remains mandatory

`eligible` is only a machine recommendation. A candidate can become champion only when:

1. the archived candidate came from a comparison recommending `eligible`;
2. `decision.yaml` was `accepted` or `accepted_with_conditions` when archived;
3. the candidate is comparable to the champion on the same cohort;
4. all archived artifact hashes still match.

No `--force` bypass is allowed in Sprint 5.

## Vocabulary

- **Campaign**: one Sprint 4 case directory containing baseline, candidate, comparison, review, and decision.
- **History run**: immutable snapshot of one evaluated stage from a campaign.
- **Anchor**: first trusted agent version. It never moves and represents total progress from the beginning.
- **Champion**: current accepted agent version. It may move only through explicit promotion.
- **Candidate**: evaluated version proposed against the current champion.
- **Cohort**: runs sharing the exact same dataset fingerprint and evaluator fingerprint.
- **Cohort anchor**: champion rerun on a new dataset/evaluator cohort, used to start a new comparable trend segment.
- **Level**: pass rate of a run within its cohort.
- **Delta**: candidate level minus the comparable champion level.
- **Slope**: direction of accepted champion performance over time within one cohort.
- **Coverage**: distribution of accepted cases by split, risk, source, and tag. Coverage growth is not quality improvement.

## History storage

Default location at repository root:

```text
aik-history/
  state.json
  runs/
    20260803T220000Z-a1b2c3d4/
      manifest.json
      test_cases.jsonl
      dataset.json
      evaluation.json
      evaluated.jsonl
      comparison.json
      decision.yaml
      review.html
  dashboard.json
  dashboard.html
```

Allow `--history-root PATH` on every new history/dashboard command.

### `state.json`

This is the only mutable history file:

```json
{
  "format_version": "1.0",
  "anchor_run_id": "20260803T220000Z-a1b2c3d4",
  "champion_run_id": "20260810T170000Z-e5f6a7b8",
  "updated_at": "2026-08-10T17:01:00Z"
}
```

All run directories and their contents are immutable after creation.

### Run ID

Generate:

```text
<UTC basic timestamp>-<first 8 chars of snapshot fingerprint>
```

Example:

```text
20260803T220000Z-a1b2c3d4
```

If that directory already exists, fail. Never overwrite or merge an existing history run.

### `manifest.json`

```json
{
  "format_version": "1.0",
  "run_id": "20260803T220000Z-a1b2c3d4",
  "recorded_at": "2026-08-03T22:00:00Z",
  "label": "initial supervisor",
  "agent_version": "demo-v1",
  "stage": "baseline",
  "role": "anchor",
  "source_case": "examples/langgraph-multi-agent/case",
  "parent_champion_run_id": null,
  "dataset_fingerprint": "sha256:...",
  "evaluator_fingerprint": "sha256:...",
  "cohort_id": "sha256:...",
  "snapshot_fingerprint": "sha256:...",
  "case_count": 4,
  "passed": 3,
  "failed": 1,
  "manual_review": 0,
  "pass_rate": 0.75,
  "acceptance_recommendation": null,
  "human_decision": null,
  "artifacts": {
    "test_cases.jsonl": "sha256:...",
    "dataset.json": "sha256:...",
    "evaluation.json": "sha256:...",
    "evaluated.jsonl": "sha256:...",
    "comparison.json": "sha256:...",
    "decision.yaml": "sha256:...",
    "review.html": "sha256:..."
  },
  "metadata": {}
}
```

Rules:

- `role`: `anchor`, `candidate`, `champion`, or `cohort_anchor`.
- A newly recorded accepted candidate still starts with role `candidate`. Promotion changes only `state.json`; do not rewrite its immutable manifest. Dashboard derives whether a run became champion from promotion events or the current pointer.
- To preserve the complete champion timeline without modifying manifests, append immutable promotion events under `aik-history/events.jsonl`.

### Promotion events

`events.jsonl` is append-only:

```json
{
  "event_id": "sha256:...",
  "type": "champion_promoted",
  "recorded_at": "2026-08-10T17:01:00Z",
  "run_id": "20260810T170000Z-e5f6a7b8",
  "previous_champion_run_id": "20260803T220000Z-a1b2c3d4",
  "human_decision": "accepted"
}
```

Write the event before atomically replacing `state.json`. Validation must detect a dangling event or pointer.

## Comparability rules

Compute:

```text
cohort_id = SHA-256(dataset_fingerprint + "\n" + evaluator_fingerprint)
```

Two runs are strictly comparable only when:

- both fingerprints are present;
- dataset fingerprints match exactly;
- evaluator fingerprints match exactly;
- evaluated case IDs overlap sufficiently to calculate the requested metric.

Never compare semantic versions or filenames as a substitute for fingerprints.

### Dataset or evaluator changes

When either fingerprint changes:

1. do not connect the new run to the old quality trend;
2. show `not comparable` for delta against the old champion;
3. instruct the user to rerun the current champion on the new suite;
4. record that rerun with role `cohort_anchor` and the same `agent_version` as the champion;
5. compare future candidates to this cohort anchor/champion rerun;
6. show coverage changes separately from quality changes.

The global anchor pointer never moves, but `delta vs anchor` is available only inside a compatible cohort. Never fabricate a cross-cohort score.

## Metrics

### Level

```text
level = passed cases / cases with a boolean evaluated verdict
```

Display as percentage with the numerator and denominator. Exclude `manual_review` and missing verdicts from the denominator, but display their count prominently.

### Delta against champion

```text
delta_vs_champion = candidate_pass_rate - comparable_champion_pass_rate
```

Only calculate within the same cohort. Express in percentage points, not relative percent.

### Delta against anchor

```text
delta_vs_anchor = run_pass_rate - comparable_anchor_pass_rate
```

If the fixed global anchor is in another cohort, return `null` and explain why.

### Slope

Calculate ordinary least-squares slope over the ordered pass rates of runs that actually became champion within the current cohort:

```text
x = 0, 1, 2, ... accepted champion transition order
y = pass rate
```

Return percentage points per accepted champion. If fewer than two champion points exist in the cohort, slope is `null`, not zero.

Also display the raw trend points. Do not label the slope as statistically significant.

### Per-evaluator scores

For every evaluator ID:

- pass/fail/error/not-applicable counts;
- mean numeric score when present;
- delta against comparable champion when both contain a numeric score;
- number of cases contributing to the mean.

Do not average different evaluator IDs into one quality score.

### Regressions

Show:

- total improved/regressed/unchanged/not-comparable;
- regressions grouped by `risk`;
- regressions grouped by `tag`;
- every high/critical regression as an individual dashboard item;
- evaluator responsible for each failed candidate case.

### Coverage

Calculate from accepted, non-retired dataset cases:

- total cases;
- cases by `split`;
- cases by `risk`;
- cases by `source`;
- cases by tag;
- number of untagged cases;
- number of high/critical cases;
- exact duplicate inputs if any.

Coverage changes must be presented as `+N cases` or distribution changes, not as model improvement.

### LLM judge variance

The v0.4 format normally contains one semantic judgment per evaluator/case. Do not invent a variance metric. Show variance or disagreement only when future/imported artifacts contain multiple numeric observations with an explicit shared evaluator version. Otherwise display `not available`.

## CLI

Add nested history commands and one dashboard command.

### Initialize anchor

```bash
python aik.py history init path/to/case \
  --stage baseline \
  --label "Initial supervisor" \
  --agent-version "demo-v1"
```

Behavior:

- require a non-empty evaluated stage;
- validate all Sprint 4 artifacts and fingerprints;
- require no existing anchor;
- archive an immutable run with role `anchor`;
- set both anchor and champion pointers to this run;
- append an `anchor_initialized` event;
- print `run_id`, `pass_rate`, `cohort_id`, and paths.

### Record candidate or cohort anchor

```bash
python aik.py history record path/to/case \
  --stage candidate \
  --label "Fixed mixed routing" \
  --agent-version "demo-v2"
```

Optional:

```bash
--role candidate|cohort_anchor
--metadata key=value
--history-root PATH
```

Behavior:

- default role is `candidate`;
- archive evaluated evidence and available review artifacts;
- preserve acceptance recommendation and human decision from the campaign;
- set `parent_champion_run_id` to current champion;
- do not alter anchor/champion pointers;
- if role is `candidate` and cohort differs from champion, record it but mark it non-comparable;
- role `cohort_anchor` requires `agent_version` to match the current champion and the stage to represent a rerun of that champion.
- recording a cohort anchor appends a `cohort_anchor_recorded` event; it does not move the global anchor or champion pointers.

A valid cohort anchor is the latest uncorrupted `cohort_anchor` run whose `parent_champion_run_id` equals the current champion and whose `agent_version` matches that champion. A candidate on a new cohort is comparable against this run, not against the old-cohort champion score.

### Promote champion

```bash
python aik.py history promote <run-id>
```

Behavior:

- verify all artifact hashes;
- require role `candidate`;
- require current pointer equals `parent_champion_run_id` to prevent stale promotion;
- require the same cohort as the current champion, or the same cohort as a valid cohort anchor linked to that champion;
- when using a cohort anchor, calculate the candidate delta against the cohort-anchor rerun and require that the archived campaign baseline represents that same evidence;
- require archived recommendation `eligible`;
- require archived human decision `accepted` or `accepted_with_conditions`;
- append promotion event;
- atomically update champion pointer;
- never rewrite the run manifest.

### Validate history

```bash
python aik.py history validate
```

Validate:

- state schema;
- anchor and champion pointers;
- unique run IDs;
- manifest schemas;
- all artifact hashes;
- snapshot fingerprints;
- event chain;
- parent champion references;
- cohort IDs;
- promotion eligibility evidence;
- no modified or missing archived artifact.

Return non-zero on corruption.

### List history

```bash
python aik.py history list
```

Print a stable human-readable table with run ID, date, label, agent version, cohort prefix, pass rate, recorded role, and whether the run is current anchor/champion.

### Generate dashboard

```bash
python aik.py dashboard
```

Options:

```text
--history-root PATH
--output PATH
--candidate RUN_ID
--open             optional; do not implement if it requires platform-specific dependencies
```

Generate both:

- `aik-history/dashboard.json`: canonical machine-readable dashboard model;
- `aik-history/dashboard.html`: standalone human-readable dashboard.

Do not automatically open a browser unless `--open` is explicitly implemented and requested.

## Dashboard information architecture

The first screen must answer five questions without opening raw JSON:

1. What is the current champion?
2. Are we better than the comparable anchor/champion?
3. Is the candidate eligible or rejected, and why?
4. Did any behavior regress?
5. Is the dataset coverage growing or merely changing?

### Header

- AIK project/history name;
- generated timestamp;
- current cohort prefix;
- global anchor version;
- current champion version;
- candidate version when selected;
- visible warning when evidence is incompatible or incomplete.

### KPI row

- champion level with numerator/denominator;
- candidate level;
- delta vs champion;
- delta vs anchor;
- accepted-run slope;
- candidate recommendation;
- regression count;
- manual-review count.

Use `N/A` plus a reason for unavailable metrics. Never display unavailable values as zero.

### Progress chart

Render an inline SVG line chart:

- x-axis: accepted champion order/date;
- y-axis: pass rate from 0–100%;
- one disconnected line per cohort;
- anchor and current champion markers;
- candidate marker must use a different visual style and must not appear on the accepted line before promotion;
- accessible text/table fallback below the SVG.

### Regression panel

- high and critical regressions first;
- case ID, description, risk, tags, failing evaluator IDs;
- link to archived `review.html` for complete evidence;
- clear empty state: `No detected regressions`.

### Evaluator panel

Table columns:

```text
Evaluator | Champion score | Candidate score | Delta | Pass | Fail | Error | N/A | Cases scored
```

Keep deterministic contract scores and semantic scores identifiable. Do not merge them into one opaque average.

### Coverage panel

- totals and distribution tables for split, risk, source, and tags;
- coverage delta against the previous comparable dataset snapshot when available;
- warnings for no regression cases, no holdout cases, no high/critical cases, untagged cases, or duplicate inputs;
- no semantic deduplication in this sprint.

### Timeline

For every history run:

- timestamp;
- label and agent version;
- stage and recorded role;
- cohort prefix;
- pass rate;
- acceptance recommendation;
- human decision;
- promotion event indicator;
- links to archived comparison and review.

## `dashboard.json`

Use a stable, testable intermediate model. Minimum shape:

```json
{
  "format_version": "1.0",
  "generated_at": "...",
  "state": {
    "anchor_run_id": "...",
    "champion_run_id": "...",
    "selected_candidate_run_id": "..."
  },
  "comparability": {
    "comparable": true,
    "cohort_id": "sha256:...",
    "reasons": []
  },
  "kpis": {
    "champion_level": 1.0,
    "candidate_level": 1.0,
    "delta_vs_champion": 0.0,
    "delta_vs_anchor": 0.25,
    "slope": 0.25,
    "regressions": 0,
    "manual_review": 0
  },
  "trend": [],
  "evaluators": [],
  "coverage": {},
  "regressions": [],
  "timeline": [],
  "warnings": []
}
```

The renderer consumes this model. Do not calculate business metrics again inside HTML rendering code.

## Snapshot and filesystem safety

- Resolve all source and destination paths.
- Refuse history roots that resolve inside a run snapshot.
- Copy files atomically through temporary files/directories in the history root.
- Validate that every archived source is a regular file.
- Do not follow symlinks outside the source campaign or history root.
- Escape every value rendered into HTML.
- Never embed raw secrets or environment variables in manifests.
- Do not include raw agent outputs directly in the dashboard; archived review remains the drill-down.
- Record relative paths in manifests when possible for portability.

## Immutability rules

The application must detect, not silently repair, a modified run snapshot.

- `history validate` fails on changed artifacts.
- `history promote` fails on changed artifacts.
- `dashboard` displays a corruption warning and exits non-zero unless a future explicit recovery workflow exists.
- Re-recording the same source creates a new run or reports an exact duplicate; it never overwrites the prior run.

## Files to add or change

Recommended implementation layout:

```text
aik.py
.agents/skills/improve-agent/SKILL.md
.agents/skills/improve-agent/scripts/history.py
.agents/skills/improve-agent/scripts/dashboard_model.py
.agents/skills/improve-agent/scripts/render_dashboard.py
.agents/skills/improve-agent/assets/dashboard.css
.agents/skills/improve-agent/references/history-dashboard.md
examples/langgraph-multi-agent/history-demo/   # generated safe example
tests/test_history.py
tests/test_dashboard.py
README.md
SECURITY.md
```

Reuse helpers in `common.py`. Keep business logic independent from argparse so tests can call Python functions directly.

Do not add a README inside the skill folder. Put detailed operational guidance in `references/history-dashboard.md` and keep `SKILL.md` concise.

## Work breakdown

### Story 1 — Immutable history model

- Implement manifests, fingerprints, artifact hashing, run IDs, state, and events.
- Add atomic creation and corruption detection.
- Add history-root override.

Acceptance: recording creates a complete immutable snapshot; modifying one archived byte makes validation fail with the exact affected artifact.

### Story 2 — Anchor, candidate, and champion lifecycle

- Implement `history init`, `record`, `promote`, `list`, and `validate`.
- Enforce human decision, policy eligibility, parent champion, and cohort compatibility.
- Preserve anchor while champion advances.

Acceptance: a pending, rejected, stale, incompatible, or corrupted candidate cannot become champion.

### Story 3 — Dashboard model

- Load and validate history.
- Select candidate explicitly or use the latest unpromoted candidate.
- Compute comparability, KPI metrics, OLS slope, evaluator summaries, coverage, regressions, and timeline.
- Emit canonical `dashboard.json`.

Acceptance: all unavailable metrics are `null` with explanatory warnings; incompatible cohorts never produce a delta or connected trend.

### Story 4 — Standalone HTML dashboard

- Render the dashboard model to escaped standalone HTML.
- Add inline SVG progress chart and accessible fallback table.
- Add KPI, regression, evaluator, coverage, and timeline panels.
- Link archived reviews with portable relative links.

Acceptance: dashboard opens from disk with no server/network and answers the five product questions from the first screen.

### Story 5 — LangGraph history demo

- Use safe copied demo artifacts or a deterministic setup script.
- Initialize the v0.4 baseline as anchor/champion.
- Record the improved candidate.
- Demonstrate that pending decision blocks promotion.
- In a copied fixture, mark accepted and promote.
- Generate the final dashboard.

Expected accepted-demo metrics:

```text
anchor level: 75%
champion level after promotion: 100%
delta vs anchor: +25 percentage points
regressions: 0
current recommendation: eligible/accepted
```

### Story 6 — Skill and user documentation

- Add history workflow to `SKILL.md` without duplicating the detailed reference.
- Document commands, semantics, comparability, security, and dashboard interpretation.
- Explain that coverage growth and quality growth are separate.
- Explain the rerun requirement when fingerprints change.

## Test matrix

Automated tests must cover at least:

### History

- initialize anchor and champion together;
- refuse a second anchor initialization;
- record candidate without changing champion;
- immutable run IDs and no overwrite;
- exact artifact hashes and snapshot fingerprint;
- missing artifact detection;
- modified artifact detection;
- invalid/dangling state pointer;
- invalid/dangling promotion event;
- relative-path portability;
- history root override.

### Promotion

- accepted eligible candidate succeeds;
- pending human decision fails;
- rejected human decision fails;
- machine-rejected candidate fails;
- manual-review candidate fails;
- stale parent champion fails;
- incompatible cohort fails;
- corrupted snapshot fails;
- anchor pointer never changes;
- promotion event and champion pointer are consistent.

### Metrics

- level excludes non-boolean verdicts and reports their count;
- delta uses percentage points;
- delta is `null` across cohorts;
- slope is `null` with fewer than two accepted champion points;
- OLS slope is correct for three or more known points;
- candidate is excluded from accepted trend before promotion;
- evaluator means use only numeric scores from the same evaluator ID/version;
- regression grouping by risk/tag is correct;
- coverage excludes retired/proposed cases as specified;
- coverage change does not alter quality delta.

### Rendering and security

- HTML escapes labels, descriptions, tags, and reasons;
- malicious HTML in dataset or metadata is not executable;
- dashboard contains no remote assets;
- SVG has accessible label/fallback;
- archived review links are relative and valid;
- empty history, no candidate, no regression, and incompatible cohort states render clearly.

### Compatibility

- all Sprint 4 tests continue to pass;
- `aik demo` continues to work;
- LangGraph mock run → evaluate → review continues to work;
- history/dashboard features do not require LangGraph dependencies.

## Quality gate

Run from the repository root:

```bash
python -m compileall -q aik.py .agents tests examples
python -m unittest discover -s tests -v
python aik.py demo
python aik.py validate examples/langgraph-multi-agent/case
python aik.py history validate
python aik.py dashboard
```

Also run the optional LangGraph suite in the existing `.venv-langgraph` environment when available.

Validate the skill with the official `skill-creator/scripts/quick_validate.py` workflow.

## Definition of done

- History snapshots are immutable and hash-verified.
- Fixed anchor and mutable champion pointers are explicit.
- Champion promotion requires both machine eligibility and archived human acceptance.
- Stale or incompatible candidates cannot be promoted.
- Dataset/evaluator fingerprint changes segment the trend.
- Level, deltas, and slope follow the formulas in this document.
- Coverage and quality are displayed separately.
- `dashboard.json` contains the canonical computed model.
- `dashboard.html` is standalone, escaped, local, and human-readable.
- The dashboard never displays a fake zero for unavailable evidence.
- Existing v0.4 workflows and tests remain operational.
- Core remains standard-library-only and vendor agnostic.
- LangGraph demo proves anchor → candidate → accepted champion → dashboard.
- Documentation is sufficient for a new user and another coding agent.

## Explicit non-goals

- hosted dashboard or multi-user service;
- database, authentication, or RBAC;
- live production monitoring;
- automatic ingestion from Langfuse, Promptfoo, or LangSmith;
- automatic agent deployment;
- automatic acceptance or champion promotion;
- GEPA or other prompt optimization;
- statistical significance claims;
- repeated-run confidence intervals;
- semantic dataset deduplication;
- real user satisfaction collection;
- modification of archived history artifacts.

## Suggested sequence and estimate

One focused sprint of approximately 6–8 engineering days:

1. days 1–2: history schemas, hashing, atomic snapshots, validation;
2. days 3–4: lifecycle commands and champion promotion rules;
3. day 5: dashboard model and metric tests;
4. day 6: standalone HTML and inline SVG;
5. days 7–8: demo, security edge cases, documentation, compatibility tests.

If constrained to five days, deliver history, promotion, canonical dashboard JSON, KPI/timeline/regression HTML, and compatibility tests. Defer only the richer SVG and coverage visual polish—not comparability or integrity checks.

## Handoff instruction to the implementing agent

Implement this sprint iteratively. Preserve a working repository after every story. Do not add external runtime dependencies. Inspect the existing v0.4 schemas and tests before editing. Treat hashes, human approval, cohort compatibility, HTML escaping, and backward compatibility as release-blocking requirements.

The final handoff must include:

- concise implementation summary;
- exact commands executed;
- test counts and results;
- generated dashboard path;
- demo metrics;
- commit hash and release tag proposal;
- explicit disclosure of any deferred item or untested external integration.
