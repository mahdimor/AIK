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

from common import DataError, write_jsonl  # noqa: E402
from export_feedback import export_feedback, holdout_seal  # noqa: E402
from init_case import initialize  # noqa: E402


def _case(case_id: str, split: str, risk: str = "medium") -> dict:
    return {
        "id": case_id,
        "description": f"Behavior {case_id}",
        "input": {"case": case_id},
        "expected": {"goal": case_id},
        "tags": ["x"],
        "risk": risk,
        "source": "test",
        "split": split,
        "status": "accepted",
    }


class FeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_case(self, splits: tuple[str, ...] = ("development", "regression", "holdout")) -> Path:
        case_dir = initialize("Feedback Case", self.root / "improvements")
        cases = [_case(f"case-{index}", split) for index, split in enumerate(splits)]
        write_jsonl(case_dir / "test_cases.jsonl", cases)
        write_jsonl(
            case_dir / "candidate.evaluated.jsonl",
            [
                {
                    "case_id": case["id"],
                    "passed": case["split"] != "development",
                    "verdict": "pass" if case["split"] != "development" else "fail",
                    "output": f"output for {case['id']}",
                    "evaluations": [
                        {
                            "evaluator_id": "confirmation",
                            "status": "pass" if case["split"] != "development" else "fail",
                            "score": 1.0 if case["split"] != "development" else 0.0,
                            "reason": "created the ticket before the user confirmed",
                        }
                    ],
                }
                for case in cases
            ],
        )
        return case_dir

    def test_holdout_is_never_exported(self) -> None:
        case_dir = self._write_case()
        report = export_feedback(case_dir)
        exported_ids = {item["case_id"] for item in report["items"]}
        self.assertEqual(exported_ids, {"case-0", "case-1"})
        self.assertNotIn("case-2", exported_ids)
        self.assertEqual(report["summary"]["withheld_holdout_cases"], 1)
        self.assertEqual(report["holdout"]["count"], 1)
        # The seal identifies the holdout without describing any of it.
        self.assertNotIn("case_ids", report["holdout"])
        self.assertNotIn("case-2", json.dumps(report))

    def test_refuses_a_dataset_without_holdout(self) -> None:
        case_dir = self._write_case(("development", "regression"))
        with self.assertRaises(DataError) as raised:
            export_feedback(case_dir)
        self.assertIn("no holdout cases", str(raised.exception))

    def test_refuses_a_legacy_dataset(self) -> None:
        case_dir = self._write_case()
        (case_dir / "dataset.json").unlink()
        with self.assertRaises(DataError) as raised:
            export_feedback(case_dir)
        self.assertIn("Dataset v2", str(raised.exception))

    def test_refuses_a_case_without_split(self) -> None:
        case_dir = self._write_case()
        cases = [_case("case-0", "development"), _case("case-1", "holdout")]
        del cases[0]["split"]
        write_jsonl(case_dir / "test_cases.jsonl", cases)
        with self.assertRaises(DataError) as raised:
            export_feedback(case_dir)
        self.assertIn("split", str(raised.exception))

    def test_feedback_text_carries_the_failure_reason(self) -> None:
        case_dir = self._write_case()
        report = export_feedback(case_dir)
        failing = next(item for item in report["items"] if item["passed"] is False)
        self.assertIn("[confirmation] fail", failing["feedback"])
        self.assertIn("before the user confirmed", failing["feedback"])
        self.assertEqual(len(failing["failed_evaluations"]), 1)

    def test_every_export_is_recorded(self) -> None:
        case_dir = self._write_case()
        first = export_feedback(case_dir)
        second = export_feedback(case_dir)
        self.assertEqual(first["export_number"], 1)
        self.assertEqual(second["export_number"], 2)
        ledger = (case_dir / "optimizer-log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(ledger), 2)
        self.assertEqual(json.loads(ledger[0])["holdout"]["count"], 1)

    def test_a_changed_holdout_is_detected(self) -> None:
        case_dir = self._write_case()
        export_feedback(case_dir)
        cases = [_case("case-0", "development"), _case("case-1", "regression"), _case("case-2", "holdout")]
        cases[2]["expected"] = {"goal": "quietly relaxed"}
        write_jsonl(case_dir / "test_cases.jsonl", cases)
        report = export_feedback(case_dir)
        self.assertTrue(any("holdout changed" in warning for warning in report["warnings"]))

    def test_missing_results_do_not_fabricate_feedback(self) -> None:
        case_dir = self._write_case()
        write_jsonl(
            case_dir / "candidate.evaluated.jsonl",
            [{"case_id": "case-0", "passed": True, "verdict": "pass", "output": "ok", "evaluations": []}],
        )
        report = export_feedback(case_dir)
        without_result = next(item for item in report["items"] if item["case_id"] == "case-1")
        self.assertIsNone(without_result["passed"])
        self.assertIn("no result", without_result["feedback"])
        self.assertTrue(any("no candidate result" in warning for warning in report["warnings"]))

    def test_holdout_seal_is_stable_and_content_sensitive(self) -> None:
        cases = [_case("a", "development"), _case("b", "holdout")]
        first = holdout_seal(cases)
        self.assertEqual(first, holdout_seal(list(reversed(cases))))
        cases[1]["expected"] = {"goal": "changed"}
        self.assertNotEqual(first["fingerprint"], holdout_seal(cases)["fingerprint"])

    def test_cli_exports_feedback(self) -> None:
        case_dir = self._write_case()
        completed = subprocess.run(
            [sys.executable, str(ROOT / "aik.py"), "feedback", str(case_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("withheld_holdout_cases=1", completed.stdout)
        self.assertTrue((case_dir / "feedback.json").exists())


if __name__ == "__main__":
    unittest.main()
