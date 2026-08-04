#!/usr/bin/env python3
"""Immutable champion-history model for AIK.

Archives evaluated evidence already produced by Sprint 4 (`aik evaluate`,
`aik review`) as content-hashed, append-only history runs. Never invokes an
agent, evaluator, API, SDK, MCP server, or production system.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DataError, fingerprint, hash_file, read_json, read_jsonl, write_json, write_jsonl
from validate_case import validate as validate_case_artifacts


FORMAT_VERSION = "1.0"
ROLES = {"anchor", "candidate", "champion", "cohort_anchor"}
ARCHIVABLE_ROLES = {"anchor", "candidate", "cohort_anchor"}
ACCEPTED_DECISIONS = {"accepted", "accepted_with_conditions"}
ARCHIVE_OPTIONAL = ("comparison.json", "decision.yaml", "review.html")
_RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


# ---------------------------------------------------------------------------
# Small primitives
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_basic(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _decision_status(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    return None


def _stage_evaluated_path(directory: Path, stage: str) -> Path | None:
    for name in (f"{stage}.evaluated.jsonl", f"{stage}.jsonl"):
        path = directory / name
        if path.exists() and path.stat().st_size:
            return path
    return None


def cohort_id_for(dataset_fingerprint: str, evaluator_fingerprint: str) -> str:
    payload = f"{dataset_fingerprint}\n{evaluator_fingerprint}".encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def parse_metadata(pairs: list[str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise DataError(f"--metadata must be key=value, got: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise DataError(f"--metadata key must be non-empty: {pair}")
        metadata[key] = value
    return metadata


# ---------------------------------------------------------------------------
# History root and low-level storage
# ---------------------------------------------------------------------------

def _guard_root_not_inside_snapshot(root: Path) -> None:
    if _RUN_ID_PATTERN.match(root.name) or root.parent.name == "runs":
        raise DataError(f"Refusing to use a history root that resolves inside a run snapshot: {root}")


def _history_paths(history_root: str | Path) -> dict[str, Path]:
    root = Path(history_root).resolve()
    _guard_root_not_inside_snapshot(root)
    return {
        "root": root,
        "state": root / "state.json",
        "events": root / "events.jsonl",
        "runs": root / "runs",
        "dashboard_json": root / "dashboard.json",
        "dashboard_html": root / "dashboard.html",
    }


def load_state(history_root: str | Path) -> dict[str, Any] | None:
    paths = _history_paths(history_root)
    if not paths["state"].exists():
        return None
    return read_json(paths["state"])


def load_events(history_root: str | Path) -> list[dict[str, Any]]:
    paths = _history_paths(history_root)
    if not paths["events"].exists():
        return []
    return read_jsonl(paths["events"], "event_id")


def load_manifest(history_root: str | Path, run_id: str) -> dict[str, Any]:
    paths = _history_paths(history_root)
    manifest_path = paths["runs"] / run_id / "manifest.json"
    if not manifest_path.exists():
        raise DataError(f"Unknown history run: {run_id}")
    return read_json(manifest_path)


def list_run_ids(history_root: str | Path) -> list[str]:
    paths = _history_paths(history_root)
    if not paths["runs"].exists():
        return []
    return sorted(entry.name for entry in paths["runs"].iterdir() if entry.is_dir() and not entry.name.startswith(".tmp-"))


def _event_content(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "event_id"}


def _append_event(history_root: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    paths = _history_paths(history_root)
    complete = {**event, "event_id": fingerprint(event)}
    existing = load_events(history_root)
    write_jsonl(paths["events"], [*existing, complete])
    return complete


def _write_state(history_root: str | Path, *, anchor_run_id: str | None, champion_run_id: str | None, when: datetime) -> Path:
    paths = _history_paths(history_root)
    return write_json(
        paths["state"],
        {
            "format_version": FORMAT_VERSION,
            "anchor_run_id": anchor_run_id,
            "champion_run_id": champion_run_id,
            "updated_at": when.isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Archiving (immutable snapshot creation)
# ---------------------------------------------------------------------------

def _resolve_case_dir(case_dir: str | Path) -> Path:
    directory = Path(case_dir).resolve()
    if not directory.is_dir():
        raise DataError(f"Case directory not found: {directory}")
    return directory


def _guard_case_outside_history_root(history_root_resolved: Path, case_dir_resolved: Path) -> None:
    try:
        case_dir_resolved.relative_to(history_root_resolved)
    except ValueError:
        return
    raise DataError("Refusing to record a case directory that resolves inside the history root")


def _collect_source_artifacts(case_dir: Path, stage: str) -> dict[str, Path]:
    errors, _warnings = validate_case_artifacts(case_dir)
    if errors:
        raise DataError("Source case failed Sprint 4 validation: " + "; ".join(errors))
    if not (case_dir / "dataset.json").exists() or not (case_dir / "evaluation.json").exists():
        raise DataError("History requires a Dataset v2 case with dataset.json and evaluation.json")
    evaluated = _stage_evaluated_path(case_dir, stage)
    if evaluated is None:
        raise DataError(f"Case has no non-empty evaluated '{stage}' stage; run 'aik evaluate' first")

    files: dict[str, Path] = {
        "test_cases.jsonl": case_dir / "test_cases.jsonl",
        "dataset.json": case_dir / "dataset.json",
        "evaluation.json": case_dir / "evaluation.json",
        "evaluated.jsonl": evaluated,
    }
    for name in ARCHIVE_OPTIONAL:
        source = case_dir / name
        if source.exists() and source.stat().st_size:
            files[name] = source
    for name, source in files.items():
        if source.is_symlink():
            raise DataError(f"Refusing to archive symlink: {name}")
        if not source.is_file():
            raise DataError(f"Archived source is not a regular file: {name}")
    return files


def _evaluated_fingerprints(evaluated_records: list[dict[str, Any]]) -> tuple[str, str]:
    dataset_fps: set[str] = set()
    evaluator_fps: set[str] = set()
    for record in evaluated_records:
        metadata = record.get("evaluation_metadata")
        if not isinstance(metadata, dict) or not metadata.get("dataset_fingerprint") or not metadata.get("evaluator_fingerprint"):
            raise DataError(
                "Evaluated stage is missing evaluation_metadata fingerprints; "
                "run 'aik evaluate' on a Dataset v2 case before recording history"
            )
        dataset_fps.add(metadata["dataset_fingerprint"])
        evaluator_fps.add(metadata["evaluator_fingerprint"])
    if len(dataset_fps) != 1 or len(evaluator_fps) != 1:
        raise DataError("Evaluated stage contains inconsistent dataset/evaluator fingerprints")
    return next(iter(dataset_fps)), next(iter(evaluator_fps))


def _stage_metrics(evaluated_records: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for record in evaluated_records if record.get("passed") is True)
    failed = sum(1 for record in evaluated_records if record.get("passed") is False)
    manual = sum(1 for record in evaluated_records if record.get("passed") is None)
    denominator = passed + failed
    pass_rate = round(passed / denominator, 6) if denominator else None
    return {"case_count": len(evaluated_records), "passed": passed, "failed": failed, "manual_review": manual, "pass_rate": pass_rate}


def _archive_run(
    *,
    history_root: str | Path,
    case_dir: str | Path,
    stage: str,
    role: str,
    label: str,
    agent_version: str,
    parent_champion_run_id: str | None,
    metadata: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    if role not in ARCHIVABLE_ROLES:
        raise DataError(f"Invalid history role: {role}")
    if not label or not label.strip():
        raise DataError("A non-empty --label is required")
    if not agent_version or not agent_version.strip():
        raise DataError("A non-empty --agent-version is required")

    paths = _history_paths(history_root)
    source_dir = _resolve_case_dir(case_dir)
    _guard_case_outside_history_root(paths["root"], source_dir)

    files = _collect_source_artifacts(source_dir, stage)
    evaluated_records = read_jsonl(files["evaluated.jsonl"], "case_id")
    dataset_fp, evaluator_fp = _evaluated_fingerprints(evaluated_records)
    cohort = cohort_id_for(dataset_fp, evaluator_fp)
    stage_metrics = _stage_metrics(evaluated_records)

    artifact_hashes = {name: hash_file(source) for name, source in files.items()}
    snapshot_fingerprint = fingerprint({"artifacts": artifact_hashes})

    run_id = f"{_timestamp_basic(now)}-{snapshot_fingerprint.split(':', 1)[1][:8]}"
    target_dir = paths["runs"] / run_id

    acceptance_recommendation = None
    human_decision = None
    if role != "anchor":
        comparison = read_json(files["comparison.json"]) if "comparison.json" in files else None
        if isinstance(comparison, dict) and isinstance(comparison.get("acceptance"), dict):
            acceptance_recommendation = comparison["acceptance"].get("recommendation")
        human_decision = _decision_status(files["decision.yaml"]) if "decision.yaml" in files else None

    try:
        source_relative = str(source_dir.relative_to(Path.cwd()))
    except ValueError:
        source_relative = str(source_dir)

    manifest = {
        "format_version": FORMAT_VERSION,
        "run_id": run_id,
        "recorded_at": now.isoformat(),
        "label": label,
        "agent_version": agent_version,
        "stage": stage,
        "role": role,
        "source_case": source_relative,
        "parent_champion_run_id": parent_champion_run_id,
        "dataset_fingerprint": dataset_fp,
        "evaluator_fingerprint": evaluator_fp,
        "cohort_id": cohort,
        "snapshot_fingerprint": snapshot_fingerprint,
        "case_count": stage_metrics["case_count"],
        "passed": stage_metrics["passed"],
        "failed": stage_metrics["failed"],
        "manual_review": stage_metrics["manual_review"],
        "pass_rate": stage_metrics["pass_rate"],
        "acceptance_recommendation": acceptance_recommendation,
        "human_decision": human_decision,
        "artifacts": artifact_hashes,
        "metadata": metadata or {},
    }

    if target_dir.exists():
        existing = read_json(target_dir / "manifest.json")
        if existing.get("snapshot_fingerprint") == snapshot_fingerprint:
            return {"manifest": existing, "duplicate": True, "run_dir": target_dir}
        raise DataError(f"History run id collision for {run_id}: archived content differs from the existing run")

    paths["runs"].mkdir(parents=True, exist_ok=True)
    temp_dir = paths["runs"] / f".tmp-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True)
    try:
        for name, source in files.items():
            shutil.copy2(source, temp_dir / name)
        write_json(temp_dir / "manifest.json", manifest)
        temp_dir.rename(target_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {"manifest": manifest, "duplicate": False, "run_dir": target_dir}


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------

def verify_run_integrity(history_root: str | Path, run_id: str) -> list[str]:
    paths = _history_paths(history_root)
    run_dir = paths["runs"] / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"Missing manifest for run {run_id}"]
    try:
        manifest = read_json(manifest_path)
    except DataError as exc:
        return [str(exc)]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return [f"{run_id}: manifest has no recorded artifacts"]

    problems: list[str] = []
    actual_hashes: dict[str, str] = {}
    for name, recorded_hash in artifacts.items():
        artifact_path = run_dir / name
        if not artifact_path.exists() or artifact_path.is_symlink() or not artifact_path.is_file():
            problems.append(f"{run_id}/{name}: archived artifact is missing")
            continue
        actual_hash = hash_file(artifact_path)
        actual_hashes[name] = actual_hash
        if actual_hash != recorded_hash:
            problems.append(f"{run_id}/{name}: archived artifact hash does not match manifest (modified)")

    if not problems:
        recomputed_snapshot = fingerprint({"artifacts": actual_hashes})
        if manifest.get("snapshot_fingerprint") != recomputed_snapshot:
            problems.append(f"{run_id}: snapshot_fingerprint does not match archived artifacts")

    for extra in run_dir.iterdir():
        if extra.name != "manifest.json" and extra.name not in artifacts:
            problems.append(f"{run_id}/{extra.name}: unexpected file not recorded in manifest")
    return problems


# ---------------------------------------------------------------------------
# Lifecycle: init / record / promote / list / validate
# ---------------------------------------------------------------------------

def history_init(
    case_dir: str | Path,
    *,
    history_root: str | Path = "aik-history",
    stage: str = "baseline",
    label: str,
    agent_version: str,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    when = now or _utc_now()
    state = load_state(history_root)
    if state and state.get("anchor_run_id"):
        raise DataError("An anchor already exists; 'history init' can run only once per history root")

    archived = _archive_run(
        history_root=history_root,
        case_dir=case_dir,
        stage=stage,
        role="anchor",
        label=label,
        agent_version=agent_version,
        parent_champion_run_id=None,
        metadata=metadata,
        now=when,
    )
    manifest = archived["manifest"]
    run_id = manifest["run_id"]
    if not archived["duplicate"]:
        _append_event(
            history_root,
            {
                "type": "anchor_initialized",
                "recorded_at": when.isoformat(),
                "run_id": run_id,
                "previous_champion_run_id": None,
                "human_decision": manifest.get("human_decision"),
            },
        )
        _write_state(history_root, anchor_run_id=run_id, champion_run_id=run_id, when=when)
    return manifest


def history_record(
    case_dir: str | Path,
    *,
    history_root: str | Path = "aik-history",
    stage: str,
    label: str,
    agent_version: str,
    role: str = "candidate",
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if role not in {"candidate", "cohort_anchor"}:
        raise DataError("Record role must be 'candidate' or 'cohort_anchor'")
    when = now or _utc_now()
    state = load_state(history_root)
    if not state or not state.get("champion_run_id"):
        raise DataError("Initialize an anchor with 'history init' before recording a candidate")
    champion_run_id = state["champion_run_id"]
    champion_manifest = load_manifest(history_root, champion_run_id)

    if role == "cohort_anchor" and agent_version != champion_manifest.get("agent_version"):
        raise DataError("A cohort anchor must use the same agent_version as the current champion")

    archived = _archive_run(
        history_root=history_root,
        case_dir=case_dir,
        stage=stage,
        role=role,
        label=label,
        agent_version=agent_version,
        parent_champion_run_id=champion_run_id,
        metadata=metadata,
        now=when,
    )
    manifest = archived["manifest"]
    if role == "cohort_anchor" and not archived["duplicate"]:
        _append_event(
            history_root,
            {
                "type": "cohort_anchor_recorded",
                "recorded_at": when.isoformat(),
                "run_id": manifest["run_id"],
                "previous_champion_run_id": champion_run_id,
                "human_decision": manifest.get("human_decision"),
            },
        )
    return manifest


def _find_valid_cohort_anchor(
    history_root: str | Path, champion_run_id: str, champion_manifest: dict[str, Any], target_cohort_id: str
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for run_id in list_run_ids(history_root):
        manifest = load_manifest(history_root, run_id)
        if manifest.get("role") != "cohort_anchor":
            continue
        if manifest.get("parent_champion_run_id") != champion_run_id:
            continue
        if manifest.get("agent_version") != champion_manifest.get("agent_version"):
            continue
        if manifest.get("cohort_id") != target_cohort_id:
            continue
        if verify_run_integrity(history_root, run_id):
            continue
        if best is None or manifest["run_id"] > best["run_id"]:
            best = manifest
    return best


def history_promote(
    run_id: str,
    *,
    history_root: str | Path = "aik-history",
    now: datetime | None = None,
) -> dict[str, Any]:
    when = now or _utc_now()
    state = load_state(history_root)
    if not state or not state.get("champion_run_id"):
        raise DataError("No champion exists yet; run 'history init' first")
    champion_run_id = state["champion_run_id"]

    problems = verify_run_integrity(history_root, run_id)
    if problems:
        raise DataError("Cannot promote a corrupted run: " + "; ".join(problems))
    manifest = load_manifest(history_root, run_id)

    if manifest.get("role") != "candidate":
        raise DataError(f"Only a candidate run can be promoted; {run_id} has role '{manifest.get('role')}'")
    if manifest.get("parent_champion_run_id") != champion_run_id:
        raise DataError(
            f"Stale candidate: {run_id} was recorded against champion "
            f"{manifest.get('parent_champion_run_id')}, but the current champion is {champion_run_id}"
        )

    champion_manifest = load_manifest(history_root, champion_run_id)
    cohort_anchor: dict[str, Any] | None = None
    if manifest.get("cohort_id") != champion_manifest.get("cohort_id"):
        cohort_anchor = _find_valid_cohort_anchor(history_root, champion_run_id, champion_manifest, manifest.get("cohort_id"))
        if cohort_anchor is None:
            raise DataError(
                f"{run_id} is not comparable to the current champion cohort; record a 'cohort_anchor' rerun "
                "of the champion on this dataset/evaluator suite before promoting"
            )
        if cohort_anchor.get("dataset_fingerprint") != manifest.get("dataset_fingerprint"):
            raise DataError("The cohort anchor does not represent the same dataset evidence as the candidate")

    if manifest.get("acceptance_recommendation") != "eligible":
        raise DataError(
            f"{run_id} recommendation must be 'eligible'; archived recommendation is "
            f"'{manifest.get('acceptance_recommendation')}'"
        )
    if manifest.get("human_decision") not in ACCEPTED_DECISIONS:
        raise DataError(
            f"{run_id} human decision must be accepted before promotion; archived decision is "
            f"'{manifest.get('human_decision')}'"
        )

    event = _append_event(
        history_root,
        {
            "type": "champion_promoted",
            "recorded_at": when.isoformat(),
            "run_id": run_id,
            "previous_champion_run_id": champion_run_id,
            "human_decision": manifest.get("human_decision"),
        },
    )
    _write_state(history_root, anchor_run_id=state.get("anchor_run_id"), champion_run_id=run_id, when=when)
    return {"event": event, "champion_run_id": run_id, "cohort_anchor_run_id": (cohort_anchor or {}).get("run_id")}


def history_list(history_root: str | Path = "aik-history") -> list[dict[str, Any]]:
    state = load_state(history_root) or {}
    anchor_run_id = state.get("anchor_run_id")
    champion_run_id = state.get("champion_run_id")
    rows: list[dict[str, Any]] = []
    for run_id in list_run_ids(history_root):
        manifest = load_manifest(history_root, run_id)
        cohort_id = manifest.get("cohort_id") or ""
        rows.append(
            {
                "run_id": run_id,
                "recorded_at": manifest.get("recorded_at"),
                "label": manifest.get("label"),
                "agent_version": manifest.get("agent_version"),
                "cohort_prefix": cohort_id.split(":", 1)[1][:8] if ":" in cohort_id else cohort_id[:8],
                "pass_rate": manifest.get("pass_rate"),
                "role": manifest.get("role"),
                "is_anchor": run_id == anchor_run_id,
                "is_champion": run_id == champion_run_id,
            }
        )
    return rows


def history_validate(history_root: str | Path = "aik-history") -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    paths = _history_paths(history_root)
    if not paths["root"].exists():
        warnings.append("No history found at the given history root")
        return errors, warnings

    state = load_state(history_root)
    if state is None:
        warnings.append("History root exists but has no state.json")
        state = {}
    else:
        for field in ("format_version", "anchor_run_id", "champion_run_id", "updated_at"):
            if field not in state:
                errors.append(f"state.json is missing '{field}'")

    run_ids = list_run_ids(history_root)
    manifests: dict[str, dict[str, Any]] = {}
    for run_id in run_ids:
        errors.extend(verify_run_integrity(history_root, run_id))
        try:
            manifest = load_manifest(history_root, run_id)
        except DataError as exc:
            errors.append(str(exc))
            continue
        manifests[run_id] = manifest
        if manifest.get("run_id") != run_id:
            errors.append(f"{run_id}: manifest run_id does not match its directory name")
        if manifest.get("role") not in ROLES:
            errors.append(f"{run_id}: invalid role '{manifest.get('role')}'")
        for field in ("dataset_fingerprint", "evaluator_fingerprint", "cohort_id", "snapshot_fingerprint"):
            if not manifest.get(field):
                errors.append(f"{run_id}: manifest is missing '{field}'")
        expected_cohort = cohort_id_for(manifest.get("dataset_fingerprint", ""), manifest.get("evaluator_fingerprint", ""))
        if manifest.get("cohort_id") != expected_cohort:
            errors.append(f"{run_id}: cohort_id does not match its dataset/evaluator fingerprints")
        parent = manifest.get("parent_champion_run_id")
        if parent is not None and parent not in run_ids:
            errors.append(f"{run_id}: parent_champion_run_id references unknown run {parent}")

    anchor_run_id = state.get("anchor_run_id")
    champion_run_id = state.get("champion_run_id")
    if anchor_run_id and anchor_run_id not in manifests:
        errors.append(f"state.json anchor_run_id references unknown run: {anchor_run_id}")
    elif anchor_run_id and manifests[anchor_run_id].get("role") != "anchor":
        errors.append(f"state.json anchor_run_id {anchor_run_id} does not point to an anchor run")
    if champion_run_id and champion_run_id not in manifests:
        errors.append(f"state.json champion_run_id references unknown run: {champion_run_id}")

    try:
        events = load_events(history_root)
    except DataError as exc:
        events = []
        errors.append(str(exc))

    replayed_anchor: str | None = None
    replayed_champion: str | None = None
    seen_anchor_event = False
    for event in events:
        expected_id = fingerprint(_event_content(event))
        if event.get("event_id") != expected_id:
            errors.append(f"events.jsonl: event {event.get('event_id')} has an invalid content hash")
        run_ref = event.get("run_id")
        if run_ref and run_ref not in manifests:
            errors.append(f"events.jsonl: event references unknown run {run_ref}")
        event_type = event.get("type")
        if event_type == "anchor_initialized":
            if seen_anchor_event:
                errors.append("events.jsonl: more than one anchor_initialized event")
            seen_anchor_event = True
            replayed_anchor = run_ref
            replayed_champion = run_ref
        elif event_type == "champion_promoted":
            if event.get("previous_champion_run_id") != replayed_champion:
                errors.append(
                    f"events.jsonl: champion_promoted event for {run_ref} has a dangling "
                    f"previous_champion_run_id {event.get('previous_champion_run_id')}"
                )
            replayed_champion = run_ref
        elif event_type == "cohort_anchor_recorded":
            pass
        else:
            errors.append(f"events.jsonl: unknown event type '{event_type}'")

    if anchor_run_id and replayed_anchor != anchor_run_id:
        errors.append("state.json anchor_run_id does not match the recorded anchor_initialized event")
    if champion_run_id and replayed_champion != champion_run_id:
        errors.append("state.json champion_run_id does not match the replayed promotion event chain")

    if champion_run_id and champion_run_id != anchor_run_id and champion_run_id in manifests:
        promoted_manifest = manifests[champion_run_id]
        if promoted_manifest.get("acceptance_recommendation") != "eligible":
            errors.append(f"{champion_run_id}: promoted champion no longer has archived recommendation 'eligible'")
        if promoted_manifest.get("human_decision") not in ACCEPTED_DECISIONS:
            errors.append(f"{champion_run_id}: promoted champion no longer has an archived accepted decision")

    if not run_ids:
        warnings.append("History root has no recorded runs yet")
    return errors, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Manage AIK champion history")
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="Archive the first anchor/champion run")
    init_parser.add_argument("case_dir")
    init_parser.add_argument("--stage", default="baseline")
    init_parser.add_argument("--label", required=True)
    init_parser.add_argument("--agent-version", required=True)
    init_parser.add_argument("--history-root", default="aik-history")
    init_parser.add_argument("--metadata", action="append", default=[])

    record_parser = commands.add_parser("record", help="Archive a candidate or cohort anchor run")
    record_parser.add_argument("case_dir")
    record_parser.add_argument("--stage", required=True, choices=("baseline", "candidate"))
    record_parser.add_argument("--label", required=True)
    record_parser.add_argument("--agent-version", required=True)
    record_parser.add_argument("--role", default="candidate", choices=("candidate", "cohort_anchor"))
    record_parser.add_argument("--history-root", default="aik-history")
    record_parser.add_argument("--metadata", action="append", default=[])

    promote_parser = commands.add_parser("promote", help="Promote an archived candidate to champion")
    promote_parser.add_argument("run_id")
    promote_parser.add_argument("--history-root", default="aik-history")

    list_parser = commands.add_parser("list", help="List archived history runs")
    list_parser.add_argument("--history-root", default="aik-history")

    validate_parser = commands.add_parser("validate", help="Validate history integrity")
    validate_parser.add_argument("--history-root", default="aik-history")

    args = parser.parse_args()
    try:
        if args.command == "init":
            manifest = history_init(
                args.case_dir,
                history_root=args.history_root,
                stage=args.stage,
                label=args.label,
                agent_version=args.agent_version,
                metadata=parse_metadata(args.metadata),
            )
            print(f"run_id={manifest['run_id']}")
            print(f"pass_rate={manifest['pass_rate']}")
            print(f"cohort_id={manifest['cohort_id']}")
            print(Path(args.history_root).resolve() / "runs" / manifest["run_id"])
            return 0
        if args.command == "record":
            manifest = history_record(
                args.case_dir,
                history_root=args.history_root,
                stage=args.stage,
                label=args.label,
                agent_version=args.agent_version,
                role=args.role,
                metadata=parse_metadata(args.metadata),
            )
            print(f"run_id={manifest['run_id']}")
            print(f"role={manifest['role']}")
            print(f"pass_rate={manifest['pass_rate']}")
            return 0
        if args.command == "promote":
            result = history_promote(args.run_id, history_root=args.history_root)
            print(f"champion_run_id={result['champion_run_id']}")
            return 0
        if args.command == "list":
            rows = history_list(args.history_root)
            for row in rows:
                markers = " ".join(filter(None, ["ANCHOR" if row["is_anchor"] else "", "CHAMPION" if row["is_champion"] else ""]))
                pass_rate = "N/A" if row["pass_rate"] is None else f"{row['pass_rate']:.3f}"
                print(
                    f"{row['run_id']}  {row['role']:<13} {row['agent_version']:<16} "
                    f"{row['cohort_prefix']:<10} pass_rate={pass_rate}  {markers}"
                )
            return 0
        if args.command == "validate":
            errors, warnings = history_validate(args.history_root)
            for warning in warnings:
                print(f"WARNING: {warning}")
            for error in errors:
                print(f"ERROR: {error}")
            if errors:
                return 1
            print("VALID")
            return 0
    except DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
