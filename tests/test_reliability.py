from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import DataError, observation_id_for, observation_sort_key, read_json, read_jsonl, write_json, write_jsonl  # noqa: E402
from compare_runs import compare  # noqa: E402
from evaluate_results import evaluate, evaluate_trials  # noqa: E402
from execute_cases import execute  # noqa: E402
from history import history_init, history_promote, history_record, load_manifest, verify_run_integrity  # noqa: E402
from init_case import initialize  # noqa: E402
from render_review import render  # noqa: E402
from run_review import run as build_review  # noqa: E402
from reliability import (  # noqa: E402
    aggregate_stage,
    bootstrap_delta,
    build_reliability,
    combine_recommendations,
    dataset_health,
    evaluator_stats,
    find_incompatible_evaluators,
    hierarchical_bootstrap_delta,
    mean_and_stdev,
    paired_case_deltas,
    paired_observations_by_case,
    reliability_acceptance,
    resolve_config,
    run_reliability,
    wilson_interval,
)


def _observation(case_id: str, repetition: int, passed: bool | None, *, score: float | None = None, evaluator_version: str = "1", status: str | None = None) -> dict:
    if status is None:
        status = "pass" if passed is True else "fail" if passed is False else "error"
    evaluation = {"evaluator_id": "quality", "evaluator_version": evaluator_version, "status": status}
    if score is not None:
        evaluation["score"] = score
    return {
        "case_id": case_id,
        "repetition": repetition,
        "observation_id": observation_id_for(case_id, repetition),
        "passed": passed,
        "verdict": "pass" if passed is True else "fail" if passed is False else "manual_review",
        "evaluations": [evaluation],
    }


class WilsonIntervalTests(unittest.TestCase):
    def test_known_example_n100_x50(self) -> None:
        lower, upper = wilson_interval(50, 100, 0.95)
        self.assertAlmostEqual(lower, 0.4038, places=3)
        self.assertAlmostEqual(upper, 0.5962, places=3)

    def test_extremes_do_not_raise(self) -> None:
        self.assertEqual(wilson_interval(0, 5, 0.95), wilson_interval(0, 5, 0.95))
        lower, upper = wilson_interval(5, 5, 0.95)
        self.assertGreater(lower, 0.0)
        self.assertEqual(upper, 1.0)

    def test_no_observations_returns_none(self) -> None:
        self.assertIsNone(wilson_interval(0, 0, 0.95))

    def test_invalid_successes_raises(self) -> None:
        with self.assertRaises(DataError):
            wilson_interval(6, 5, 0.95)


class MeanStdevTests(unittest.TestCase):
    def test_missing_scores_are_excluded_not_zero(self) -> None:
        mean, stdev = mean_and_stdev([])
        self.assertIsNone(mean)
        self.assertIsNone(stdev)

    def test_stdev_requires_at_least_two_scores(self) -> None:
        mean, stdev = mean_and_stdev([0.8])
        self.assertEqual(mean, 0.8)
        self.assertIsNone(stdev)

    def test_stdev_with_two_or_more_scores(self) -> None:
        mean, stdev = mean_and_stdev([0.6, 0.8, 1.0])
        self.assertAlmostEqual(mean, 0.8)
        self.assertIsNotNone(stdev)
        self.assertGreater(stdev, 0)


class BootstrapDeterminismTests(unittest.TestCase):
    def test_reproducible_with_same_seed(self) -> None:
        deltas = [0.1, 0.2, -0.05, 0.3, 0.0]
        first = bootstrap_delta(deltas, samples=500, confidence_level=0.95, seed=42)
        second = bootstrap_delta(deltas, samples=500, confidence_level=0.95, seed=42)
        self.assertEqual(first, second)

    def test_different_seed_can_differ(self) -> None:
        deltas = [0.03, 0.11, -0.07, 0.19, -0.13, 0.29, 0.02, -0.21, 0.17, 0.05]
        first = bootstrap_delta(deltas, samples=500, confidence_level=0.9, seed=1)
        second = bootstrap_delta(deltas, samples=500, confidence_level=0.9, seed=2)
        self.assertNotEqual((first["ci_low"], first["ci_high"]), (second["ci_low"], second["ci_high"]))

    def test_empty_deltas_returns_none_fields(self) -> None:
        result = bootstrap_delta([], samples=100, confidence_level=0.95, seed=1)
        self.assertIsNone(result["delta_mean"])
        self.assertIsNone(result["probability_of_improvement"])
        self.assertEqual(result["paired_cases"], 0)

    def test_all_positive_deltas_give_high_probability(self) -> None:
        result = bootstrap_delta([0.2, 0.25, 0.3, 0.15], samples=2000, confidence_level=0.95, seed=7)
        self.assertGreater(result["probability_of_improvement"], 0.9)

    def test_all_negative_deltas_give_low_probability_and_negative_ci(self) -> None:
        result = bootstrap_delta([-0.2, -0.25, -0.3, -0.15], samples=2000, confidence_level=0.95, seed=7)
        self.assertLess(result["probability_of_improvement"], 0.1)
        self.assertLess(result["ci_high"], 0)


class ObservationOrderIndependenceTests(unittest.TestCase):
    def test_aggregate_stage_is_order_independent(self) -> None:
        records = [
            _observation("c1", 0, True), _observation("c1", 1, False), _observation("c1", 2, True),
            _observation("c2", 0, True), _observation("c2", 1, True),
        ]
        forward = aggregate_stage(records, confidence_level=0.95)
        backward = aggregate_stage(list(reversed(records)), confidence_level=0.95)
        self.assertEqual(forward, backward)

    def test_observation_sort_key_orders_numerically_not_lexically(self) -> None:
        ids = [observation_id_for("c1", i) for i in [10, 2, 1]]
        self.assertEqual(sorted(ids, key=observation_sort_key), [observation_id_for("c1", 1), observation_id_for("c1", 2), observation_id_for("c1", 10)])

    def test_full_reliability_report_is_order_independent(self) -> None:
        cases = [{"id": "c1", "tags": ["x"]}, {"id": "c2", "tags": ["y"]}]
        baseline = [_observation("c1", 0, True), _observation("c1", 1, False), _observation("c2", 0, True), _observation("c2", 1, True)]
        candidate = [_observation("c1", 0, True), _observation("c1", 1, True), _observation("c2", 0, True), _observation("c2", 1, True)]
        forward = build_reliability(cases, baseline, candidate, seed=99)
        backward = build_reliability(cases, list(reversed(baseline)), list(reversed(candidate)), seed=99)
        self.assertEqual(forward["comparison"], backward["comparison"])
        self.assertEqual(forward["stages"], backward["stages"])


