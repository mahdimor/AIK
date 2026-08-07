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
from judge import judge, judge_case, render_yaml, validate_judge_config  # noqa: E402
from validate_case import validate  # noqa: E402


CONFIG = {
    "id": "refund-policy-judge",
    "domain": "commerce.refunds",
    "version": "0.1.0",
    "criteria": {
        "policy_compliance": {"evaluators": ["delay-window"], "weight": 0.5},
        "evidence_quality": {"evaluators": ["proof-of-damage"], "weight": 0.3},
        "consistency": {"evaluators": ["rationale-matches"], "weight": 0.2},
    },
    "hard_rules": ["amount-ceiling"],
    "abstention": {"enabled": True, "minimum_confidence": 0.7},
    "verdicts": {"approve_at": 0.85, "escalate_at": 0.5},
    "recommended_actions": {"evidence_quality": "request_missing_evidence"},
}

CASE = {"id": "dossier-1", "risk": "high", "split": "development"}


def _evaluation(evaluator_id: str, status: str, score: float | None = None, reason: str = "", evidence=None) -> dict:
    item = {"evaluator_id": evaluator_id, "status": status, "reason": reason, "kind": "deterministic"}
    if score is not None:
        item["score"] = score
    if evidence is not None:
        item["evidence"] = evidence
    return item


def _result(*evaluations: dict) -> dict:
    return {"case_id": "dossier-1", "evaluations": list(evaluations)}


