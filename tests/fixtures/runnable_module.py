"""Dependency-free fixture for the generic LangGraph-compatible adapter."""

from __future__ import annotations


class Runnable:
    def __init__(self, stage: str):
        self.stage = stage

    def invoke(self, graph_input):
        return {"value": graph_input["value"] * 2, "stage": self.stage}


def build_graph(stage: str):
    return Runnable(stage)


def evaluate(*, case, result, stage):
    expected = case["expected"]["value"]
    passed = result["value"] == expected and result["stage"] == stage
    return {"passed": passed, "output": str(result["value"]), "metadata": {"stage": stage}}
