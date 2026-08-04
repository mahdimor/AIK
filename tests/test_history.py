from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import DataError, hash_file, read_json, read_jsonl, write_json, write_jsonl  # noqa: E402
from evaluate_results import evaluate  # noqa: E402
from init_case import initialize  # noqa: E402
from run_review import run as build_review  # noqa: E402
from history import (  # noqa: E402
    history_init,
    history_promote,
    history_record,
    history_validate,
    load_events,
    load_manifest,
    load_state,
    verify_run_integrity,
)


CASES = [
    {"id": "c1", "description": "Case one", "input": {"i": 1}, "expected": {"required_agents": ["a"]}, "tags": ["t1"], "risk": "medium", "source": "test", "split": "development", "status": "accepted"},
    {"id": "c2", "description": "Case two", "input": {"i": 2}, "expected": {"required_agents": ["a"]}, "tags": ["t1"], "risk": "medium", "source": "test", "split": "regression", "status": "accepted"},
    {"id": "c3", "description": "Case three", "input": {"i": 3}, "expected": {"required_agents": ["a"]}, "tags": ["t2"], "risk": "low", "source": "test", "split": "challenge", "status": "accepted"},
    {"id": "c4", "description": "Case four", "input": {"i": 4}, "expected": {"required_agents": ["a"]}, "tags": ["t2", "critical-path"], "risk": "high", "source": "test", "split": "development", "status": "accepted"},
]

DEFAULT_ACCEPTANCE = {
    "reject_on_evaluator_error": True,
    "no_regression_at_or_above_risk": "high",
    "critical_cases_must_pass": True,
    "maximum_regressions": 0,
    "minimum_candidate_pass_rate": 0.0,
    "minimum_delta": 0.0,
}


def _raw(agents_by_case: dict[str, list[str]]) -> list[dict]:
    return [{"case_id": case_id, "output": "ok", "agents": agents, "tool_calls": [], "error": None} for case_id, agents in agents_by_case.items()]


class HistoryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare_case(
        self,
        name: str,
        *,
        baseline_agents: dict[str, list[str]] | None = None,
        candidate_agents: dict[str, list[str]] | None = None,
        decision: str = "pending",
        cases: list[dict] | None = None,
        evaluators: list[dict] | None = None,
        acceptance: dict | None = None,
    ) -> Path:
        case_dir = initialize(name, self.root / "improvements")
        write_jsonl(case_dir / "test_cases.jsonl", cases if cases is not None else CASES)
        write_json(
            case_dir / "evaluation.json",
            {
                "format_version": "1.0",
                "suite_id": f"{name}-suite",
                "version": "1.0.0",
                "evaluators": evaluators
                or [
                    {"id": "execution-ok", "type": "no_execution_error", "required": True},
                    {"id": "required-agents", "type": "required_agents", "required": True},
                ],
                "acceptance": acceptance or DEFAULT_ACCEPTANCE,
            },
        )
        default_baseline = {"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": []}
        default_candidate = {"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"]}
        write_jsonl(case_dir / "baseline.raw.jsonl", _raw(baseline_agents or default_baseline))
        write_jsonl(case_dir / "candidate.raw.jsonl", _raw(candidate_agents or default_candidate))
        evaluate(case_dir, "baseline", overwrite=True)
        evaluate(case_dir, "candidate", overwrite=True)
        build_review(case_dir)
        (case_dir / "decision.yaml").write_text(f"status: {decision}\n", encoding="utf-8")
        return case_dir