class FlakyDetectionTests(unittest.TestCase):
    def test_mixed_pass_fail_marks_case_flaky(self) -> None:
        records = [_observation("c1", 0, True), _observation("c1", 1, False), _observation("c1", 2, True)]
        stage = aggregate_stage(records, confidence_level=0.95)
        self.assertTrue(stage["cases"]["c1"]["flaky"])

    def test_all_pass_is_not_flaky(self) -> None:
        records = [_observation("c1", 0, True), _observation("c1", 1, True)]
        stage = aggregate_stage(records, confidence_level=0.95)
        self.assertFalse(stage["cases"]["c1"]["flaky"])


class EvaluatorVersionCompatibilityTests(unittest.TestCase):
    def test_same_id_different_version_not_aggregated_together(self) -> None:
        records = [
            _observation("c1", 0, True, evaluator_version="1"),
            _observation("c1", 1, True, evaluator_version="2"),
        ]
        stats = evaluator_stats(records)
        self.assertEqual(set(stats), {"quality@1", "quality@2"})
        self.assertEqual(stats["quality@1"]["pass"], 1)
        self.assertEqual(stats["quality@2"]["pass"], 1)

    def test_version_mismatch_between_stages_is_flagged_incompatible(self) -> None:
        baseline_records = [_observation("c1", 0, True, evaluator_version="1")]
        candidate_records = [_observation("c1", 0, True, evaluator_version="2")]
        baseline_stage = aggregate_stage(baseline_records, confidence_level=0.95)
        candidate_stage = aggregate_stage(candidate_records, confidence_level=0.95)
        incompatible = find_incompatible_evaluators(baseline_stage["evaluators"], candidate_stage["evaluators"])
        self.assertEqual(len(incompatible), 1)
        self.assertEqual(incompatible[0]["evaluator_id"], "quality")

    def test_same_version_is_not_flagged(self) -> None:
        baseline_records = [_observation("c1", 0, True, evaluator_version="1")]
        candidate_records = [_observation("c1", 0, True, evaluator_version="1")]
        baseline_stage = aggregate_stage(baseline_records, confidence_level=0.95)
        candidate_stage = aggregate_stage(candidate_records, confidence_level=0.95)
        self.assertEqual(find_incompatible_evaluators(baseline_stage["evaluators"], candidate_stage["evaluators"]), [])


class AcceptanceGateTests(unittest.TestCase):
    def _make_reliability(self, *, config: dict, baseline: list[dict], candidate: list[dict], cases=None) -> dict:
        return build_reliability(cases or [{"id": "c1"}, {"id": "c2"}], baseline, candidate, config=config, seed=1)

    def test_insufficient_repetitions_forces_manual_review(self) -> None:
        config = {"minimum_repetitions": 3, "confidence_level": 0.95, "bootstrap_samples": 200, "minimum_probability_of_improvement": 0.5, "maximum_flaky_case_rate": 1.0}
        baseline = [_observation("c1", 0, True), _observation("c2", 0, True)]
        candidate = [_observation("c1", 0, True), _observation("c2", 0, True)]
        reliability = self._make_reliability(config=config, baseline=baseline, candidate=candidate)
        acceptance = reliability_acceptance(reliability)
        self.assertEqual(acceptance["recommendation"], "manual_review")

    def test_low_probability_of_improvement_is_not_eligible(self) -> None:
        config = {"minimum_repetitions": 1, "confidence_level": 0.95, "bootstrap_samples": 2000, "minimum_probability_of_improvement": 0.9, "maximum_flaky_case_rate": 1.0}
        baseline = [_observation("c1", r, True) for r in range(5)] + [_observation("c2", r, True) for r in range(5)]
        candidate = [_observation("c1", r, r % 2 == 0) for r in range(5)] + [_observation("c2", r, r % 2 == 0) for r in range(5)]
        reliability = self._make_reliability(config=config, baseline=baseline, candidate=candidate)
        acceptance = reliability_acceptance(reliability)
        self.assertNotEqual(acceptance["recommendation"], "eligible")

    def test_clear_improvement_is_eligible(self) -> None:
        config = {"minimum_repetitions": 3, "confidence_level": 0.95, "bootstrap_samples": 2000, "minimum_probability_of_improvement": 0.9, "maximum_flaky_case_rate": 0.5}
        baseline = [_observation("c1", r, False) for r in range(5)] + [_observation("c2", r, False) for r in range(5)]
        candidate = [_observation("c1", r, True) for r in range(5)] + [_observation("c2", r, True) for r in range(5)]
        reliability = self._make_reliability(config=config, baseline=baseline, candidate=candidate)
        acceptance = reliability_acceptance(reliability)
        self.assertEqual(acceptance["recommendation"], "eligible")

    def test_no_paired_cases_forces_manual_review(self) -> None:
        config = {"minimum_repetitions": 1, "confidence_level": 0.95, "bootstrap_samples": 100, "minimum_probability_of_improvement": 0.5, "maximum_flaky_case_rate": 1.0}
        baseline = [_observation("c1", 0, True)]
        candidate = [_observation("c2", 0, True)]
        reliability = self._make_reliability(config=config, baseline=baseline, candidate=candidate)
        self.assertEqual(reliability_acceptance(reliability)["recommendation"], "manual_review")

    def test_incompatible_evaluator_versions_force_manual_review(self) -> None:
        config = {"minimum_repetitions": 1, "confidence_level": 0.95, "bootstrap_samples": 100, "minimum_probability_of_improvement": 0.5, "maximum_flaky_case_rate": 1.0}
        baseline = [_observation("c1", 0, True, evaluator_version="1")]
        candidate = [_observation("c1", 0, True, evaluator_version="2")]
        reliability = self._make_reliability(config=config, baseline=baseline, candidate=candidate)
        self.assertEqual(reliability_acceptance(reliability)["recommendation"], "manual_review")


