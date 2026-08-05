from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "improve-agent" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import observation_id_for, read_json, write_json, write_jsonl  # noqa: E402
from evaluate_results import evaluate, evaluate_trials  # noqa: E402
from init_case import initialize  # noqa: E402
from run_review import run as build_review  # noqa: E402
from dashboard_model import _evaluator_summary, build_dashboard  # noqa: E402
from history import history_init, history_promote, history_record  # noqa: E402
from reliability import run_reliability  # noqa: E402
from render_dashboard import render_dashboard  # noqa: E402


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


class DashboardTestBase(unittest.TestCase):
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
        decision: str = "accepted",
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


class DashboardModelTests(DashboardTestBase):
    def test_empty_history_renders_clearly(self) -> None:
        history_root = self.root / "aik-history"
        dashboard = build_dashboard(history_root)
        self.assertTrue(dashboard["integrity"]["valid"])
        self.assertIsNone(dashboard["state"]["anchor_run_id"])
        self.assertIsNone(dashboard["kpis"]["champion_level"])
        self.assertFalse(dashboard["coverage"]["available"])
        self.assertEqual(dashboard["regressions"]["items"], [])
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("No history has been recorded yet", text)
        self.assertIn("No detected regressions", text)

    def test_dashboard_does_not_embed_absolute_history_path(self) -> None:
        history_root = self.root / "aik-history"
        case_dir = self._prepare_case("portable-root")
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")

        dashboard = build_dashboard(history_root)

        self.assertEqual(dashboard["header"]["history_root"], "aik-history")
        self.assertNotIn(str(self.root), dashboard["header"]["history_root"])

    def test_kpis_and_comparability_within_one_cohort(self) -> None:
        case_dir = self._prepare_case("dash-a")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root)
        self.assertTrue(dashboard["comparability"]["comparable"])
        self.assertAlmostEqual(dashboard["kpis"]["champion_level"], 0.75)
        self.assertAlmostEqual(dashboard["kpis"]["candidate_level"], 1.0)
        self.assertAlmostEqual(dashboard["kpis"]["delta_vs_champion"], 0.25)
        self.assertAlmostEqual(dashboard["kpis"]["delta_vs_anchor"], 0.25)

    def test_delta_is_null_across_incompatible_cohorts(self) -> None:
        history_root = self.root / "aik-history"
        case_a = self._prepare_case("dash-b-a")
        different_cases = [dict(case, id=case["id"] + "-z") for case in CASES]
        renamed_baseline = {"c1-z": ["a"], "c2-z": ["a"], "c3-z": ["a"], "c4-z": []}
        renamed_candidate = {"c1-z": ["a"], "c2-z": ["a"], "c3-z": ["a"], "c4-z": ["a"]}
        case_b = self._prepare_case(
            "dash-b-b", cases=different_cases, baseline_agents=renamed_baseline, candidate_agents=renamed_candidate
        )
        history_init(case_a, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        history_record(case_b, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root)
        self.assertFalse(dashboard["comparability"]["comparable"])
        self.assertIsNone(dashboard["kpis"]["delta_vs_champion"])
        self.assertIsNotNone(dashboard["kpis"]["delta_vs_champion_reason"])
        html_path = render_dashboard(dashboard, self.root / "out2" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("N/A", text)

    def test_slope_is_null_with_fewer_than_two_champion_points(self) -> None:
        case_dir = self._prepare_case("dash-c")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        dashboard = build_dashboard(history_root)
        self.assertIsNone(dashboard["kpis"]["slope"])
        self.assertIsNotNone(dashboard["kpis"]["slope_reason"])

    def test_ols_slope_is_correct_for_three_known_points(self) -> None:
        history_root = self.root / "aik-history"
        case1 = self._prepare_case(
            "dash-d-1",
            baseline_agents={"c1": ["a"], "c2": ["a"], "c3": [], "c4": []},
            candidate_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": []},
        )
        history_init(case1, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate1 = history_record(case1, history_root=history_root, stage="candidate", label="First", agent_version="v2")
        history_promote(candidate1["run_id"], history_root=history_root)

        case2 = self._prepare_case(
            "dash-d-2",
            candidate_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"]},
        )
        candidate2 = history_record(case2, history_root=history_root, stage="candidate", label="Second", agent_version="v3")
        history_promote(candidate2["run_id"], history_root=history_root)

        dashboard = build_dashboard(history_root)
        self.assertIsNotNone(dashboard["kpis"]["slope"])
        self.assertAlmostEqual(dashboard["kpis"]["slope"], 25.0, places=3)

    def test_candidate_excluded_from_accepted_trend_before_promotion(self) -> None:
        case_dir = self._prepare_case("dash-e")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root)
        candidate_points = [point for point in dashboard["trend"] if point["run_id"] == candidate["run_id"]]
        self.assertEqual(len(candidate_points), 1)
        self.assertEqual(candidate_points[0]["kind"], "candidate")
        champion_run_ids = {point["run_id"] for point in dashboard["trend"] if point["kind"] in {"anchor", "champion"}}
        self.assertNotIn(candidate["run_id"], champion_run_ids)

    def test_auto_selection_ignores_candidates_from_previous_champion(self) -> None:
        case_dir = self._prepare_case("dash-stale")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        promoted = history_record(
            case_dir, history_root=history_root, stage="candidate", label="First", agent_version="v2"
        )
        stale = history_record(
            case_dir, history_root=history_root, stage="candidate", label="Recorded in parallel", agent_version="v3"
        )
        history_promote(promoted["run_id"], history_root=history_root)

        dashboard = build_dashboard(history_root)
        self.assertNotEqual(dashboard["state"]["selected_candidate_run_id"], stale["run_id"])
        self.assertIsNone(dashboard["state"]["selected_candidate_run_id"])

    def test_explicit_stale_candidate_is_not_comparable(self) -> None:
        case_dir = self._prepare_case("dash-explicit-stale")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        promoted = history_record(case_dir, history_root=history_root, stage="candidate", label="First", agent_version="v2")
        stale = history_record(case_dir, history_root=history_root, stage="candidate", label="Parallel", agent_version="v3")
        history_promote(promoted["run_id"], history_root=history_root)

        dashboard = build_dashboard(history_root, candidate_run_id=stale["run_id"])
        self.assertFalse(dashboard["comparability"]["comparable"])
        self.assertIn("stale", dashboard["comparability"]["reasons"][0])
        self.assertTrue(all(row["delta"] is None for row in dashboard["evaluators"]))

    def test_evaluator_means_use_only_numeric_scores(self) -> None:
        evaluators = [
            {"id": "execution-ok", "type": "no_execution_error", "required": True},
            {"id": "required-agents", "type": "required_agents", "required": True},
            {"id": "task-completion", "type": "llm_rubric", "required": False, "threshold": 0.7},
        ]
        cases = [dict(case, expected={**case["expected"], "rubric": ["Looks good"]}) for case in CASES]
        case_dir = self._prepare_case("dash-f", cases=cases, evaluators=evaluators)
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root)
        by_id = {row["evaluator_id"]: row for row in dashboard["evaluators"]}
        self.assertAlmostEqual(by_id["required-agents"]["champion_score"], 0.75)
        self.assertAlmostEqual(by_id["required-agents"]["candidate_score"], 1.0)
        self.assertIsNone(by_id["task-completion"]["champion_score"])
        self.assertIsNone(by_id["task-completion"]["candidate_score"])
        self.assertEqual(by_id["task-completion"]["cases_scored"], 0)

    def test_evaluator_versions_are_separate_series(self) -> None:
        summary = _evaluator_summary(
            [
                {
                    "case_id": "c1",
                    "evaluations": [
                        {"evaluator_id": "quality", "evaluator_version": "1", "status": "pass", "score": 0.8},
                        {"evaluator_id": "quality", "evaluator_version": "2", "status": "pass", "score": 0.9},
                    ],
                }
            ]
        )
        self.assertEqual(set(summary), {("quality", "1"), ("quality", "2")})

    def test_regression_grouping_by_risk_and_tag(self) -> None:
        case_dir = self._prepare_case(
            "dash-g",
            baseline_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"]},
            candidate_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": []},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root, candidate_run_id=candidate["run_id"])
        self.assertEqual(dashboard["regressions"]["by_risk"], {"high": 1})
        self.assertIn("critical-path", dashboard["regressions"]["by_tag"])
        self.assertEqual(dashboard["regressions"]["items"][0]["case_id"], "c4")
        self.assertIn("required-agents", dashboard["regressions"]["items"][0]["failing_evaluators"])

    def test_coverage_excludes_non_accepted_cases(self) -> None:
        cases = [
            {**CASES[0]},
            {**CASES[1]},
            {**CASES[2]},
            {**CASES[3], "status": "proposed"},
            {**CASES[3], "id": "c5", "status": "retired"},
        ]
        raw = {"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"], "c5": ["a"]}
        case_dir = self._prepare_case("dash-h", cases=cases, baseline_agents=raw, candidate_agents=raw)
        history_root = self.root / "aik-history"
        anchor = history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        dashboard = build_dashboard(history_root, candidate_run_id=anchor["run_id"])
        self.assertEqual(dashboard["coverage"]["total_cases"], 3)


class DashboardRenderingSecurityTests(DashboardTestBase):
    def test_render_escapes_malicious_label_and_tags(self) -> None:
        malicious_cases = [
            dict(CASES[0], description='<img src=x onerror=alert(1)>', tags=['<script>evil()</script>']),
            *CASES[1:],
        ]
        case_dir = self._prepare_case(
            "dash-i",
            cases=malicious_cases,
            baseline_agents={"c1": ["a"], "c2": ["a"], "c3": ["a"], "c4": ["a"]},
            candidate_agents={"c1": [], "c2": ["a"], "c3": ["a"], "c4": ["a"]},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label='<script>alert(2)</script>', agent_version="v1")
        candidate = history_record(
            case_dir, history_root=history_root, stage="candidate", label='"><svg onload=alert(3)>', agent_version="v2"
        )
        dashboard = build_dashboard(history_root, candidate_run_id=candidate["run_id"])
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(2)</script>", text)
        self.assertNotIn("<script>evil()</script>", text)
        self.assertNotIn("<img src=x onerror=alert(1)>", text)
        self.assertNotIn("<svg onload=alert(3)>", text)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", text)
        self.assertIn("&lt;script&gt;evil()&lt;/script&gt;", text)

    def test_render_has_no_remote_assets(self) -> None:
        dashboard = build_dashboard(self.root / "aik-history")
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("<script", text)

    def test_svg_has_accessible_title_desc_and_fallback_table(self) -> None:
        case_dir = self._prepare_case("dash-j")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root)
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn('<title id="trend-title">', text)
        self.assertIn('<desc id="trend-desc">', text)
        self.assertIn('<details class="fallback">', text)
        self.assertIn("v1 · deterministic", text)

    def test_review_and_comparison_links_are_relative(self) -> None:
        case_dir = self._prepare_case("dash-k")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root)
        for row in dashboard["timeline"]:
            if row["review_link"]:
                self.assertTrue(row["review_link"].startswith("runs/"))
                self.assertNotIn("http://", row["review_link"])
            if row["comparison_link"]:
                self.assertTrue(row["comparison_link"].startswith("runs/"))

    def test_no_candidate_state_renders_clearly(self) -> None:
        case_dir = self._prepare_case("dash-l")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        dashboard = build_dashboard(history_root)
        self.assertIsNone(dashboard["state"]["selected_candidate_run_id"])
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("No candidate is selected", text)

    def test_no_regression_state_renders_clearly(self) -> None:
        case_dir = self._prepare_case("dash-m")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root, candidate_run_id=candidate["run_id"])
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("No detected regressions", text)


