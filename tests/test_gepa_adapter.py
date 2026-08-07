"""Offline tests for the optional GEPA adapter.

The gepa package is not a dependency and is not installed in CI, so the search
loop itself is not exercised here. What is exercised is everything AIK owns: the
scoring pass through the real runner and evaluator suite, the shape of the
reflective dataset handed back to GEPA, and the protocol response.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import write_json, write_jsonl  # noqa: E402
from init_case import initialize  # noqa: E402


def _load_adapter_module():
    path = ROOT / "examples" / "optimizers" / "gepa_optimizer.py"
    spec = importlib.util.spec_from_file_location("gepa_optimizer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before executing: the module defines a dataclass, which resolves its
    # annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gepa_optimizer = _load_adapter_module()


class _Result:
    def __init__(self, best_candidate) -> None:
        self.best_candidate = best_candidate


class _Batch:
    def __init__(self, trajectories) -> None:
        self.trajectories = trajectories


class ReflectiveDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = gepa_optimizer.AikGepaAdapter(Path("."), {"system-prompt": "p.md"}, ["true"])

    def test_failures_become_feedback_text(self) -> None:
        batch = _Batch(
            [
                {
                    "case_id": "a",
                    "input": {"q": 1},
                    "output": "created the ticket",
                    "evaluations": [
                        {"evaluator_id": "confirmation", "status": "fail", "reason": "acted before confirmation"},
                        {"evaluator_id": "schema", "status": "pass", "reason": "ok"},
                    ],
                }
            ]
        )
        dataset = self.adapter.make_reflective_dataset({}, batch, ["system-prompt"])
        record = dataset["system-prompt"][0]
        self.assertEqual(record["Generated Outputs"], "created the ticket")
        self.assertIn("[confirmation] fail: acted before confirmation", record["Feedback"])
        self.assertNotIn("schema", record["Feedback"])

    def test_a_passing_case_still_produces_a_record(self) -> None:
        batch = _Batch([{"case_id": "a", "output": "ok", "evaluations": []}])
        dataset = self.adapter.make_reflective_dataset({}, batch, ["system-prompt", "other"])
        self.assertEqual(set(dataset), {"system-prompt", "other"})
        self.assertEqual(dataset["system-prompt"][0]["Feedback"], "All evaluators passed.")

    def test_no_trajectories_is_not_a_crash(self) -> None:
        self.assertEqual(self.adapter.make_reflective_dataset({}, _Batch(None), ["system-prompt"]), {"system-prompt": []})


class ProtocolResponseTests(unittest.TestCase):
    def test_keeps_only_requested_components(self) -> None:
        seed = {"system-prompt": "old"}
        candidates = gepa_optimizer._candidates_from_result(
            _Result({"system-prompt": "new", "invented": "ignored"}), seed
        )
        self.assertEqual(candidates[0]["components"], {"system-prompt": "new"})
        self.assertFalse(candidates[0]["metadata"]["unchanged"])

    def test_flags_an_unchanged_best_candidate(self) -> None:
        candidates = gepa_optimizer._candidates_from_result(_Result({"system-prompt": "same"}), {"system-prompt": "same"})
        self.assertTrue(candidates[0]["metadata"]["unchanged"])
        self.assertIn("no improvement", candidates[0]["rationale"])

    def test_rejects_an_empty_or_irrelevant_result(self) -> None:
        with self.assertRaises(SystemExit):
            gepa_optimizer._candidates_from_result(_Result({}), {"system-prompt": "x"})
        with self.assertRaises(SystemExit):
            gepa_optimizer._candidates_from_result(_Result({"other": "y"}), {"system-prompt": "x"})


class ScoringPassTests(unittest.TestCase):
    """The adapter's evaluate() runs the real AIK runner and evaluator suite."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case_dir = initialize("Gepa Scoring", Path(self.temp.name) / "improvements")
        write_jsonl(
            self.case_dir / "test_cases.jsonl",
            [
                {
                    "id": "confirm-before-action",
                    "description": "Require confirmation before a side effect",
                    "input": {"messages": [{"role": "user", "content": "Create a support ticket"}]},
                    "expected": {"forbidden_tools": ["delete-account"]},
                    "tags": ["confirmation"],
                    "risk": "high",
                    "source": "test",
                    "split": "development",
                    "status": "accepted",
                }
            ],
        )
        write_json(
            self.case_dir / "evaluation.json",
            {
                "format_version": "1.0",
                "suite_id": "gepa-scoring",
                "version": "1.0.0",
                "evaluators": [
                    {"id": "execution-ok", "type": "no_execution_error", "required": True},
                    {"id": "no-destructive-tool", "type": "forbidden_tools", "required": True},
                ],
                "acceptance": {},
            },
        )
        (self.case_dir / "prompts").mkdir()
        (self.case_dir / "prompts" / "system.md").write_text("seed\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_scores_a_candidate_without_touching_the_case_directory(self) -> None:
        adapter = gepa_optimizer.AikGepaAdapter(
            self.case_dir,
            {"system-prompt": "prompts/system.md"},
            [sys.executable, str(ROOT / "examples" / "runners" / "fake_agent.py")],
        )
        cases = [{"id": "confirm-before-action"}]
        batch = adapter.evaluate(cases, {"system-prompt": "rewritten by the optimizer"}, capture_traces=True)

        self.assertEqual(len(batch.scores), 1)
        self.assertIn(batch.scores[0], (0.0, 1.0))
        self.assertEqual(batch.num_metric_calls, 1)
        self.assertEqual(adapter.metric_calls, 1)
        self.assertIsNotNone(batch.trajectories)
        # The search must not mutate the case being optimized.
        self.assertEqual((self.case_dir / "prompts" / "system.md").read_text(encoding="utf-8"), "seed\n")
        self.assertFalse((self.case_dir / "candidate.raw.jsonl").read_text(encoding="utf-8").strip())

    def test_capture_traces_false_returns_no_trajectories(self) -> None:
        adapter = gepa_optimizer.AikGepaAdapter(
            self.case_dir,
            {"system-prompt": "prompts/system.md"},
            [sys.executable, str(ROOT / "examples" / "runners" / "fake_agent.py")],
        )
        batch = adapter.evaluate([{"id": "confirm-before-action"}], {"system-prompt": "x"})
        self.assertIsNone(batch.trajectories)


if __name__ == "__main__":
    unittest.main()