class CombineRecommendationsTests(unittest.TestCase):
    def test_no_reliability_config_keeps_deterministic_unchanged(self) -> None:
        self.assertEqual(combine_recommendations("eligible", None), "eligible")
        self.assertEqual(combine_recommendations("rejected", None), "rejected")

    def test_statistics_cannot_upgrade_a_deterministic_rejection(self) -> None:
        self.assertEqual(combine_recommendations("rejected", "eligible"), "rejected")

    def test_statistics_can_downgrade_eligible_to_manual_review(self) -> None:
        self.assertEqual(combine_recommendations("eligible", "manual_review"), "manual_review")

    def test_both_eligible_is_eligible(self) -> None:
        self.assertEqual(combine_recommendations("eligible", "eligible"), "eligible")


class DatasetHealthTests(unittest.TestCase):
    def test_does_not_mutate_input_cases(self) -> None:
        cases = [{"id": "c1", "tags": ["x"]}]
        baseline = [_observation("c1", 0, True)]
        candidate = [_observation("c1", 0, True)]
        snapshot = [dict(case) for case in cases]
        build_reliability(cases, baseline, candidate, seed=1)
        self.assertEqual(cases, snapshot)

    def test_consistently_easy_and_failing_are_identified(self) -> None:
        cases = [{"id": "easy", "tags": []}, {"id": "hard", "tags": []}]
        baseline = [_observation("easy", 0, True), _observation("easy", 1, True), _observation("hard", 0, False), _observation("hard", 1, False)]
        candidate = [_observation("easy", 0, True), _observation("easy", 1, True), _observation("hard", 0, False), _observation("hard", 1, False)]
        reliability = build_reliability(cases, baseline, candidate, seed=1)
        self.assertIn("easy", reliability["dataset_health"]["consistently_easy"])
        self.assertIn("hard", reliability["dataset_health"]["consistently_failing"])


class PairedDeltaTests(unittest.TestCase):
    def test_pairs_only_cases_present_in_both_stages_with_boolean_evidence(self) -> None:
        baseline_records = [_observation("c1", 0, True), _observation("c2", 0, None, status="error")]
        candidate_records = [_observation("c1", 0, False), _observation("c3", 0, True)]
        baseline_stage = aggregate_stage(baseline_records, confidence_level=0.95)
        candidate_stage = aggregate_stage(candidate_records, confidence_level=0.95)
        deltas = paired_case_deltas(baseline_stage["cases"], candidate_stage["cases"])
        self.assertEqual(set(deltas), {"c1"})
        self.assertAlmostEqual(deltas["c1"], -1.0)