def _raw_trials(agents_by_case: dict[str, list[list[str]]]) -> list[dict]:
    records = []
    for case_id, per_repetition_agents in agents_by_case.items():
        for repetition, agents in enumerate(per_repetition_agents):
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


class DashboardReliabilityTests(DashboardTestBase):
    def _prepare_case_with_reliability(
        self,
        name: str,
        *,
        baseline_trial_agents: dict[str, list[list[str]]],
        candidate_trial_agents: dict[str, list[list[str]]],
        reliability_config: dict | None = None,
    ) -> Path:
        case_dir = self._prepare_case(name)
        write_jsonl(case_dir / "baseline.trials.raw.jsonl", _raw_trials(baseline_trial_agents))
        write_jsonl(case_dir / "candidate.trials.raw.jsonl", _raw_trials(candidate_trial_agents))
        evaluate_trials(case_dir, "baseline", overwrite=True)
        evaluate_trials(case_dir, "candidate", overwrite=True)
        suite = read_json(case_dir / "evaluation.json")
        suite["reliability"] = reliability_config or {
            "minimum_repetitions": 3,
            "confidence_level": 0.95,
            "bootstrap_samples": 500,
            "minimum_probability_of_improvement": 0.9,
            "maximum_flaky_case_rate": 0.5,
        }
        write_json(case_dir / "evaluation.json", suite)
        reliability = run_reliability(case_dir)
        write_json(case_dir / "reliability.json", reliability)
        build_review(case_dir)
        return case_dir

    def test_no_trials_shows_one_run_only(self) -> None:
        case_dir = self._prepare_case("dash-rel-a")
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root, candidate_run_id=candidate["run_id"])
        self.assertFalse(dashboard["reliability"]["available"])
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("One run only", text)

    def test_archived_trials_populate_reliability_panel(self) -> None:
        case_dir = self._prepare_case_with_reliability(
            "dash-rel-b",
            baseline_trial_agents={"c1": [["a"], ["a"], [], ["a"], ["a"]], "c2": [["a"]] * 5, "c3": [["a"]] * 5, "c4": [[]] * 5},
            candidate_trial_agents={"c1": [["a"]] * 5, "c2": [["a"]] * 5, "c3": [["a"]] * 5, "c4": [["a"]] * 5},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        dashboard = build_dashboard(history_root, candidate_run_id=candidate["run_id"])
        self.assertTrue(dashboard["reliability"]["available"])
        self.assertIn({"case_id": "c1", "risk": "medium"}, dashboard["reliability"]["flaky_cases"])
        self.assertIsNotNone(dashboard["kpis"]["probability_of_improvement"])

        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertIn("Flaky cases", text)
        self.assertIn(">c1<", text)
        self.assertIn("P(candidate&gt;baseline)", text)

    def test_malicious_evaluator_id_in_reliability_is_escaped(self) -> None:
        case_dir = self._prepare_case_with_reliability(
            "dash-rel-c",
            baseline_trial_agents={"c1": [["a"], []], "c2": [["a"]] * 2, "c3": [["a"]] * 2, "c4": [[]] * 2},
            candidate_trial_agents={"c1": [["a"]] * 2, "c2": [["a"]] * 2, "c3": [["a"]] * 2, "c4": [["a"]] * 2},
        )
        history_root = self.root / "aik-history"
        history_init(case_dir, history_root=history_root, stage="baseline", label="Anchor", agent_version="v1")
        candidate = history_record(case_dir, history_root=history_root, stage="candidate", label="Candidate", agent_version="v2")
        reliability_path = history_root / "runs" / candidate["run_id"] / "reliability.json"
        reliability = read_json(reliability_path)
        reliability["comparison"]["incompatible_evaluators"] = [
            {"evaluator_id": "<script>alert(1)</script>", "baseline_versions": ["1"], "candidate_versions": ["2"]}
        ]
        write_json(reliability_path, reliability)
        dashboard = build_dashboard(history_root, candidate_run_id=candidate["run_id"])
        html_path = render_dashboard(dashboard, self.root / "out" / "dashboard.html")
        text = html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)


if __name__ == "__main__":
    unittest.main()
