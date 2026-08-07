from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import DataError, read_json, write_json, write_jsonl  # noqa: E402
from init_case import initialize  # noqa: E402
from objectives import (  # noqa: E402
    case_cost_usd,
    check_objective_gates,
    percentile,
    run_objectives,
    validate_objectives_config,
)
from pareto import build_pareto, dominates, parse_variant  # noqa: E402


def _case(case_id: str, risk: str = "medium") -> dict:
    return {
        "id": case_id,
        "description": f"Behavior {case_id}",
        "input": {"case": case_id},
        "expected": {"goal": case_id},
        "tags": ["x"],
        "risk": risk,
        "source": "test",
        "split": "development",
        "status": "accepted",
    }


def _result(case_id: str, passed: bool, cost: float | None, duration: float | None) -> dict:
    record: dict = {
        "case_id": case_id,
        "passed": passed,
        "verdict": "pass" if passed else "fail",
        "output": "text",
        "evaluations": [],
    }
    if cost is not None:
        record["cost"] = {"total_usd": cost}
    if duration is not None:
        record["execution"] = {"duration_ms": duration, "exit_code": 0}
    return record


class ObjectiveMetricTests(unittest.TestCase):
    def test_unpriced_run_is_not_free(self) -> None:
        metrics = run_objectives(
            [_result("a", True, 0.01, 100), _result("b", True, None, 120)]
        )
        self.assertFalse(metrics["cost"]["complete"])
        self.assertIsNone(metrics["cost"]["per_case_usd"])
        self.assertIsNone(metrics["cost"]["total_usd"])
        self.assertEqual(metrics["cost"]["priced_cases"], 1)

    def test_aggregates_quality_cost_and_latency(self) -> None:
        metrics = run_objectives(
            [_result("a", True, 0.02, 100), _result("b", False, 0.04, 300)]
        )
        self.assertEqual(metrics["quality"]["pass_rate"], 0.5)
        self.assertEqual(metrics["cost"]["per_case_usd"], 0.03)
        self.assertEqual(metrics["cost"]["total_usd"], 0.06)
        self.assertEqual(metrics["latency"]["mean_ms"], 200.0)
        self.assertTrue(metrics["latency"]["complete"])

    def test_negative_and_boolean_cost_is_rejected(self) -> None:
        self.assertIsNone(case_cost_usd({"cost": {"total_usd": -1}}))
        self.assertIsNone(case_cost_usd({"cost": {"total_usd": True}}))
        self.assertIsNone(case_cost_usd({"cost": "0.01"}))
        self.assertEqual(case_cost_usd({"cost": {"total_usd": 0}}), 0.0)

    def test_percentile_is_interpolated_and_deterministic(self) -> None:
        self.assertEqual(percentile([10, 20, 30, 40], 0.5), 25.0)
        self.assertEqual(percentile([5], 0.95), 5.0)
        self.assertIsNone(percentile([], 0.95))
        with self.assertRaises(DataError):
            percentile([1, 2], 1.5)

    def test_objective_gates_report_unmeasured_values(self) -> None:
        metrics = run_objectives([_result("a", True, None, 100)])
        failures = check_objective_gates(metrics, {"maximum_cost_per_case_usd": 0.01})
        self.assertEqual(len(failures), 1)
        self.assertIn("was not measured", failures[0])

    def test_objectives_config_validation(self) -> None:
        validate_objectives_config({"minimum_pass_rate": 0.9, "require_cost": True})
        with self.assertRaises(DataError):
            validate_objectives_config({"minimum_pass_rate": 2})
        with self.assertRaises(DataError):
            validate_objectives_config({"cheapest": True})
        with self.assertRaises(DataError):
            validate_objectives_config({"maximum_latency_p95_ms": -5})


class DominanceTests(unittest.TestCase):
    def test_direction_per_objective(self) -> None:
        # (pass_rate up, cost down, latency down)
        self.assertTrue(dominates([0.9, 0.01, 100], [0.9, 0.02, 100]))
        self.assertTrue(dominates([1.0, 0.01, 100], [0.9, 0.01, 100]))
        self.assertFalse(dominates([0.9, 0.01, 100], [0.9, 0.01, 100]))
        self.assertFalse(dominates([1.0, 0.05, 100], [0.9, 0.01, 100]))


class ParetoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case_dir = initialize("Cost Sweep", self.root / "improvements")
        write_jsonl(self.case_dir / "test_cases.jsonl", [_case("a"), _case("b", "critical")])
        self._write_variant("baseline.evaluated.jsonl", [(True, 0.10, 500), (True, 0.10, 700)])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_variant(self, filename: str, rows: list[tuple[bool, float | None, float | None]]) -> Path:
        path = self.case_dir / filename
        write_jsonl(
            path,
            [
                _result(case_id, passed, cost, duration)
                for case_id, (passed, cost, duration) in zip(("a", "b"), rows)
            ],
        )
        return path

    def _set_objectives(self, objectives: dict) -> None:
        suite = read_json(self.case_dir / "evaluation.json")
        suite["objectives"] = objectives
        write_json(self.case_dir / "evaluation.json", suite)

    def test_cheaper_equal_quality_variant_dominates(self) -> None:
        cheap = self._write_variant("cheap.evaluated.jsonl", [(True, 0.01, 400), (True, 0.01, 600)])
        report = build_pareto(
            self.case_dir,
            [("expensive", self.case_dir / "baseline.evaluated.jsonl"), ("cheap", cheap)],
        )
        self.assertEqual(report["front"], ["cheap"])
        self.assertEqual(report["cheapest_feasible"], "cheap")
        expensive = next(item for item in report["variants"] if item["name"] == "expensive")
        self.assertEqual(expensive["status"], "dominated")
        self.assertEqual(expensive["dominated_by"], ["cheap"])

    def test_a_cheap_variant_failing_a_critical_case_is_rejected_not_ranked(self) -> None:
        broken = self._write_variant("broken.evaluated.jsonl", [(True, 0.001, 50), (False, 0.001, 50)])
        report = build_pareto(
            self.case_dir,
            [("expensive", self.case_dir / "baseline.evaluated.jsonl"), ("dirt-cheap", broken)],
        )
        entry = next(item for item in report["variants"] if item["name"] == "dirt-cheap")
        self.assertEqual(entry["status"], "rejected")
        self.assertNotIn("dirt-cheap", report["front"])
        self.assertNotEqual(report["cheapest_feasible"], "dirt-cheap")
        self.assertTrue(any("critical" in reason.lower() for reason in entry["blocking_reasons"]))

    def test_absolute_objective_gate_rejects_a_variant(self) -> None:
        self._set_objectives({"maximum_latency_p95_ms": 100})
        slow = self._write_variant("slow.evaluated.jsonl", [(True, 0.01, 4000), (True, 0.01, 5000)])
        report = build_pareto(self.case_dir, [("slow", slow)])
        entry = report["variants"][0]
        self.assertEqual(entry["status"], "rejected")
        self.assertTrue(any("exceeds the allowed" in reason for reason in entry["blocking_reasons"]))

    def test_unpriced_variant_is_not_ranked(self) -> None:
        unpriced = self._write_variant("unpriced.evaluated.jsonl", [(True, None, 100), (True, None, 100)])
        report = build_pareto(
            self.case_dir,
            [("expensive", self.case_dir / "baseline.evaluated.jsonl"), ("unpriced", unpriced)],
        )
        entry = next(item for item in report["variants"] if item["name"] == "unpriced")
        self.assertEqual(entry["status"], "not_comparable")
        self.assertNotIn("unpriced", report["front"])
        self.assertTrue(any("cost.per_case_usd" in reason for reason in entry["review_reasons"]))

    def test_two_incomparable_variants_both_stay_on_the_front(self) -> None:
        fast = self._write_variant("fast.evaluated.jsonl", [(True, 0.09, 10), (True, 0.09, 10)])
        frugal = self._write_variant("frugal.evaluated.jsonl", [(True, 0.01, 900), (True, 0.01, 900)])
        report = build_pareto(self.case_dir, [("fast", fast), ("frugal", frugal)])
        self.assertEqual(sorted(report["front"]), ["fast", "frugal"])
        # The front is ordered by price, so the cheapest survivor is reported first.
        self.assertEqual(report["cheapest_feasible"], "frugal")

    def test_missing_baseline_warns_and_still_ranks(self) -> None:
        (self.case_dir / "baseline.evaluated.jsonl").unlink()
        cheap = self._write_variant("cheap.evaluated.jsonl", [(True, 0.01, 100), (True, 0.01, 100)])
        report = build_pareto(self.case_dir, [("cheap", cheap)])
        self.assertIsNone(report["baseline"])
        self.assertTrue(any("No baseline" in warning for warning in report["warnings"]))
        self.assertEqual(report["front"], ["cheap"])

    def test_rejects_duplicate_and_malformed_variants(self) -> None:
        path = self.case_dir / "baseline.evaluated.jsonl"
        with self.assertRaises(DataError):
            build_pareto(self.case_dir, [("same", path), ("same", path)])
        with self.assertRaises(DataError):
            build_pareto(self.case_dir, [])
        with self.assertRaises(DataError):
            parse_variant("no-equals-sign")
        with self.assertRaises(DataError):
            build_pareto(self.case_dir, [("ghost", self.case_dir / "missing.jsonl")])

    def test_cli_builds_the_front(self) -> None:
        cheap = self._write_variant("cheap.evaluated.jsonl", [(True, 0.01, 100), (True, 0.01, 100)])
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "aik.py"),
                "pareto",
                str(self.case_dir),
                "--variant",
                f"expensive={self.case_dir / 'baseline.evaluated.jsonl'}",
                "--variant",
                f"cheap={cheap}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("front=cheap", completed.stdout)
        report = json.loads((self.case_dir / "pareto.json").read_text(encoding="utf-8"))
        self.assertEqual(report["front"], ["cheap"])


if __name__ == "__main__":
    unittest.main()
