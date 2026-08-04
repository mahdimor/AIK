# Champion history and local dashboard

Sprint 5 archives evidence Sprint 4 already produced (`aik evaluate`, `aik review`) as
immutable, hash-verified history runs, then renders a standalone local dashboard.
History recording never invokes an agent, evaluator, API, SDK, MCP server, or
production system — it only copies and hashes files that already exist.

## Vocabulary

- **Campaign**: one Sprint 4 case directory (baseline, candidate, comparison, review, decision).
- **History run**: an immutable snapshot of one evaluated stage from a campaign.
- **Anchor**: the first trusted agent version. It never moves; it represents total progress from the beginning.
- **Champion**: the current accepted agent version. It moves only through explicit `history promote`.
- **Candidate**: an evaluated version archived for comparison against the current champion.
- **Cohort**: history runs that share the exact same dataset fingerprint and evaluator fingerprint.
- **Cohort anchor**: the champion rerun on a new dataset/evaluator cohort, used to start a new comparable trend segment.
- **Level**: pass rate of a run within its cohort (`passed / (passed + failed)`, excluding `manual_review` and missing verdicts).
- **Delta**: candidate level minus a comparable champion or anchor level, in percentage points.
- **Slope**: OLS slope of accepted champion pass rates within one cohort, in percentage points per accepted champion.
- **Coverage**: distribution of accepted, non-retired dataset cases by split/risk/source/tag. Coverage growth is not quality improvement.

## Storage layout

```text
aik-history/
  state.json        # the only mutable file: anchor_run_id, champion_run_id, updated_at
  events.jsonl       # append-only: anchor_initialized, cohort_anchor_recorded, champion_promoted
  runs/
    <run_id>/
      manifest.json       # immutable: fingerprints, cohort_id, pass_rate, role, artifact hashes
      test_cases.jsonl
      dataset.json
      evaluation.json
      evaluated.jsonl      # the archived stage, renamed generically
      comparison.json      # present when the campaign had one
      decision.yaml        # present when the campaign had one
      review.html          # present when the campaign had one
  dashboard.json      # canonical machine-readable dashboard model
  dashboard.html      # standalone human-readable dashboard
```

`run_id` is `<UTC basic timestamp>-<first 8 hex chars of the snapshot fingerprint>`, e.g.
`20260803T220000Z-a1b2c3d4`. Re-recording byte-identical evidence returns the same run
and is reported as a duplicate; it never overwrites or merges an existing run. Every
run directory and its contents are immutable once created — `history validate` and
`history promote` detect any modified or missing archived byte and name the exact
affected artifact.

Pass `--history-root PATH` on every command to use a location other than the default
`aik-history/` at the repository root.

## CLI

```bash
python aik.py history init CASE_DIR --stage baseline --label "Initial supervisor" --agent-version demo-v1
python aik.py history record CASE_DIR --stage candidate --label "Fixed mixed routing" --agent-version demo-v2 [--role candidate|cohort_anchor] [--metadata key=value]
python aik.py history promote RUN_ID
python aik.py history list
python aik.py history validate
python aik.py dashboard [--candidate RUN_ID] [--output PATH] [--open]
```

`history init` requires a non-empty evaluated stage, requires no existing anchor,
archives an immutable run with role `anchor`, and sets both the anchor and champion
pointers to it.

`history record` archives a candidate (default role) or a cohort anchor. Recording
never changes the anchor or champion pointer. A candidate recorded against a cohort
that differs from the champion's is archived but marked non-comparable until a
matching cohort anchor exists.

## Comparability

```text
cohort_id = SHA-256(dataset_fingerprint + "\n" + evaluator_fingerprint)
```

Two runs are strictly comparable only when both fingerprints are present and match
exactly. AIK never compares semantic versions or filenames as a substitute for
fingerprints, and never fabricates a cross-cohort score.

When the dataset or evaluator suite changes:

1. the new run is not connected to the old quality trend;
2. delta against the old champion shows `not comparable`;
3. rerun the current champion on the new suite and record that rerun with
   `--role cohort_anchor` (the same `agent_version` as the champion);