class HistoryLifecycleTests(HistoryTestBase):
    def test_initialize_anchor_and_champion_together(self) -> None:
        case_dir = self._prepare_case("case-a")
        history_root = self.root / "aik-history"
        manifest = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        self.assertEqual(manifest["role"], "anchor")
        self.assertEqual(manifest["pass_rate"], 0.75)
        self.assertIsNone(manifest["acceptance_recommendation"])
        self.assertIsNone(manifest["human_decision"])
        state = load_state(history_root)
        self.assertEqual(state["anchor_run_id"], manifest["run_id"])
        self.assertEqual(state["champion_run_id"], manifest["run_id"])
        errors, _warnings = history_validate(history_root)
        self.assertEqual(errors, [])

    def test_refuse_second_anchor_initialization(self) -> None:
        case_dir = self._prepare_case("case-b")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        with self.assertRaises(DataError):
            history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor again", agent_version="v1")

    def test_record_candidate_without_changing_champion(self) -> None:
        case_dir = self._prepare_case("case-c")
        history_root = self.root / "aik-history"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertEqual(candidate["role"], "candidate")
        self.assertEqual(candidate["pass_rate"], 1.0)
        self.assertEqual(candidate["parent_champion_run_id"], anchor["run_id"])
        state = load_state(history_root)
        self.assertEqual(state["champion_run_id"], anchor["run_id"])

    def test_recording_identical_source_twice_is_an_idempotent_duplicate(self) -> None:
        case_dir = self._prepare_case("case-d")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2", now=when)
        second = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2", now=when)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["snapshot_fingerprint"], second["snapshot_fingerprint"])
        run_dirs = list((history_root / "runs").iterdir())
        self.assertEqual(len(run_dirs), 2)

    def test_exact_artifact_hashes_and_snapshot_fingerprint(self) -> None:
        case_dir = self._prepare_case("case-e")
        history_root = self.root / "aik-history"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        manifest = load_manifest(history_root, anchor["run_id"])
        run_dir = history_root / "runs" / anchor["run_id"]
        for name, recorded_hash in manifest["artifacts"].items():
            self.assertEqual(hash_file(run_dir / name), recorded_hash)
        self.assertEqual(verify_run_integrity(history_root, anchor["run_id"]), [])

    def test_missing_archived_artifact_is_detected(self) -> None:
        case_dir = self._prepare_case("case-f")
        history_root = self.root / "aik-history"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        run_dir = history_root / "runs" / anchor["run_id"]
        (run_dir / "test_cases.jsonl").unlink()
        problems = verify_run_integrity(history_root, anchor["run_id"])
        self.assertTrue(any("test_cases.jsonl" in problem and "missing" in problem for problem in problems))
        errors, _warnings = history_validate(history_root)
        self.assertTrue(errors)

    def test_modified_archived_artifact_is_detected_with_exact_name(self) -> None:
        case_dir = self._prepare_case("case-g")
        history_root = self.root / "aik-history"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        run_dir = history_root / "runs" / anchor["run_id"]
        with (run_dir / "evaluated.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        problems = verify_run_integrity(history_root, anchor["run_id"])
        self.assertEqual(len(problems), 1)
        self.assertIn("evaluated.jsonl", problems[0])
        self.assertIn("modified", problems[0])

    def test_dangling_state_pointer_is_detected(self) -> None:
        case_dir = self._prepare_case("case-h")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        state_path = history_root / "state.json"
        state = read_json(state_path)
        state["champion_run_id"] = "20200101T000000Z-deadbeef"
        write_json(state_path, state)
        errors, _warnings = history_validate(history_root)
        self.assertTrue(any("champion_run_id" in error for error in errors))

    def test_dangling_promotion_event_is_detected(self) -> None:
        case_dir = self._prepare_case("case-i", decision="accepted")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        history_promote(candidate["run_id"], history_root=history_root)
        events_path = history_root / "events.jsonl"
        events = read_jsonl(events_path, "event_id")
        events[-1]["previous_champion_run_id"] = "bogus-run-id"
        write_jsonl(events_path, events)
        errors, _warnings = history_validate(history_root)
        self.assertTrue(any("dangling" in error for error in errors))

    def test_source_case_recorded_as_relative_path_when_possible(self) -> None:
        case_dir = self._prepare_case("case-j")
        history_root = self.root / "aik-history"
        old_cwd = Path.cwd()
        os.chdir(case_dir.parent)
        try:
            anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        finally:
            os.chdir(old_cwd)
        self.assertFalse(Path(anchor["source_case"]).is_absolute())

    def test_history_root_override_is_respected(self) -> None:
        case_dir = self._prepare_case("case-k")
        custom_root = self.root / "custom-history-location"
        anchor = history_init(case_dir, history_root=custom_root, stage="baseline", label="Anchor", agent_version="v1")
        self.assertTrue((custom_root / "runs" / anchor["run_id"] / "manifest.json").exists())

    def test_level_excludes_manual_review_and_reports_count(self) -> None:
        evaluators = [
            {"id": "execution-ok", "type": "no_execution_error", "required": True},
            {"id": "required-agents", "type": "required_agents", "required": True},
            {"id": "quality", "type": "llm_rubric", "required": True, "threshold": 0.7},
        ]
        cases = [dict(case, expected={**case["expected"], "rubric": ["Looks good"]}) for case in CASES]
        case_dir = self._prepare_case("metrics-a", cases=cases, evaluators=evaluators)
        history_root = self.root / "aik-history-metrics-a"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        self.assertIsNone(anchor["pass_rate"])
        self.assertEqual(anchor["manual_review"], 4)
        self.assertEqual(anchor["passed"] + anchor["failed"], 0)


class HistoryPromotionTests(HistoryTestBase):
    def _init_and_candidate(self, name: str, decision: str = "pending") -> tuple[Path, dict, dict]:
        case_dir = self._prepare_case(name, decision=decision)
        history_root = self.root / f"aik-history-{name}"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        return history_root, anchor, candidate

    def test_accepted_eligible_candidate_succeeds(self) -> None:
        history_root, anchor, candidate = self._init_and_candidate("promo-a", decision="accepted")
        result = history_promote(candidate["run_id"], history_root=history_root)
        self.assertEqual(result["champion_run_id"], candidate["run_id"])
        state = load_state(history_root)
        self.assertEqual(state["champion_run_id"], candidate["run_id"])
        self.assertEqual(state["anchor_run_id"], anchor["run_id"])

    def test_pending_human_decision_fails(self) -> None:
        history_root, _anchor, candidate = self._init_and_candidate("promo-b", decision="pending")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_manifest_cannot_override_archived_human_decision(self) -> None:
        history_root, _anchor, candidate = self._init_and_candidate("promo-manifest", decision="pending")
        manifest_path = history_root / "runs" / candidate["run_id"] / "manifest.json"
        manifest = read_json(manifest_path)
        manifest["human_decision"] = "accepted"
        write_json(manifest_path, manifest)

        problems = verify_run_integrity(history_root, candidate["run_id"])
        self.assertTrue(any("human_decision" in problem for problem in problems))
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_rejected_human_decision_fails(self) -> None:
        history_root, _anchor, candidate = self._init_and_candidate("promo-c", decision="rejected")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_machine_rejected_candidate_fails(self) -> None:
        acceptance = {**DEFAULT_ACCEPTANCE, "minimum_candidate_pass_rate": 2.0}
        case_dir = self._prepare_case("promo-d", decision="accepted", acceptance=acceptance)
        history_root = self.root / "aik-history-promo-d"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertEqual(candidate["acceptance_recommendation"], "rejected")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_manual_review_candidate_fails(self) -> None:
        evaluators = [
            {"id": "execution-ok", "type": "no_execution_error", "required": True},
            {"id": "required-agents", "type": "required_agents", "required": True},
            {"id": "quality", "type": "llm_rubric", "required": True, "threshold": 0.7},
        ]
        cases = [dict(case, expected={**case["expected"], "rubric": ["Looks good"]}) for case in CASES]
        case_dir = self._prepare_case("promo-e", decision="accepted", cases=cases, evaluators=evaluators)
        history_root = self.root / "aik-history-promo-e"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertEqual(candidate["acceptance_recommendation"], "manual_review")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_stale_parent_champion_fails(self) -> None:
        history_root = self.root / "aik-history-promo-f"
        case_a = self._prepare_case("promo-f-a", decision="accepted")
        case_b = self._prepare_case("promo-f-b", decision="accepted")
        history_init(case_a, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate1 = history_record(case_a, history_root=history_root, stage="candidate", label="First", agent_version="v2")
        candidate2 = history_record(case_b, history_root=history_root, stage="candidate", label="Second", agent_version="v3")
        history_promote(candidate1["run_id"], history_root=history_root)
        with self.assertRaises(DataError):
            history_promote(candidate2["run_id"], history_root=history_root)

    def test_incompatible_cohort_fails_without_a_cohort_anchor(self) -> None:
        history_root = self.root / "aik-history-promo-g"
        case_a = self._prepare_case("promo-g-a", decision="accepted")
        different_cases = [dict(case, id=case["id"] + "-x") for case in CASES]
        renamed_baseline = {"c1-x": ["a"], "c2-x": ["a"], "c3-x": ["a"], "c4-x": []}
        renamed_candidate = {"c1-x": ["a"], "c2-x": ["a"], "c3-x": ["a"], "c4-x": ["a"]}
        case_b = self._prepare_case(
            "promo-g-b", decision="accepted", cases=different_cases, baseline_agents=renamed_baseline, candidate_agents=renamed_candidate
        )
        history_init(case_a, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_b, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_cohort_anchor_enables_promotion_after_dataset_change(self) -> None:
        history_root = self.root / "aik-history-promo-h"
        case_a = self._prepare_case("promo-h-a", decision="accepted")
        different_cases = [dict(case, id=case["id"] + "-y") for case in CASES]
        renamed_baseline = {"c1-y": ["a"], "c2-y": ["a"], "c3-y": ["a"], "c4-y": []}
        renamed_candidate = {"c1-y": ["a"], "c2-y": ["a"], "c3-y": ["a"], "c4-y": ["a"]}
        case_b = self._prepare_case(
            "promo-h-b", decision="accepted", cases=different_cases, baseline_agents=renamed_baseline, candidate_agents=renamed_candidate
        )
        anchor = history_init(case_a, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        cohort_anchor = history_record(
            case_b, history_root=history_root, stage="baseline", label="Champion rerun", agent_version="v1", role="cohort_anchor"
        )
        candidate = history_record(case_b, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        result = history_promote(candidate["run_id"], history_root=history_root)
        self.assertEqual(result["champion_run_id"], candidate["run_id"])
        self.assertEqual(result["cohort_anchor_run_id"], cohort_anchor["run_id"])
        self.assertEqual(load_state(history_root)["anchor_run_id"], anchor["run_id"])

    def test_candidate_cannot_use_weaker_baseline_than_current_champion(self) -> None:
        history_root = self.root / "aik-history-promo-weak-baseline"
        champion_case = self._prepare_case(
            "promo-strong-champion",
            decision="accepted",
            baseline_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"]},
        )
        candidate_case = self._prepare_case(
            "promo-weak-reference",
            decision="accepted",
            baseline_agents={"c1": ["a"], "c2": ["a"], "c3": [], "c4": []},
            candidate_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": []},
        )
        champion = history_init(
            champion_case, history_root=history_root, stage="baseline", label="Strong champion", agent_version="v1"
        )
        candidate = history_record(
            candidate_case, history_root=history_root, stage="candidate", label="Regressing candidate", agent_version="v2"
        )
        self.assertEqual(champion["pass_rate"], 1.0)
        self.assertEqual(candidate["pass_rate"], 0.75)
        self.assertEqual(candidate["acceptance_recommendation"], "eligible")
        with self.assertRaisesRegex(DataError, "baseline evidence"):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_corrupted_snapshot_fails(self) -> None:
        history_root = self.root / "aik-history-promo-i"
        case_dir = self._prepare_case("promo-i", decision="accepted")
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        run_dir = history_root / "runs" / candidate["run_id"]
        with (run_dir / "test_cases.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_anchor_pointer_is_stable_across_multiple_promotions(self) -> None:
        history_root = self.root / "aik-history-promo-j"
        case_a = self._prepare_case("promo-j-a", decision="accepted")
        case_b = self._prepare_case(
            "promo-j-b",
            decision="accepted",
            baseline_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"]},
        )
        anchor = history_init(case_a, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate1 = history_record(case_a, history_root=history_root, stage="candidate", label="First", agent_version="v2")
        history_promote(candidate1["run_id"], history_root=history_root)
        candidate2 = history_record(case_b, history_root=history_root, stage="candidate", label="Second", agent_version="v3")
        history_promote(candidate2["run_id"], history_root=history_root)
        state = load_state(history_root)
        self.assertEqual(state["anchor_run_id"], anchor["run_id"])
        self.assertEqual(state["champion_run_id"], candidate2["run_id"])

    def test_promotion_event_and_champion_pointer_are_consistent(self) -> None:
        history_root = self.root / "aik-history-promo-k"
        case_dir = self._prepare_case("promo-k", decision="accepted")
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        history_promote(candidate["run_id"], history_root=history_root)
        events = load_events(history_root)
        promoted_events = [event for event in events if event["type"] == "champion_promoted"]
        self.assertEqual(len(promoted_events), 1)
        self.assertEqual(promoted_events[0]["run_id"], candidate["run_id"])
        self.assertEqual(load_state(history_root)["champion_run_id"], promoted_events[0]["run_id"])
        errors, _warnings = history_validate(history_root)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
