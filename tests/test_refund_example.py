"""Keep the refund-extraction example honest.

The example ships the evidence from a real optimization run and its README quotes
numbers from it. These tests fail if the two ever drift apart. Nothing here calls a
model or the network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import index_by, read_jsonl  # noqa: E402
from export_feedback import export_feedback  # noqa: E402
from objectives import run_objectives  # noqa: E402
from validate_case import validate  # noqa: E402

EXAMPLE = ROOT / "examples" / "refund-extraction"


class RefundExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = index_by(read_jsonl(EXAMPLE / "test_cases.jsonl", "id"), "id")

    def _pass_rate_by_split(self, filename: str) -> dict[str, tuple[int, int]]:
        results = read_jsonl(EXAMPLE / filename, "case_id")
        counts: dict[str, list[int]] = {}
        for result in results:
            split = self.cases[result["case_id"]]["split"]
            bucket = counts.setdefault(split, [0, 0])
            bucket[0] += int(result.get("passed") is True)
            bucket[1] += 1
        return {split: (passed, total) for split, (passed, total) in counts.items()}

    def test_the_case_is_valid(self) -> None:
        errors, _warnings = validate(EXAMPLE)
        self.assertEqual(errors, [])

    def test_the_readme_table_matches_the_committed_evidence(self) -> None:
        self.assertEqual(self._pass_rate_by_split("baseline.evaluated.jsonl"), {"development": (0, 6), "holdout": (0, 4)})
        self.assertEqual(self._pass_rate_by_split("candidate.evaluated.jsonl"), {"development": (6, 6), "holdout": (3, 4)})

    def test_the_holdout_failure_is_the_invented_reason_code(self) -> None:
        results = index_by(read_jsonl(EXAMPLE / "candidate.evaluated.jsonl", "case_id"), "case_id")
        failed = [
            case_id
            for case_id, result in results.items()
            if result.get("passed") is not True and self.cases[case_id]["split"] == "holdout"
        ]
        self.assertEqual(failed, ["r7"])
        self.assertIn("incorrect_item", results["r7"]["output"])
        self.assertIn('"reason_code": "wrong_item"', str(self.cases["r7"]["expected"]))

    def test_the_seed_prompt_is_still_the_weak_one(self) -> None:
        seed = (EXAMPLE / "prompts" / "system.md").read_text(encoding="utf-8").strip()
        self.assertEqual(seed, "Extract the refund details from the customer message.")
        optimized = (EXAMPLE / "recorded" / "gepa-system-prompt.md").read_text(encoding="utf-8")
        self.assertIn("reason_code", optimized)
        self.assertGreater(len(optimized), 20 * len(seed))

    def test_the_holdout_is_sealed_from_the_optimizer(self) -> None:
        report = export_feedback(EXAMPLE, stage="baseline", record_export=False)
        try:
            self.assertEqual(report["summary"], {
                "visible_cases": 6,
                "visible_failed": 6,
                "visible_passed": 0,
                "withheld_holdout_cases": 4,
            })
            for item in report["items"]:
                self.assertNotEqual(self.cases[item["case_id"]]["split"], "holdout")
        finally:
            (EXAMPLE / "feedback.json").unlink(missing_ok=True)

    def test_the_optimized_prompt_really_does_cost_more(self) -> None:
        # The README argues a cost objective needs a quality floor. That only holds
        # if the optimized prompt is genuinely the expensive one.
        seed = run_objectives(read_jsonl(EXAMPLE / "baseline.evaluated.jsonl", "case_id"))
        optimized = run_objectives(read_jsonl(EXAMPLE / "candidate.evaluated.jsonl", "case_id"))
        self.assertTrue(seed["cost"]["complete"] and optimized["cost"]["complete"])
        self.assertGreater(optimized["cost"]["per_case_usd"], seed["cost"]["per_case_usd"])
        self.assertEqual(seed["quality"]["pass_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
