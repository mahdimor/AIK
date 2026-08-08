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
from optimize import optimize, validate_optimization_config  # noqa: E402

RULE_OPTIMIZER = ROOT / "examples" / "optimizers" / "rule_optimizer.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _case(case_id: str, split: str) -> dict:
    return {
        "id": case_id,
        "description": f"Behavior {case_id}",
        "input": {"case": case_id},
        "expected": {"goal": case_id},
        "tags": ["x"],
        "risk": "medium",
        "source": "test",
        "split": split,
        "status": "accepted",
    }


class OptimizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case_dir = initialize("Prompt Sweep", self.root / "improvements")
        write_jsonl(
            self.case_dir / "test_cases.jsonl",
            [_case("visible", "development"), _case("guarded", "regression"), _case("sealed", "holdout")],
        )
        write_jsonl(
            self.case_dir / "candidate.evaluated.jsonl",
            [
                {
                    "case_id": "visible",
                    "passed": False,
                    "verdict": "fail",
                    "output": "created the ticket",
                    "evaluations": [
                        {
                            "evaluator_id": "confirmation",
                            "status": "fail",
                            "score": 0.0,
                            "reason": "acted before the user confirmed",
                        }
                    ],
                },
                {"case_id": "guarded", "passed": True, "verdict": "pass", "output": "ok", "evaluations": []},
                {
                    "case_id": "sealed",
                    "passed": False,
                    "verdict": "fail",
                    "output": "leaked",
                    "evaluations": [
                        {"evaluator_id": "policy", "status": "fail", "score": 0.0, "reason": "HOLDOUT SECRET"}
                    ],
                },
            ],
        )
        (self.case_dir / "prompts").mkdir()
        (self.case_dir / "prompts" / "system.md").write_text("You are a support agent.\n", encoding="utf-8")
        self._set_optimization({"components": [{"id": "system-prompt", "path": "prompts/system.md"}], "max_candidates": 2})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _set_optimization(self, config: dict | None) -> None:
        suite = read_json(self.case_dir / "evaluation.json")
        if config is None:
            suite.pop("optimization", None)
        else:
            suite["optimization"] = config
        write_json(self.case_dir / "evaluation.json", suite)

    def _run(self, script: Path = RULE_OPTIMIZER, **kwargs):
        return optimize(self.case_dir, [sys.executable, str(script)], **kwargs)

    def test_writes_candidate_files_and_a_proposal(self) -> None:
        proposal = self._run()
        self.assertTrue(proposal["candidates"])
        for candidate in proposal["candidates"]:
            for relative in candidate["files"].values():
                written = self.case_dir / relative
                self.assertTrue(written.is_file())
                self.assertIn("You are a support agent.", written.read_text(encoding="utf-8"))
        self.assertTrue((self.case_dir / "optimization" / "proposal.json").is_file())

    def test_the_holdout_never_reaches_the_optimizer(self) -> None:
        echo = FIXTURES / "echo_optimizer.py"
        proposal = self._run(script=echo)
        received = json.loads((self.case_dir / "optimization" / "candidates" / "echo" / "system-prompt.txt").read_text(encoding="utf-8"))
        case_ids = {item["case_id"] for item in received["feedback"]["items"]}
        self.assertEqual(case_ids, {"visible", "guarded"})
        self.assertNotIn("HOLDOUT SECRET", json.dumps(received))
        self.assertEqual(received["feedback"]["summary"]["withheld_holdout_cases"], 1)
        self.assertNotEqual(received["feedback"]["dataset_fingerprint"], proposal["dataset_fingerprint"])
        # The proposal still records that a holdout existed and was sealed.
        self.assertEqual(proposal["holdout"]["count"], 1)

    def test_every_optimizer_run_is_recorded_in_the_ledger(self) -> None:
        self._run()
        self._run(overwrite=True)
        lines = (self.case_dir / "optimizer-log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["kind"], "optimize")
        self.assertEqual(json.loads(lines[1])["export_number"], 2)

    def test_refuses_to_overwrite_an_existing_proposal(self) -> None:
        self._run()
        with self.assertRaises(DataError) as raised:
            self._run()
        self.assertIn("--overwrite", str(raised.exception))

    def test_requires_an_optimization_section(self) -> None:
        self._set_optimization(None)
        with self.assertRaises(DataError) as raised:
            self._run()
        self.assertIn("no 'optimization' section", str(raised.exception))

    def test_rejects_a_component_path_outside_the_case(self) -> None:
        self._set_optimization({"components": [{"id": "escape", "path": "../../outside.md"}]})
        with self.assertRaises(DataError) as raised:
            self._run()
        self.assertIn("escapes the case directory", str(raised.exception))

    def test_rejects_an_unknown_component(self) -> None:
        with self.assertRaises(DataError) as raised:
            self._run(script=FIXTURES / "rogue_optimizer.py")
        self.assertIn("unknown component", str(raised.exception))

    def test_rejects_more_candidates_than_the_budget(self) -> None:
        self._set_optimization({"components": [{"id": "system-prompt", "path": "prompts/system.md"}], "max_candidates": 1})
        with self.assertRaises(DataError) as raised:
            self._run(script=FIXTURES / "greedy_optimizer.py")
        self.assertIn("budget is 1", str(raised.exception))

    def test_reports_optimizer_failure_and_bad_output(self) -> None:
        with self.assertRaises(DataError) as failing:
            self._run(script=FIXTURES / "failing_optimizer.py")
        self.assertIn("Optimizer failed", str(failing.exception))
        with self.assertRaises(DataError) as garbage:
            self._run(script=FIXTURES / "garbage_optimizer.py")
        self.assertIn("not one JSON object", str(garbage.exception))

    def test_config_validation(self) -> None:
        validate_optimization_config({"components": [{"id": "a", "path": "p.md"}]})
        for invalid in (
            {"components": []},
            {"components": [{"id": "a b", "path": "p.md"}]},
            {"components": [{"id": "a", "path": "p.md"}, {"id": "a", "path": "q.md"}]},
            {"components": [{"id": "a"}]},
            {"components": [{"id": "a", "path": "p.md"}], "max_candidates": 0},
            {"components": [{"id": "a", "path": "p.md"}], "unknown": 1},
        ):
            with self.assertRaises(DataError):
                validate_optimization_config(invalid)

    def test_cli_runs_the_loop(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "aik.py"),
                "optimize",
                str(self.case_dir),
                "--",
                sys.executable,
                str(RULE_OPTIMIZER),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("candidates=", completed.stdout)
        self.assertIn("proposal=", completed.stdout)


if __name__ == "__main__":
    unittest.main()
