#!/usr/bin/env python3
"""Optional OpenAI implementation of AIK's external semantic evaluator protocol."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def main() -> int:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("The optional OpenAI judge requires the 'openai' package") from exc

    request = json.load(sys.stdin)
    model = os.getenv("AIK_JUDGE_MODEL", "gpt-5.6-luna")
    evaluator = request["evaluator"]
    threshold = float(evaluator.get("threshold", 0.7))
    material: dict[str, Any] = {
        "user_input": request["case"].get("input"),
        "expected": request["case"].get("expected"),
        "rubric": request.get("rubric"),
        "agent_output": request["result"].get("output"),
        "agents": request["result"].get("agents", []),
        "tool_calls": request["result"].get("tool_calls", []),
    }
    schema = {
        "type": "object",
        "properties": {
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["score", "reason", "evidence"],
        "additionalProperties": False,
    }
    response = OpenAI().responses.create(
        model=model,
        reasoning={"effort": os.getenv("AIK_JUDGE_REASONING", "low")},
        instructions=(
            "You are an independent AI-agent evaluator. Score only the supplied evidence against every rubric item. "
            "A score of 1 means fully satisfied and 0 means not satisfied. Do not reward style unless the rubric asks for it. "
            "Cite short evidence from the supplied material and explicitly mention missing evidence."
        ),
        input=json.dumps(material, ensure_ascii=False),
        text={"format": {"type": "json_schema", "name": "aik_evaluation", "strict": True, "schema": schema}},
        store=False,
    )
    value = json.loads(response.output_text)
    score = float(value["score"])
    json.dump(
        {
            "status": "pass" if score >= threshold else "fail",
            "score": score,
            "reason": value["reason"],
            "evidence": value["evidence"],
            "metadata": {"provider": "openai", "model": model, "response_id": response.id},
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