4. future candidates on that cohort compare against the cohort anchor, not the
   old-cohort champion score;
5. coverage changes are shown separately from quality changes.

The global anchor pointer never moves, but `delta vs anchor` is available only when
the anchor is in the same cohort as the run being displayed.

## Promotion eligibility

`history promote RUN_ID` requires, in order:

1. every archived artifact hash for the run (and, when a cohort anchor is used, for
   that cohort anchor) still matches its manifest — a corrupted run cannot be promoted;
2. the run's role is `candidate` (a `cohort_anchor` cannot be promoted directly);
3. the run's `parent_champion_run_id` equals the current champion (a stale candidate,
   recorded before a different promotion, is refused);
4. the run's cohort matches the current champion's cohort directly, or a valid cohort
   anchor bridges them (same `parent_champion_run_id`, same `agent_version` as the
   champion, same cohort as the candidate, and the same dataset evidence);
5. the run's archived acceptance recommendation is `eligible`;
6. the run's archived human decision is `accepted` or `accepted_with_conditions`.

There is no `--force` bypass. Promotion appends a `champion_promoted` event and then
atomically replaces `state.json`; it never rewrites the candidate's immutable manifest,
so the dashboard derives champion history from `events.jsonl`, not from role rewrites.

## Metrics

- **Level**: `passed / (passed + failed)` from boolean verdicts in the archived stage;
  `manual_review` and missing verdicts are excluded from the denominator but always
  shown.
- **Delta vs champion / anchor**: percentage points, calculated only within a
  comparable cohort; otherwise `null` with an explanatory reason — never a fabricated
  zero.
- **Slope**: ordinary least squares over the pass rates of runs that actually became
  champion (the anchor plus every `champion_promoted` run) within the currently
  displayed cohort, `x = 0, 1, 2, ...` in promotion order. `null` (not zero) with
  fewer than two such points. Never labeled statistically significant.
- **Per-evaluator scores**: pass/fail/error/not-applicable counts and the mean of
  numeric scores from the same evaluator ID, kept separate for deterministic contracts
  and semantic judges — never merged into one opaque average.
- **Regressions**: from the candidate's own archived `comparison.json`, grouped by
  risk and tag, with every high/critical regression listed individually and its
  failing evaluator IDs.
- **Coverage**: computed from accepted, non-retired cases only. Coverage growth is
  reported as `+N cases` or a distribution change, never as a quality improvement.
- **LLM judge variance**: the v0.4 format holds one semantic judgment per
  evaluator/case, so variance is `not available` unless a future artifact records
  multiple numeric observations under an explicit shared evaluator version.

## Dashboard

`python aik.py dashboard` writes `dashboard.json` (the canonical, testable model) and
`dashboard.html` (a standalone, escaped, local render of that model — the renderer
never recomputes a business metric). Both files answer, from the first screen:

1. What is the current champion?
2. Are we better than the comparable anchor/champion?
3. Is the candidate eligible or rejected, and why?
4. Did any behavior regress?
5. Is dataset coverage growing or merely changing?

If no `--candidate` is given, the dashboard selects the latest unpromoted candidate.
If history is corrupted, the dashboard still renders with a visible integrity warning
and the CLI exits non-zero; it never silently repairs a modified archive. The SVG
progress chart draws one disconnected line per cohort, marks the anchor and current
champion, and gives the selected candidate a distinct marker that never appears on
the accepted line before promotion. An accessible data table follows every chart.

## Security notes

- All HTML rendering escapes labels, descriptions, tags, agent versions, and reasons.
  Malicious content in a dataset case or `--metadata` value cannot execute.
- The dashboard embeds no remote script, stylesheet, font, or image; it opens directly
  from disk with no network request.
- `history init`/`history record` refuse to archive a symlink or a source case
  directory that resolves inside the history root, and copy files atomically through
  a temporary directory in the history root.
- History runs may contain the same sensitive evidence as the campaigns they archive
  (agent outputs, tool arguments). Follow [SECURITY.md](../../../../SECURITY.md) before
  committing or sharing a history root.
