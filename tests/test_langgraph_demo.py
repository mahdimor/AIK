from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "langgraph-multi-agent"
HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None and importlib.util.find_spec("langchain") is not None


@unittest.skipUnless(HAS_LANGGRAPH, "optional LangGraph dependencies are not installed")
class LangGraphDemoTests(unittest.TestCase):
    def test_mock_demo_detects_and_fixes_mixed_routing(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DEMO / "run_demo.py"), "--mode", "mock"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        comparison = json.loads((DEMO / "case" / "comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(
            comparison["summary"],
            {"improved": 1, "regressed": 0, "unchanged": 3, "not_comparable": 0, "total": 4},
        )

    def test_openai_graph_builds_without_calling_the_api(self) -> None:
        environment = os.environ.copy()
        environment["AIK_DEMO_MODE"] = "openai"
        environment["OPENAI_API_KEY"] = "test-key-not-used"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from multi_agent_demo import build_graph; graph = build_graph('candidate'); assert hasattr(graph, 'invoke')",
            ],
            cwd=DEMO,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
