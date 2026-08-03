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

from common import read_jsonl, write_json, write_jsonl  # noqa: E402
from compare_runs import compare  # noqa: E402
from execute_cases import execute  # noqa: E402
from init_case import initialize  # noqa: E402
from promote_tests import promote  # noqa: E402
from render_review import render  # noqa: E402
from validate_case import validate  # noqa: E402


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_case(self) -> Path:
        case_dir = initialize("Example Failure", self.root / "improvements")
        write_jsonl(
            case_dir / "test_cases.jsonl",
            [
                {"id": "a", "description": "A", "input": {}, "expected": {}, "tags": ["x"]},
                {"id": "b", "description": "B", "input": {}, "expected": {}, "tags": ["x"]},
                {"id": "c", "description": "C", "input": {}, "expected": {}, "tags": ["x"]},
            ],
        )
        write_jsonl(
            case_dir / "baseline.jsonl",
            [
                {"case_id": "a", "passed": False, "output": "old <unsafe>", "scores": {"q": 0.1}},
                {"case_id": "b", "passed": True, "output": "old", "scores": {"q": 1.0}},
                {"case_id": "c", "passed": True, "output": "same"},
            ],
        )
        write_jsonl(
            case_dir / "candidate.jsonl",
            [
                {"case_id": "a", "passed": True, "output": "new", "scores": {"q": 0.8}},
                {"case_id": "b", "passed": False, "output": "new", "scores": {"q": 0.2}},
                {"case_id": "c", "passed": True, "output": "same"},
            ],
        )
        return case_dir

    def _write_execution_case(self, modes: list[str] | None = None) -> Path:
        case_dir = initialize("Execution Case", self.root / "improvements")
        chosen_modes = modes or ["normal", "normal"]
        write_jsonl(
            case_dir / "test_cases.jsonl",
            [
                {
                    "id": f"run-{index}",
                    "description": f"Runner case {index}",
                    "input": {"mode": mode},
                    "expected": {},
                    "tags": ["runner", "even" if index % 2 == 0 else "odd"],
                }
                for index, mode in enumerate(chosen_modes, start=1)
            ],
        )
        return case_dir

    def test_initialize_refuses_overwrite(self) -> None:
        initialize("Do Not Overwrite", self.root)
        with self.assertRaises(FileExistsError):
            initialize("Do Not Overwrite", self.root)

    def test_compare_tracks_all_statuses(self) -> None:
        case_dir = self._write_case()
        result = compare(case_dir / "test_cases.jsonl", case_dir / "baseline.jsonl", case_dir / "candidate.jsonl")
        self.assertEqual(result["summary"]["improved"], 1)
        self.assertEqual(result["summary"]["regressed"], 1)
        self.assertEqual(result["summary"]["unchanged"], 1)
        self.assertEqual(result["summary"]["not_comparable"], 0)
        improved = next(item for item in result["items"] if item["case_id"] == "a")
        self.assertAlmostEqual(improved["score_deltas"]["q"], 0.7)

    def test_missing_candidate_is_not_comparable(self) -> None:
        case_dir = self._write_case()
        candidate = read_jsonl(case_dir / "candidate.jsonl", "case_id")
        write_jsonl(case_dir / "candidate.jsonl", [item for item in candidate if item["case_id"] != "b"])

        result = compare(case_dir / "test_cases.jsonl", case_dir / "baseline.jsonl", case_dir / "candidate.jsonl")

        self.assertEqual(result["summary"]["not_comparable"], 1)
        missing = next(item for item in result["items"] if item["case_id"] == "b")
        self.assertEqual(missing["status"], "not_comparable")

    def test_render_is_human_readable_and_escapes_output(self) -> None:
        case_dir = self._write_case()
        result = compare(case_dir / "test_cases.jsonl", case_dir / "baseline.jsonl", case_dir / "candidate.jsonl")
        comparison = write_json(case_dir / "comparison.json", result)
        report = render(comparison, case_dir / "case.yaml", case_dir / "review.html")
        text = report.read_text(encoding="utf-8")
        self.assertIn("Behavioral Change Review", text)
        self.assertIn("REJECT OR FIX", text)
        self.assertIn("old &lt;unsafe&gt;", text)
        self.assertNotIn("old <unsafe>", text)

    def test_validate_reports_complete_case(self) -> None:
        case_dir = self._write_case()
        errors, warnings = validate(case_dir)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_promotion_requires_acceptance_and_promotes_improved(self) -> None:
        case_dir = self._write_case()
        result = compare(case_dir / "test_cases.jsonl", case_dir / "baseline.jsonl", case_dir / "candidate.jsonl")
        write_json(case_dir / "comparison.json", result)
        destination = self.root / "datasets" / "regression.jsonl"
        with self.assertRaises(ValueError):
            promote(case_dir, destination, {"improved"})
        (case_dir / "decision.yaml").write_text("status: accepted\n", encoding="utf-8")
        promoted = promote(case_dir, destination, {"improved"})
        self.assertEqual(promoted, ["a"])
        records = read_jsonl(destination, "id")
        self.assertEqual([record["id"] for record in records], ["a"])

    def test_root_cli_runs_review_with_portable_paths(self) -> None:
        case_dir = self._write_case()
        completed = subprocess.run(
            [sys.executable, str(ROOT / "aik.py"), "review", str(case_dir)],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("improved=1 regressed=1 unchanged=1 not_comparable=0", completed.stdout)
        comparison = json.loads((case_dir / "comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(comparison["inputs"]["baseline"], "baseline.jsonl")

    def test_root_cli_returns_nonzero_for_invalid_case(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "aik.py"), "validate", str(self.root / "missing")],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("ERROR:", completed.stdout)

    def test_command_runner_generates_both_stages_and_resumes(self) -> None:
        case_dir = self._write_execution_case()
        runner = [sys.executable, str(ROOT / "tests" / "fixtures" / "protocol_runner.py")]

        baseline = execute(case_dir, "baseline", runner)
        self.assertEqual(baseline["executed"], 2)
        self.assertEqual([item["passed"] for item in read_jsonl(case_dir / "baseline.jsonl", "case_id")], [False, False])

        first = execute(case_dir, "candidate", runner, selected_ids={"run-1"})
        self.assertEqual(first["executed"], 1)
        resumed = execute(case_dir, "candidate", runner, resume=True)
        self.assertEqual(resumed["executed"], 1)
        self.assertEqual(resumed["skipped"], 1)
        candidate = read_jsonl(case_dir / "candidate.jsonl", "case_id")
        self.assertEqual([item["case_id"] for item in candidate], ["run-1", "run-2"])
        self.assertTrue(all(item["passed"] for item in candidate))

    def test_command_runner_captures_protocol_errors_and_timeouts(self) -> None:
        case_dir = self._write_execution_case(["invalid-json", "exit-error", "timeout"])
        runner = [sys.executable, str(ROOT / "tests" / "fixtures" / "protocol_runner.py")]

        summary = execute(case_dir, "baseline", runner, timeout_seconds=0.5)

        self.assertEqual(summary["execution_errors"], 3)
        records = read_jsonl(case_dir / "baseline.jsonl", "case_id")
        self.assertEqual(
            {item["error"]["type"] for item in records},
            {"invalid_json", "runner_exit", "timeout"},
        )

    def test_command_runner_refuses_implicit_overwrite(self) -> None:
        case_dir = self._write_execution_case(["normal"])
        runner = [sys.executable, str(ROOT / "tests" / "fixtures" / "protocol_runner.py")]
        execute(case_dir, "baseline", runner)
        with self.assertRaises(ValueError):
            execute(case_dir, "baseline", runner)

    def test_command_runner_filters_by_tag(self) -> None:
        case_dir = self._write_execution_case()
        runner = [sys.executable, str(ROOT / "tests" / "fixtures" / "protocol_runner.py")]
        summary = execute(case_dir, "baseline", runner, selected_tags={"even"})
        self.assertEqual(summary["selected"], 1)
        records = read_jsonl(case_dir / "baseline.jsonl", "case_id")
        self.assertEqual([item["case_id"] for item in records], ["run-2"])

    def test_root_cli_executes_runner_after_separator(self) -> None:
        case_dir = self._write_execution_case(["normal"])
        runner = str(ROOT / "tests" / "fixtures" / "protocol_runner.py")
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "aik.py"),
                "run",
                str(case_dir),
                "--stage",
                "baseline",
                "--",
                sys.executable,
                runner,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("executed=1 skipped=0 execution_errors=0", completed.stdout)

    def test_langgraph_adapter_loads_factory_and_evaluator(self) -> None:
        case_dir = initialize("Adapter Case", self.root / "improvements")
        write_jsonl(
            case_dir / "test_cases.jsonl",
            [{"id": "adapter-1", "description": "Adapter", "input": {"value": 2}, "expected": {"value": 4}, "tags": ["adapter"]}],
        )
        adapter = ROOT / ".agents" / "skills" / "improve-agent" / "scripts" / "langgraph_runner.py"
        command = [
            sys.executable,
            str(adapter),
            "--graph",
            "runnable_module:build_graph",
            "--evaluator",
            "runnable_module:evaluate",
        ]
        summary = execute(
            case_dir,
            "baseline",
            command,
            runner_cwd=ROOT / "tests" / "fixtures",
        )
        self.assertEqual(summary["execution_errors"], 0)
        result = read_jsonl(case_dir / "baseline.jsonl", "case_id")[0]
        self.assertTrue(result["passed"])
        self.assertEqual(result["metadata"]["stage"], "baseline")


if __name__ == "__main__":
    unittest.main()