class VerdictTests(unittest.TestCase):
    def test_all_criteria_pass_approves(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("proof-of-damage", "pass"),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["verdict"], "approve")
        self.assertEqual(judgement["score"], 1.0)
        self.assertEqual(judgement["confidence"], 1.0)
        self.assertEqual(judgement["recommended_action"], "proceed")
        self.assertEqual(judgement["critical_violations"], [])

    def test_a_hard_rule_violation_rejects_whatever_the_score(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("proof-of-damage", "pass"),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "fail", reason="1200 USD exceeds the 500 USD authority"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["verdict"], "reject")
        self.assertEqual(judgement["score"], 1.0)  # every criterion still passed
        self.assertEqual(judgement["critical_violations"][0]["evaluator_id"], "amount-ceiling")
        self.assertIn("exceeds", judgement["critical_violations"][0]["reason"])

    def test_an_unchecked_hard_rule_forces_abstention(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("proof-of-damage", "pass"),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "error", reason="ledger unavailable"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["verdict"], "insufficient_evidence")
        self.assertTrue(judgement["abstained"])
        self.assertIn("amount-ceiling", judgement["abstention_reasons"][0])

    def test_a_middling_score_escalates_and_uses_the_mapped_action(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("proof-of-damage", "fail", reason="no proof attached"),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["verdict"], "escalate")
        self.assertEqual(judgement["score"], 0.7)
        self.assertEqual(judgement["criteria"]["evidence_quality"]["status"], "fail")
        self.assertEqual(judgement["recommended_action"], "request_missing_evidence")

    def test_low_coverage_abstains_instead_of_guessing(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["confidence"], 0.5)
        self.assertEqual(judgement["verdict"], "insufficient_evidence")
        self.assertIn("below the required", judgement["abstention_reasons"][0])
        self.assertEqual(len(judgement["unmeasured"]), 2)

    def test_confidence_is_coverage_not_correctness(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "fail"),
                _evaluation("proof-of-damage", "fail"),
                _evaluation("rationale-matches", "fail"),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        # Everything failed, yet everything was measured: full confidence, worst score.
        self.assertEqual(judgement["confidence"], 1.0)
        self.assertEqual(judgement["score"], 0.0)
        self.assertEqual(judgement["verdict"], "reject")
        self.assertIn("share of criterion weight", judgement["confidence_basis"])

    def test_mixed_evaluators_make_a_criterion_partial(self) -> None:
        config = json.loads(json.dumps(CONFIG))
        config["criteria"]["evidence_quality"]["evaluators"] = ["proof-of-damage", "receipt-present"]
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("proof-of-damage", "pass"),
                _evaluation("receipt-present", "fail"),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "pass"),
            ),
            config,
        )
        self.assertEqual(judgement["criteria"]["evidence_quality"]["status"], "partial")
        self.assertEqual(judgement["criteria"]["evidence_quality"]["score"], 0.5)

    def test_not_applicable_is_not_a_failure(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass"),
                _evaluation("proof-of-damage", "not_applicable"),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["criteria"]["evidence_quality"]["status"], "unknown")
        self.assertEqual(judgement["score"], 1.0)  # measured criteria all passed
        self.assertEqual(judgement["confidence"], 0.7)
        self.assertEqual(judgement["verdict"], "approve")

    def test_semantic_scores_are_used_when_present(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass", score=0.9),
                _evaluation("proof-of-damage", "pass", score=0.8),
                _evaluation("rationale-matches", "pass", score=0.6),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        self.assertEqual(judgement["score"], round(0.5 * 0.9 + 0.3 * 0.8 + 0.2 * 0.6, 6))
        self.assertEqual(judgement["verdict"], "escalate")

    def test_evidence_is_cited(self) -> None:
        judgement = judge_case(
            CASE,
            _result(
                _evaluation("delay-window", "pass", evidence={"ordered_at": "2026-07-01"}),
                _evaluation("proof-of-damage", "fail", reason="missing", evidence={"attachments": []}),
                _evaluation("rationale-matches", "pass"),
                _evaluation("amount-ceiling", "pass"),
            ),
            CONFIG,
        )
        cited = {item["evaluator_id"] for item in judgement["evidence_citations"]}
        self.assertEqual(cited, {"delay-window", "proof-of-damage"})


class ConfigTests(unittest.TestCase):
    def test_accepts_a_valid_config(self) -> None:
        validate_judge_config(CONFIG, {"delay-window", "proof-of-damage", "rationale-matches", "amount-ceiling"})

    def test_rejects_evaluators_missing_from_the_suite(self) -> None:
        with self.assertRaises(DataError) as raised:
            validate_judge_config(CONFIG, {"delay-window"})
        self.assertIn("not in the suite", str(raised.exception))

    def test_rejects_malformed_configs(self) -> None:
        for invalid in (
            {**CONFIG, "id": "not valid!"},
            {**CONFIG, "version": ""},
            {**CONFIG, "criteria": {}},
            {**CONFIG, "criteria": {"a": {"evaluators": [], "weight": 1}}},
            {**CONFIG, "criteria": {"a": {"evaluators": ["x"], "weight": 0}}},
            {**CONFIG, "verdicts": {"approve_at": 0.4, "escalate_at": 0.9}},
            {**CONFIG, "verdicts": {"approve_at": 2}},
            {**CONFIG, "abstention": {"minimum_confidence": 5}},
            {**CONFIG, "recommended_actions": {"nope": "x"}},
            {**CONFIG, "hard_rules": "amount-ceiling"},
            {**CONFIG, "surprise": 1},
        ):
            with self.assertRaises(DataError):
                validate_judge_config(invalid)


class JudgeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.case_dir = initialize("Refund Pack", Path(self.temp.name) / "improvements")
        write_jsonl(
            self.case_dir / "test_cases.jsonl",
            [
                {
                    "id": name,
                    "description": name,
                    "input": {"c": name},
                    "expected": {"goal": name},
                    "tags": ["policy"],
                    "risk": "high",
                    "source": "production",
                    "split": "development",
                    "status": "accepted",
                }
                for name in ("dossier-1", "dossier-2")
            ],
        )
        suite = read_json(self.case_dir / "evaluation.json")
        suite["evaluators"] = [
            {"id": evaluator, "type": "output_contains", "required": True}
            for evaluator in ("delay-window", "proof-of-damage", "rationale-matches", "amount-ceiling")
        ]
        suite["judge"] = CONFIG
        write_json(self.case_dir / "evaluation.json", suite)
        write_jsonl(
            self.case_dir / "candidate.evaluated.jsonl",
            [
                {
                    "case_id": "dossier-1",
                    "passed": True,
                    "evaluations": [
                        _evaluation("delay-window", "pass"),
                        _evaluation("proof-of-damage", "pass"),
                        _evaluation("rationale-matches", "pass"),
                        _evaluation("amount-ceiling", "pass"),
                    ],
                    "evaluation_metadata": {"stage": "candidate", "dataset_fingerprint": "sha256:abc"},
                },
                {
                    "case_id": "dossier-2",
                    "passed": False,
                    "evaluations": [
                        _evaluation("delay-window", "pass"),
                        _evaluation("proof-of-damage", "pass"),
                        _evaluation("rationale-matches", "pass"),
                        _evaluation("amount-ceiling", "fail", reason="over the authority limit"),
                    ],
                    "evaluation_metadata": {"stage": "candidate", "dataset_fingerprint": "sha256:abc"},
                },
            ],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_judges_every_case(self) -> None:
        report = judge(self.case_dir)
        self.assertEqual(report["summary"]["approve"], 1)
        self.assertEqual(report["summary"]["reject"], 1)
        self.assertTrue((self.case_dir / "judgements.jsonl").is_file())

    def test_judges_a_single_case_and_carries_provenance(self) -> None:
        report = judge(self.case_dir, case_id="dossier-2")
        self.assertEqual(report["verdict"], "reject")
        self.assertEqual(report["provenance"]["dataset_fingerprint"], "sha256:abc")
        self.assertTrue((self.case_dir / "judgement.json").is_file())

    def test_rejects_an_unknown_case_and_a_missing_section(self) -> None:
        with self.assertRaises(DataError):
            judge(self.case_dir, case_id="ghost")
        suite = read_json(self.case_dir / "evaluation.json")
        del suite["judge"]
        write_json(self.case_dir / "evaluation.json", suite)
        with self.assertRaises(DataError) as raised:
            judge(self.case_dir)
        self.assertIn("no 'judge' section", str(raised.exception))

    def test_aik_validate_checks_the_judge_section(self) -> None:
        suite = read_json(self.case_dir / "evaluation.json")
        suite["judge"] = {**CONFIG, "hard_rules": ["nonexistent-rule"]}
        write_json(self.case_dir / "evaluation.json", suite)
        errors, _warnings = validate(self.case_dir)
        self.assertTrue(any("'judge' section is invalid" in error for error in errors))

    def test_yaml_rendering(self) -> None:
        report = judge(self.case_dir, case_id="dossier-2")
        rendered = render_yaml(report)
        self.assertIn("verdict: reject", rendered)
        self.assertIn("critical_violations:", rendered)
        self.assertIn("amount-ceiling", rendered)

    def test_cli_judges_one_case(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "aik.py"), "judge", str(self.case_dir), "--case-id", "dossier-2"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("verdict: reject", completed.stdout)


if __name__ == "__main__":
    unittest.main()