class CompareRunsIntegrationTests(unittest.TestCase):
    """Integration tests for combining the deterministic (Sprint 4) recommendation
    from compare_runs.compare() with an archived reliability.json report."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_single_run(self, *, baseline_passed: dict[str, bool], candidate_passed: dict[str, bool]) -> tuple[Path, Path, Path]:
        cases = [
            {"id": case_id, "description": case_id, "input": {}, "expected": {}, "tags": [], "risk": "medium", "source": "test", "split": "development", "status": "accepted"}
            for case_id in baseline_passed
        ]
        test_cases_path = self.root / "test_cases.jsonl"
        baseline_path = self.root / "baseline.jsonl"
        candidate_path = self.root / "candidate.jsonl"
        write_jsonl(test_cases_path, cases)
        write_jsonl(baseline_path, [{"case_id": case_id, "passed": passed, "output": ""} for case_id, passed in baseline_passed.items()])
        write_jsonl(candidate_path, [{"case_id": case_id, "passed": passed, "output": ""} for case_id, passed in candidate_passed.items()])
        return test_cases_path, baseline_path, candidate_path

    def test_deterministic_rejection_cannot_be_overridden_by_eligible_statistics(self) -> None:
        # A protected high-risk regression makes the deterministic policy reject,
        # even though the statistical evidence (constructed here as strongly
        # favorable) would otherwise be eligible on its own.
        cases = [
            {"id": "c1", "description": "c1", "input": {}, "expected": {}, "tags": [], "risk": "high", "source": "test", "split": "development", "status": "accepted"}
        ]
        test_cases_path = self.root / "test_cases.jsonl"
        baseline_path = self.root / "baseline.jsonl"
        candidate_path = self.root / "candidate.jsonl"
        write_jsonl(test_cases_path, cases)
        write_jsonl(baseline_path, [{"case_id": "c1", "passed": True, "output": ""}])
        write_jsonl(candidate_path, [{"case_id": "c1", "passed": False, "output": ""}])  # protected regression
        suite = {"acceptance": {"no_regression_at_or_above_risk": "high", "maximum_regressions": 0}}

        baseline_trials = [_observation("c1", r, r < 1) for r in range(5)]
        candidate_trials = [_observation("c1", r, True) for r in range(5)]
        reliability = build_reliability(
            [{"id": "c1"}], baseline_trials, candidate_trials,
            config={"minimum_probability_of_improvement": 0.5, "bootstrap_samples": 2000}, seed=1,
        )
        self.assertEqual(reliability_acceptance(reliability)["recommendation"], "eligible")

        result = compare(test_cases_path, baseline_path, candidate_path, suite, reliability)
        self.assertEqual(result["acceptance"]["recommendation"], "rejected")

    def test_likely_improvement_below_probability_threshold_is_not_eligible(self) -> None:
        test_cases_path, baseline_path, candidate_path = self._write_single_run(
            baseline_passed={"c1": True, "c2": True}, candidate_passed={"c1": True, "c2": True}
        )
        suite = {"acceptance": {}}
        baseline_trials = [_observation("c1", r, True) for r in range(5)] + [_observation("c2", r, True) for r in range(5)]
        candidate_trials = [_observation("c1", r, r < 3) for r in range(5)] + [_observation("c2", r, r < 3) for r in range(5)]
        reliability = build_reliability(
            [{"id": "c1"}, {"id": "c2"}], baseline_trials, candidate_trials,
            config={"minimum_probability_of_improvement": 0.95, "bootstrap_samples": 2000}, seed=3,
        )
        result = compare(test_cases_path, baseline_path, candidate_path, suite, reliability)
        self.assertNotEqual(result["acceptance"]["recommendation"], "eligible")

    def test_without_reliability_config_behavior_is_unchanged(self) -> None:
        test_cases_path, baseline_path, candidate_path = self._write_single_run(
            baseline_passed={"c1": False}, candidate_passed={"c1": True}
        )
        with_reliability = compare(test_cases_path, baseline_path, candidate_path, {"acceptance": {}}, None)
        without_call = compare(test_cases_path, baseline_path, candidate_path, {"acceptance": {}})
        self.assertEqual(with_reliability["acceptance"], without_call["acceptance"])
        self.assertNotIn("statistical_reliability", without_call["acceptance"])


_HISTORY_CASES = [
    {"id": "c1", "description": "c1", "input": {}, "expected": {"required_agents": ["a"]}, "tags": [], "risk": "medium", "source": "test", "split": "development", "status": "accepted"},
    {"id": "c2", "description": "c2", "input": {}, "expected": {"required_agents": ["a"]}, "tags": [], "risk": "medium", "source": "test", "split": "development", "status": "accepted"},
]
_HISTORY_EVALUATORS = [
    {"id": "execution-ok", "type": "no_execution_error", "required": True},
    {"id": "required-agents", "type": "required_agents", "required": True},
]


def _raw_single(agents_by_case: dict[str, list[str]]) -> list[dict]:
    return [{"case_id": case_id, "output": "ok", "agents": agents, "tool_calls": [], "error": None} for case_id, agents in agents_by_case.items()]


def _raw_trials(agents_by_case: dict[str, list[str]], repetitions: int) -> list[dict]:
    records = []
    for case_id, agents in agents_by_case.items():
        for repetition in range(repetitions):
            records.append(
                {
                    "case_id": case_id,
                    "repetition": repetition,
                    "observation_id": observation_id_for(case_id, repetition),
                    "output": "ok",
                    "agents": agents,
                    "tool_calls": [],
                    "error": None,
                }
            )
    return records


class HistoryReliabilityArchivingTests(unittest.TestCase):
    """Adversarial tests: archived statistical evidence must be tamper-evident,
    and a statistically weak candidate must never become champion."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare_case(
        self,
        name: str,
        *,
        baseline_single: dict[str, list[str]],
        candidate_single: dict[str, list[str]],
        baseline_trials: dict[str, list[str]],
        candidate_trials: dict[str, list[str]],
        repetitions: int = 5,
        reliability_config: dict | None = None,
        decision: str = "accepted",
    ) -> Path:
        case_dir = initialize(name, self.root / "improvements")
        write_jsonl(case_dir / "test_cases.jsonl", _HISTORY_CASES)
        write_json(
            case_dir / "evaluation.json",
            {
                "format_version": "1.0",
                "suite_id": f"{name}-suite",
                "version": "1.0.0",
                "evaluators": _HISTORY_EVALUATORS,
                "acceptance": {
                    "reject_on_evaluator_error": True,
                    "no_regression_at_or_above_risk": "high",
                    "critical_cases_must_pass": True,
                    "maximum_regressions": 0,
                    "minimum_candidate_pass_rate": 0.0,
                    "minimum_delta": 0.0,
                },
                "reliability": reliability_config
                or {
                    "minimum_repetitions": 3,
                    "confidence_level": 0.95,
                    "bootstrap_samples": 500,
                    "minimum_probability_of_improvement": 0.9,
                    "maximum_flaky_case_rate": 0.5,
                },
            },
        )
        write_jsonl(case_dir / "baseline.raw.jsonl", _raw_single(baseline_single))
        write_jsonl(case_dir / "candidate.raw.jsonl", _raw_single(candidate_single))
        evaluate(case_dir, "baseline", overwrite=True)
        evaluate(case_dir, "candidate", overwrite=True)
        write_jsonl(case_dir / "baseline.trials.raw.jsonl", _raw_trials(baseline_trials, repetitions))
        write_jsonl(case_dir / "candidate.trials.raw.jsonl", _raw_trials(candidate_trials, repetitions))
        evaluate_trials(case_dir, "baseline", overwrite=True)
        evaluate_trials(case_dir, "candidate", overwrite=True)
        reliability = run_reliability(case_dir)
        write_json(case_dir / "reliability.json", reliability)
        build_review(case_dir)
        (case_dir / "decision.yaml").write_text(f"status: {decision}\n", encoding="utf-8")
        return case_dir

    def test_archived_reliability_tampering_is_detected(self) -> None:
        case_dir = self._prepare_case(
            "reliab-a",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertIsNotNone(candidate.get("reliability"))

        run_dir = history_root / "runs" / candidate["run_id"]
        reliability_data = read_json(run_dir / "reliability.json")
        reliability_data["comparison"]["probability_of_improvement"] = 0.99
        write_json(run_dir / "reliability.json", reliability_data)

        problems = verify_run_integrity(history_root, candidate["run_id"])
        self.assertTrue(any("reliability.json" in problem and "modified" in problem for problem in problems))
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_manifest_only_statistics_tampering_is_detected(self) -> None:
        case_dir = self._prepare_case(
            "reliab-b",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")

        manifest_path = history_root / "runs" / candidate["run_id"] / "manifest.json"
        manifest = read_json(manifest_path)
        manifest["reliability"]["recommendation"] = "eligible"
        manifest["reliability"]["probability_of_improvement"] = 0.99
        write_json(manifest_path, manifest)

        problems = verify_run_integrity(history_root, candidate["run_id"])
        self.assertTrue(any("reliability summary" in problem for problem in problems))
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_insufficient_repetitions_block_promotion(self) -> None:
        case_dir = self._prepare_case(
            "reliab-c",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
            repetitions=1,  # below the configured minimum_repetitions=3
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertNotEqual(candidate["acceptance_recommendation"], "eligible")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    def test_statistically_uncertain_candidate_does_not_become_champion(self) -> None:
        # Deterministically eligible on the single archived run (both pass), but
        # the repeated trials show the candidate mostly failing (only the one
        # repetition consistent with the single run passes): statistics must
        # veto promotion even though the one-shot comparison looked fine. This
        # is genuine flakiness, not a wholesale mismatch with the single run
        # (which is a separate, stricter tamper check — see
        # test_archived_reliability_tampering_is_detected and friends).
        case_dir = self._prepare_case(
            "reliab-d",
            baseline_single={"c1": ["a"], "c2": ["a"]},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": ["a"], "c2": ["a"]},
            candidate_trials={"c1": [], "c2": []},
            repetitions=5,
        )
        # Make the candidate trials genuinely flaky rather than flatly
        # contradicting the single-run verdict: one repetition per case
        # matches the single run's "pass", the rest fail.
        candidate_trials_path = case_dir / "candidate.trials.raw.jsonl"
        candidate_trials = read_jsonl(candidate_trials_path, "observation_id")
        for record in candidate_trials:
            if record["repetition"] == 0:
                record["agents"] = ["a"]
        write_jsonl(candidate_trials_path, candidate_trials)
        evaluate_trials(case_dir, "candidate", overwrite=True)
        reliability = run_reliability(case_dir)
        write_json(case_dir / "reliability.json", reliability)
        build_review(case_dir)

        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertNotEqual(candidate["acceptance_recommendation"], "eligible")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)


class ReviewFixAdversarialTests(unittest.TestCase):
    """The 11 adversarial/regression tests required by the independent Sprint 6
    review: repeated evaluator errors, reliability bound to real trials,
    hierarchical bootstrap uncertainty, and strictly optional statistics."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare_case(
        self,
        name: str,
        *,
        baseline_single: dict[str, list[str]],
        candidate_single: dict[str, list[str]],
        baseline_trials: dict[str, list[str]],
        candidate_trials: dict[str, list[str]],
        repetitions: int = 5,
        reliability_config: dict | None = None,
        decision: str = "accepted",
        write_reliability: bool = True,
        cases: list[dict] | None = None,
    ) -> Path:
        case_dir = initialize(name, self.root / "improvements")
        write_jsonl(case_dir / "test_cases.jsonl", cases if cases is not None else _HISTORY_CASES)
        write_json(
            case_dir / "evaluation.json",
            {
                "format_version": "1.0",
                "suite_id": f"{name}-suite",
                "version": "1.0.0",
                "evaluators": _HISTORY_EVALUATORS,
                "acceptance": {
                    "reject_on_evaluator_error": True,
                    "no_regression_at_or_above_risk": "high",
                    "critical_cases_must_pass": True,
                    "maximum_regressions": 0,
                    "minimum_candidate_pass_rate": 0.0,
                    "minimum_delta": 0.0,
                },
                "reliability": reliability_config
                or {
                    "minimum_repetitions": 3,
                    "confidence_level": 0.95,
                    "bootstrap_samples": 500,
                    "minimum_probability_of_improvement": 0.9,
                    "maximum_flaky_case_rate": 1.0,
                },
            },
        )
        write_jsonl(case_dir / "baseline.raw.jsonl", _raw_single(baseline_single))
        write_jsonl(case_dir / "candidate.raw.jsonl", _raw_single(candidate_single))
        evaluate(case_dir, "baseline", overwrite=True)
        evaluate(case_dir, "candidate", overwrite=True)
        write_jsonl(case_dir / "baseline.trials.raw.jsonl", _raw_trials(baseline_trials, repetitions))
        write_jsonl(case_dir / "candidate.trials.raw.jsonl", _raw_trials(candidate_trials, repetitions))
        evaluate_trials(case_dir, "baseline", overwrite=True)
        evaluate_trials(case_dir, "candidate", overwrite=True)
        if write_reliability:
            reliability = run_reliability(case_dir)
            write_json(case_dir / "reliability.json", reliability)
            build_review(case_dir)
        (case_dir / "decision.yaml").write_text(f"status: {decision}\n", encoding="utf-8")
        return case_dir

    # 1. One valid result + two evaluator errors does not satisfy minimum_repetitions=3.
    def test_one_valid_plus_two_evaluator_errors_fails_minimum_repetitions(self) -> None:
        def obs(case_id: str, repetition: int, status: str, stage: str) -> dict:
            passed = True if status == "pass" else (False if status == "fail" else None)
            return {
                "case_id": case_id,
                "repetition": repetition,
                "observation_id": observation_id_for(case_id, repetition),
                "passed": passed,
                "verdict": status if status in ("pass", "fail") else "manual_review",
                "evaluations": [{"evaluator_id": "judge", "evaluator_version": "1", "status": status}],
                "evaluation_metadata": {"stage": stage, "dataset_fingerprint": "sha256:d", "evaluator_fingerprint": "sha256:e"},
            }

        baseline = [obs("c1", r, "pass", "baseline") for r in range(3)]
        candidate = [obs("c1", 0, "pass", "candidate"), obs("c1", 1, "error", "candidate"), obs("c1", 2, "error", "candidate")]
        evaluators = [{"id": "judge", "type": "llm_rubric", "required": True}]
        config = {
            "minimum_repetitions": 3,
            "confidence_level": 0.95,
            "bootstrap_samples": 200,
            "minimum_probability_of_improvement": 0.5,
            "maximum_flaky_case_rate": 1.0,
        }
        report = build_reliability([{"id": "c1"}], baseline, candidate, config=config, evaluators=evaluators, seed=1)
        self.assertEqual(report["stages"]["candidate"]["cases"]["c1"]["boolean_observations"], 1)
        self.assertEqual(report["stages"]["candidate"]["cases"]["c1"]["required_evaluator_error_observations"], 2)
        acceptance = reliability_acceptance(report, reject_on_evaluator_error=True)
        self.assertEqual(acceptance["recommendation"], "manual_review")
        self.assertNotEqual(acceptance["recommendation"], "eligible")

    def test_repeated_evaluator_errors_block_promotion_end_to_end(self) -> None:
        evaluators = [
            {"id": "execution-ok", "type": "no_execution_error", "required": True},
            {"id": "quality", "type": "llm_rubric", "required": True, "threshold": 0.7},
        ]
        cases = [dict(case, expected={**case["expected"], "rubric": ["ok"]}) for case in _HISTORY_CASES]
        config = {
            "minimum_repetitions": 3,
            "confidence_level": 0.95,
            "bootstrap_samples": 200,
            "minimum_probability_of_improvement": 0.5,
            "maximum_flaky_case_rate": 1.0,
        }
        # No judge command is supplied, so every required "quality" evaluation
        # errors: every repeated observation is manual_review, not a boolean.
        case_dir = self._prepare_case(
            "errors-block",
            baseline_single={"c1": ["a"], "c2": ["a"]},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": ["a"], "c2": ["a"]},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
            reliability_config=config,
            cases=cases,
        )
        # Rewrite the suite's evaluators to include the always-erroring judge
        # for the trial evidence (the single-run evaluate() call above used
        # the default deterministic evaluators only).
        suite = read_json(case_dir / "evaluation.json")
        suite["evaluators"] = evaluators
        write_json(case_dir / "evaluation.json", suite)
        write_jsonl(case_dir / "baseline.trials.raw.jsonl", _raw_trials({"c1": ["a"], "c2": ["a"]}, 5))
        write_jsonl(case_dir / "candidate.trials.raw.jsonl", _raw_trials({"c1": ["a"], "c2": ["a"]}, 5))
        evaluate_trials(case_dir, "baseline", overwrite=True)
        evaluate_trials(case_dir, "candidate", overwrite=True)
        write_jsonl(case_dir / "baseline.raw.jsonl", _raw_single({"c1": ["a"], "c2": ["a"]}))
        write_jsonl(case_dir / "candidate.raw.jsonl", _raw_single({"c1": ["a"], "c2": ["a"]}))
        evaluate(case_dir, "baseline", overwrite=True)
        evaluate(case_dir, "candidate", overwrite=True)
        reliability = run_reliability(case_dir)
        write_json(case_dir / "reliability.json", reliability)
        build_review(case_dir)

        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        self.assertNotEqual(candidate["acceptance_recommendation"], "eligible")
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    # 2. A reliability.json without trials cannot be archived or promoted.
    def test_reliability_json_without_trials_cannot_be_archived(self) -> None:
        case_dir = self._prepare_case(
            "no-trials",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        (case_dir / "baseline.trials.evaluated.jsonl").unlink()
        (case_dir / "candidate.trials.evaluated.jsonl").unlink()
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        with self.assertRaises(DataError):
            history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")

    # 3. A manually fabricated favorable reliability.json cannot be promoted.
    def test_fabricated_favorable_reliability_json_cannot_be_archived(self) -> None:
        case_dir = self._prepare_case(
            "fabricated",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": [], "c2": []},  # genuinely weak candidate trials
        )
        # Overwrite the honestly computed (weak) reliability.json with a
        # hand-fabricated, favorable one that does not match the real trials.
        fabricated = read_json(case_dir / "reliability.json")
        fabricated["comparison"]["probability_of_improvement"] = 0.99
        fabricated["comparison"]["pass_rate_delta"] = 0.9
        fabricated["comparison"]["bootstrap"] = {"ci_low": 0.8, "ci_high": 1.0}
        write_json(case_dir / "reliability.json", fabricated)

        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        with self.assertRaises(DataError):
            history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")

    # 4. A report (and trials) copied from another candidate is rejected.
    def test_reliability_report_copied_from_another_candidate_is_rejected(self) -> None:
        source_case = self._prepare_case(
            "source-good",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        target_case = self._prepare_case(
            "target-bad",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": [], "c2": []},  # the real candidate actually fails
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": [], "c2": []},
        )
        # Borrow the source's favorable trials and reliability.json wholesale.
        import shutil

        for name in ("candidate.trials.evaluated.jsonl", "baseline.trials.evaluated.jsonl", "reliability.json"):
            shutil.copy2(source_case / name, target_case / name)

        history_root = self.root / "aik-history"
        history_init(target_case, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        with self.assertRaises(DataError):
            history_record(target_case, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")

    # 5. Modifying archived trials after recording is detected.
    def test_modifying_archived_trials_after_recording_is_detected(self) -> None:
        case_dir = self._prepare_case(
            "post-tamper-trials",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")

        run_dir = history_root / "runs" / candidate["run_id"]
        trials_path = run_dir / "candidate.trials.evaluated.jsonl"
        with trials_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        problems = verify_run_integrity(history_root, candidate["run_id"])
        self.assertTrue(any("candidate.trials.evaluated.jsonl" in p and "modified" in p for p in problems))
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    # 6. Modifying the archived reliability configuration after recording is detected.
    def test_modifying_archived_reliability_config_after_recording_is_detected(self) -> None:
        case_dir = self._prepare_case(
            "post-tamper-config",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")

        run_dir = history_root / "runs" / candidate["run_id"]
        suite_path = run_dir / "evaluation.json"
        suite = read_json(suite_path)
        suite["reliability"]["minimum_probability_of_improvement"] = 0.0
        write_json(suite_path, suite)
        problems = verify_run_integrity(history_root, candidate["run_id"])
        self.assertTrue(problems)
        with self.assertRaises(DataError):
            history_promote(candidate["run_id"], history_root=history_root)

    # 7. The 1/3 vs 2/3 single-case scenario remains uncertain, not 100% confidence.
    def test_single_case_one_third_vs_two_thirds_remains_uncertain(self) -> None:
        def obs(case_id: str, repetition: int, passed: bool, stage: str) -> dict:
            return {
                "case_id": case_id,
                "repetition": repetition,
                "observation_id": observation_id_for(case_id, repetition),
                "passed": passed,
                "verdict": "pass" if passed else "fail",
                "evaluations": [{"evaluator_id": "q", "evaluator_version": "1", "status": "pass" if passed else "fail"}],
                "evaluation_metadata": {"stage": stage, "dataset_fingerprint": "sha256:d", "evaluator_fingerprint": "sha256:e"},
            }

        baseline = [obs("c1", r, r == 2, "baseline") for r in range(3)]  # 1/3 pass
        candidate = [obs("c1", r, r in (0, 2), "candidate") for r in range(3)]  # 2/3 pass
        report = build_reliability([{"id": "c1"}], baseline, candidate, seed=1337)
        comparison = report["comparison"]
        self.assertEqual(comparison["paired_cases"], 1)
        self.assertEqual(comparison["paired_observations"], 3)
        self.assertAlmostEqual(comparison["pass_rate_delta"], 1 / 3, places=3)
        # The old (flawed) method fabricated P == 1.0 and a zero-width CI.
        self.assertLess(comparison["probability_of_improvement"], 0.95)
        self.assertGreater(comparison["probability_of_improvement"], 0.3)
        self.assertNotEqual(comparison["bootstrap"]["ci_low"], comparison["bootstrap"]["ci_high"])
        acceptance = reliability_acceptance(
            {**report, "config": {**report["config"], "minimum_probability_of_improvement": 0.9}}
        )
        self.assertNotEqual(acceptance["recommendation"], "eligible")

    def test_single_case_order_independence_with_repetitions(self) -> None:
        def obs(case_id: str, repetition: int, passed: bool, stage: str) -> dict:
            return {
                "case_id": case_id,
                "repetition": repetition,
                "observation_id": observation_id_for(case_id, repetition),
                "passed": passed,
                "verdict": "pass" if passed else "fail",
                "evaluations": [{"evaluator_id": "q", "evaluator_version": "1", "status": "pass" if passed else "fail"}],
                "evaluation_metadata": {"stage": stage, "dataset_fingerprint": "sha256:d", "evaluator_fingerprint": "sha256:e"},
            }

        baseline = (
            [obs("c1", r, r % 2 == 0, "baseline") for r in range(4)]
            + [obs("c2", r, r < 1, "baseline") for r in range(4)]
        )
        candidate = (
            [obs("c1", r, r < 3, "candidate") for r in range(4)]
            + [obs("c2", r, r % 2 == 0, "candidate") for r in range(4)]
        )
        cases = [{"id": "c1"}, {"id": "c2"}]
        forward = build_reliability(cases, baseline, candidate, seed=42)
        backward = build_reliability(cases, list(reversed(baseline)), list(reversed(candidate)), seed=42)
        shuffled = build_reliability(cases, baseline[::2] + baseline[1::2], candidate[1::2] + candidate[::2], seed=42)
        self.assertEqual(forward["comparison"], backward["comparison"])
        self.assertEqual(forward["comparison"], shuffled["comparison"])

    # 8. An old reliability.json is ignored when no reliability section is configured.
    def test_stale_reliability_ignored_without_configured_section(self) -> None:
        case_dir = self._prepare_case(
            "stale-ignored",
            baseline_single={"c1": [], "c2": []},
            candidate_single={"c1": ["a"], "c2": ["a"]},
            baseline_trials={"c1": [], "c2": []},
            candidate_trials={"c1": ["a"], "c2": ["a"]},
        )
        # A stale, favorable reliability.json sits on disk, but the section is
        # removed from evaluation.json before generating the review.
        suite = read_json(case_dir / "evaluation.json")
        del suite["reliability"]
        write_json(case_dir / "evaluation.json", suite)

        _comparison_path, _review_path, result = build_review(case_dir)
        self.assertNotIn("statistical_reliability", result["acceptance"])
        self.assertEqual(result["acceptance"]["recommendation"], "eligible")

    # 9. Invalid reliability configuration always raises DataError, never a
    # ZeroDivisionError, KeyError, or IndexError.
    def test_invalid_reliability_config_values_raise_data_error(self) -> None:
        base = {
            "minimum_repetitions": 3,
            "confidence_level": 0.95,
            "bootstrap_samples": 500,
            "minimum_probability_of_improvement": 0.9,
            "maximum_flaky_case_rate": 0.5,
        }
        invalid_overrides = [
            {"bootstrap_samples": 0},
            {"bootstrap_samples": 99},
            {"minimum_repetitions": -1},
            {"minimum_repetitions": 0},
            {"minimum_probability_of_improvement": 2},
            {"minimum_probability_of_improvement": -0.1},
            {"maximum_flaky_case_rate": -1},
            {"maximum_flaky_case_rate": 1.5},
            {"confidence_level": 1.0},
            {"confidence_level": 0.0},
            {"seed": True},
            {"seed": 1.5},
            {"minimum_repetitions": True},
        ]
        for override in invalid_overrides:
            with self.subTest(override=override):
                config = {**base, **override}
                with self.assertRaises(DataError):
                    resolve_config(config)

    def test_zero_bootstrap_samples_never_raises_zero_division(self) -> None:
        # Even calling the low-level bootstrap function directly with a bad
        # sample count must raise DataError, not a raw ZeroDivisionError.
        with self.assertRaises(DataError):
            hierarchical_bootstrap_delta({"c1": [(True, True)]}, samples=0, confidence_level=0.95, seed=1)

    # 10 & 11 (one-shot unchanged, existing suite green) are exercised by the
    # full test run: tests/test_workflow.py and the rest of this module.


class RepeatedExecutionTests(unittest.TestCase):
    """Mechanics of --repetitions through the real external-process runner bridge."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _case(self, modes: list[str] | None = None) -> Path:
        case_dir = initialize("trial-exec", self.root / "improvements")
        chosen = modes or ["normal", "normal"]
        write_jsonl(
            case_dir / "test_cases.jsonl",
            [
                {
                    "id": f"run-{index}",
                    "description": f"Runner case {index}",
                    "input": {"mode": mode},
                    "expected": {},
                    "tags": ["runner"],
                    "risk": "low",
                    "source": "test",
                    "split": "development",
                    "status": "accepted",
                }
                for index, mode in enumerate(chosen, start=1)
            ],
        )
        return case_dir

    def _runner(self) -> list[str]:
        return [sys.executable, str(ROOT / "tests" / "fixtures" / "protocol_runner.py")]

    def test_single_repetition_preserves_legacy_workflow(self) -> None:
        case_dir = self._case()
        summary = execute(case_dir, "baseline", self._runner(), repetitions=1)
        self.assertEqual(summary["executed"], 2)
        self.assertTrue((case_dir / "baseline.raw.jsonl").exists())
        self.assertFalse((case_dir / "baseline.trials.raw.jsonl").exists())
        raw = read_jsonl(case_dir / "baseline.raw.jsonl", "case_id")
        self.assertEqual({record["case_id"] for record in raw}, {"run-1", "run-2"})

    def test_repeated_executions_generate_unique_stable_observation_ids(self) -> None:
        case_dir = self._case()
        execute(case_dir, "baseline", self._runner(), repetitions=4)
        records = read_jsonl(case_dir / "baseline.trials.raw.jsonl", "observation_id")
        ids = [record["observation_id"] for record in records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 8)
        expected = sorted(
            (observation_id_for(f"run-{case}", repetition) for case in (1, 2) for repetition in range(4)),
            key=observation_sort_key,
        )
        self.assertEqual(sorted(ids, key=observation_sort_key), expected)

    def test_resume_skips_only_completed_observations(self) -> None:
        case_dir = self._case()
        execute(case_dir, "baseline", self._runner(), repetitions=3)
        resumed = execute(case_dir, "baseline", self._runner(), repetitions=5, resume=True)
        self.assertEqual(resumed["executed"], 4)
        self.assertEqual(resumed["skipped"], 6)
        records = read_jsonl(case_dir / "baseline.trials.raw.jsonl", "observation_id")
        self.assertEqual(len(records), 10)

    def test_evaluate_trials_resume_skips_only_completed_observations(self) -> None:
        case_dir = self._case()
        execute(case_dir, "baseline", self._runner(), repetitions=3)
        write_json(
            case_dir / "evaluation.json",
            {
                "format_version": "1.0",
                "suite_id": "trial-exec-suite",
                "version": "1.0.0",
                "evaluators": [{"id": "execution-ok", "type": "no_execution_error", "required": True}],
                "acceptance": {},
            },
        )
        evaluate_trials(case_dir, "baseline", selected_ids={"run-1"}, overwrite=True)
        resumed = evaluate_trials(case_dir, "baseline", resume=True)
        self.assertEqual(resumed["executed"], 3)
        self.assertEqual(resumed["skipped"], 3)


class ReviewReliabilityRenderingTests(unittest.TestCase):
    """render_review.py must escape every imported reliability value."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_review_escapes_malicious_reliability_content(self) -> None:
        comparison = {
            "summary": {"improved": 0, "regressed": 0, "unchanged": 1, "not_comparable": 0, "total": 1},
            "items": [
                {
                    "case_id": "c1",
                    "description": "c1",
                    "tags": [],
                    "risk": "high",
                    "status": "unchanged",
                    "warnings": [],
                    "score_deltas": {},
                    "contract_changes": {},
                    "baseline": {"passed": True, "output": ""},
                    "candidate": {"passed": True, "output": ""},
                }
            ],
            "acceptance": {
                "recommendation": "manual_review",
                "reasons": [],
                "statistical_reliability": {"recommendation": "manual_review", "reasons": ["<script>evil()</script>"]},
            },
        }
        comparison_path = self.root / "comparison.json"
        write_json(comparison_path, comparison)
        case_yaml = self.root / "case.yaml"
        case_yaml.write_text("name: t\n", encoding="utf-8")

        reliability = {
            "method": {"version": "v", "confidence_level": 0.95, "bootstrap_samples": 10, "seed": 1},
            "config": {"minimum_probability_of_improvement": 0.9},
            "stages": {
                "baseline": {"overall": {"pass_rate": 1.0, "wilson_interval": [0.5, 1.0]}},
                "candidate": {
                    "overall": {"pass_rate": 1.0, "wilson_interval": [0.5, 1.0]},
                    "evaluators": {"<img src=x onerror=alert(1)>@1": {"pass": 1, "fail": 1}},
                },
            },
            "comparison": {
                "pass_rate_delta": 0.0,
                "bootstrap": {"ci_low": 0.0, "ci_high": 0.0},
                "probability_of_improvement": 0.5,
                "flaky_cases": ["<b>c1</b>"],
                "incompatible_evaluators": [
                    {"evaluator_id": "<script>x()</script>", "baseline_versions": ["1"], "candidate_versions": ["2"]}
                ],
            },
        }

        output_path = self.root / "review.html"
        render(comparison_path, case_yaml, output_path, reliability)
        text = output_path.read_text(encoding="utf-8")
        self.assertNotIn("<script>evil()</script>", text)
        self.assertNotIn("<img src=x onerror=alert(1)>", text)
        self.assertNotIn("<b>c1</b>", text)
        self.assertNotIn("<script>x()</script>", text)
        self.assertIn("&lt;script&gt;evil()&lt;/script&gt;", text)
        self.assertIn("&lt;b&gt;c1&lt;/b&gt;", text)


if __name__ == "__main__":
    unittest.main()
