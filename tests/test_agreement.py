from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agreement import bootstrap_kappa, cohen_kappa, measure_agreement, read_expert_decisions  # noqa: E402
from common import DataError, read_json, write_json, write_jsonl  # noqa: E402
from init_case import initialize  # noqa: E402


JUDGE_CONFIG = {
    "id": "refund-policy-judge",
    "version": "0.1.0",
    "criteria": {
        "policy_compliance": {"evaluators": ["delay-window"], "weight": 0.5},
        "evidence_quality": {"evaluators": ["proof-of-damage"], "weight": 0.5},
    },
    "hard_rules": [],
    "verdicts": {"approve_at": 0.85, "escalate_at": 0.5},
}


class KappaTests(unittest.TestCase):
    def test_perfect_agreement_on_two_categories(self) -> None:
        pairs = [("approve", "approve")] * 5 + [("reject", "reject")] * 5
        self.assertEqual(cohen_kappa(pairs)["kappa"], 1.0)

    def test_chance_level_agreement_is_near_zero(self) -> None:
        pairs = [("approve", "approve"), ("approve", "reject"), ("reject", "approve"), ("reject", "reject")]
        self.assertEqual(cohen_kappa(pairs)["kappa"], 0.0)
        self.assertEqual(cohen_kappa(pairs)["observed_agreement"], 0.5)

    def test_a_single_shared_category_makes_kappa_undefined_not_perfect(self) -> None:
        result = cohen_kappa([("approve", "approve")] * 10)
        self.assertIsNone(result["kappa"])
        self.assertEqual(result["observed_agreement"], 1.0)
        self.assertIn("Chance agreement", result["undefined_reason"])

    def test_skewed_data_exposes_a_lazy_rater(self) -> None:
        # The judge approves everything; 9 of 10 cases really are approvals.
        pairs = [("approve", "approve")] * 9 + [("approve", "reject")]
        result = cohen_kappa(pairs)
        self.assertEqual(result["observed_agreement"], 0.9)
        self.assertEqual(result["kappa"], 0.0)  # raw agreement flatters, kappa does not

    def test_no_pairs(self) -> None:
        self.assertEqual(cohen_kappa([])["n"], 0)
        self.assertIsNone(cohen_kappa([])["kappa"])

    def test_bootstrap_is_deterministic_and_brackets_the_estimate(self) -> None:
        pairs = [("approve", "approve")] * 8 + [("reject", "reject")] * 8 + [("approve", "reject")] * 4
        first = bootstrap_kappa(pairs, resamples=200)
        second = bootstrap_kappa(pairs, resamples=200)
        self.assertEqual(first, second)
        self.assertLessEqual(first["ci_low"], cohen_kappa(pairs)["kappa"])
        self.assertGreaterEqual(first["ci_high"], cohen_kappa(pairs)["kappa"])
        self.assertIsNone(bootstrap_kappa([("approve", "approve")]))


class DecisionFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "expert_decisions.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, *records: dict) -> Path:
        write_jsonl(self.path, list(records))
        return self.path

    def test_allows_several_experts_per_case(self) -> None:
        self._write(
            {"case_id": "a", "expert": "amina", "verdict": "approve"},
            {"case_id": "a", "expert": "luc", "verdict": "escalate"},
        )
        self.assertEqual(len(read_expert_decisions(self.path)), 2)

    def test_rejects_a_duplicate_expert_and_an_unknown_verdict(self) -> None:
        self._write(
            {"case_id": "a", "expert": "amina", "verdict": "approve"},
            {"case_id": "a", "expert": "amina", "verdict": "reject"},
        )
        with self.assertRaises(DataError) as duplicate:
            read_expert_decisions(self.path)
        self.assertIn("twice", str(duplicate.exception))

        self._write({"case_id": "a", "expert": "amina", "verdict": "maybe"})
        with self.assertRaises(DataError) as unknown:
            read_expert_decisions(self.path)
        self.assertIn("Unknown verdict", str(unknown.exception))


class AgreementCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case_dir = initialize("Calibration", Path(self.temp.name) / "improvements")
        self.ids = [f"dossier-{index}" for index in range(6)]
        write_jsonl(
            self.case_dir / "test_cases.jsonl",
            [
                {
                    "id": case_id,
                    "description": case_id,
                    "input": {"c": case_id},
                    "expected": {"goal": case_id},
                    "tags": ["refund"],
                    "risk": "high",
                    "source": "production",
                    "split": "development",
                    "status": "accepted",
                }
                for case_id in self.ids
            ],
        )
        suite = read_json(self.case_dir / "evaluation.json")
        suite["evaluators"] = [
            {"id": name, "type": "output_contains", "required": True}
            for name in ("delay-window", "proof-of-damage")
        ]
        suite["judge"] = JUDGE_CONFIG
        write_json(self.case_dir / "evaluation.json", suite)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _evaluated(self, outcomes: dict[str, tuple[str, str]]) -> None:
        write_jsonl(
            self.case_dir / "candidate.evaluated.jsonl",
            [
                {
                    "case_id": case_id,
                    "passed": None,
                    "evaluations": [
                        {"evaluator_id": "delay-window", "status": delay, "reason": "", "kind": "deterministic"},
                        {"evaluator_id": "proof-of-damage", "status": proof, "reason": "", "kind": "deterministic"},
                    ],
                }
                for case_id, (delay, proof) in outcomes.items()
            ],
        )

    def _decisions(self, *records: dict) -> None:
        write_jsonl(self.case_dir / "expert_decisions.jsonl", list(records))

    def test_measures_the_human_ceiling_and_flags_contested_cases(self) -> None:
        self._evaluated({case_id: ("pass", "pass") for case_id in self.ids})
        self._decisions(
            *[{"case_id": case_id, "expert": "amina", "verdict": "approve"} for case_id in self.ids[:5]],
            {"case_id": self.ids[5], "expert": "amina", "verdict": "approve"},
            *[{"case_id": case_id, "expert": "luc", "verdict": "approve"} for case_id in self.ids[:5]],
            {"case_id": self.ids[5], "expert": "luc", "verdict": "reject"},
        )
        report = measure_agreement(self.case_dir)
        self.assertEqual(report["experts"], ["amina", "luc"])
        self.assertEqual(report["summary"]["contested_cases"], 1)
        self.assertEqual(report["summary"]["uncontested_cases"], 5)
        self.assertEqual(report["contested"][0]["case_id"], self.ids[5])
        self.assertIn("amina vs luc", report["expert_vs_expert"])
        self.assertTrue(any("contested" in note for note in report["interpretation"]))

    def test_abstentions_are_excluded_from_kappa_but_reported(self) -> None:
        outcomes = {case_id: ("pass", "pass") for case_id in self.ids}
        outcomes[self.ids[0]] = ("error", "error")
        outcomes[self.ids[1]] = ("error", "error")
        self._evaluated(outcomes)
        self._decisions(*[{"case_id": case_id, "expert": "amina", "verdict": "approve"} for case_id in self.ids])
        report = measure_agreement(self.case_dir)
        self.assertEqual(report["summary"]["judge_abstained"], 2)
        self.assertEqual(report["summary"]["scored_cases"], 4)
        self.assertEqual(report["judge_vs_consensus"]["n"], 4)
        self.assertTrue(any("abstained on 2" in note for note in report["interpretation"]))

    def test_warns_when_there_is_no_second_expert(self) -> None:
        self._evaluated({case_id: ("pass", "pass") for case_id in self.ids})
        self._decisions(*[{"case_id": case_id, "expert": "amina", "verdict": "approve"} for case_id in self.ids])
        report = measure_agreement(self.case_dir)
        self.assertEqual(report["expert_vs_expert"], {})
        self.assertIsNone(report["human_ceiling"]["lowest_pair_kappa"])
        self.assertTrue(any("no human ceiling" in note for note in report["interpretation"]))

    def test_disagreements_carry_the_failing_criteria(self) -> None:
        outcomes = {case_id: ("pass", "pass") for case_id in self.ids}
        outcomes[self.ids[0]] = ("fail", "fail")
        self._evaluated(outcomes)
        self._decisions(*[{"case_id": case_id, "expert": "amina", "verdict": "approve"} for case_id in self.ids])
        report = measure_agreement(self.case_dir)
        disagreement = next(item for item in report["disagreements"] if item["case_id"] == self.ids[0])
        self.assertEqual(disagreement["judge_verdict"], "reject")
        self.assertEqual(disagreement["expert_verdict"], "approve")
        self.assertEqual(disagreement["failing_criteria"], ["evidence_quality", "policy_compliance"])

    def test_rejects_decisions_for_an_unknown_case(self) -> None:
        self._evaluated({case_id: ("pass", "pass") for case_id in self.ids})
        self._decisions({"case_id": "ghost", "expert": "amina", "verdict": "approve"})
        with self.assertRaises(DataError) as raised:
            measure_agreement(self.case_dir)
        self.assertIn("unknown case", str(raised.exception))

    def test_cli_reports_agreement(self) -> None:
        self._evaluated({case_id: ("pass", "pass") for case_id in self.ids})
        self._decisions(
            *[{"case_id": case_id, "expert": "amina", "verdict": "approve"} for case_id in self.ids[:4]],
            *[{"case_id": case_id, "expert": "amina", "verdict": "reject"} for case_id in self.ids[4:]],
        )
        completed = subprocess.run(
            [sys.executable, str(ROOT / "aik.py"), "agreement", str(self.case_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("judge vs consensus", completed.stdout)
        self.assertTrue((self.case_dir / "agreement.json").is_file())


if __name__ == "__main__":
    unittest.main()
