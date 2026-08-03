"""Two-subagent LangGraph demo adapted from LangChain's official subagents pattern."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Sequence

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


BILLING_WORDS = {"bill", "billing", "charge", "charged", "invoice", "payment", "refund"}
TECHNICAL_WORDS = {"app", "bug", "crash", "error", "login", "password", "technical"}


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    return str(content)


class MockSpecialistModel(BaseChatModel):
    domain: str

    @property
    def _llm_type(self) -> str:
        return f"aik-mock-{self.domain}"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        request = _message_text(messages[-1])
        answer = f"{self.domain.title()} specialist handled: {request}"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])


class MockSupervisorModel(BaseChatModel):
    stage: str
    tool_names: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "aik-mock-supervisor"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any) -> "MockSupervisorModel":
        names = tuple(getattr(item, "name", str(item)) for item in tools)
        return self.model_copy(update={"tool_names": names})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tool_results = [message for message in messages if isinstance(message, ToolMessage)]
        if tool_results:
            combined = "\n".join(_message_text(message) for message in tool_results)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"Supervisor summary:\n{combined}"))])

        user_text = next((_message_text(message) for message in reversed(messages) if isinstance(message, HumanMessage)), "")
        words = {word.strip(".,!?;:").lower() for word in user_text.split()}
        wants_billing = bool(words & BILLING_WORDS)
        wants_technical = bool(words & TECHNICAL_WORDS)
        calls: list[dict[str, Any]] = []
        if wants_billing:
            calls.append({"name": "consult_billing_agent", "args": {"request": user_text}, "id": "billing-call", "type": "tool_call"})
        if wants_technical and not (self.stage == "baseline" and wants_billing):
            calls.append({"name": "consult_technical_agent", "args": {"request": user_text}, "id": "technical-call", "type": "tool_call"})
        if calls:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=calls))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="This request needs no specialist."))])


@dataclass
class TracedAgent:
    agent: Any
    agents_called: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def invoke(self, graph_input: dict[str, Any]) -> dict[str, Any]:
        self.agents_called.clear()
        self.tool_calls.clear()
        result = self.agent.invoke(graph_input)
        final_message = result["messages"][-1]
        return {
            "final_answer": _message_text(final_message),
            "agents_called": ["supervisor", *self.agents_called],
            "tool_calls": list(self.tool_calls),
        }


def _model(mode: str, role: str, stage: str) -> BaseChatModel:
    if mode == "mock":
        return MockSupervisorModel(stage=stage) if role == "supervisor" else MockSpecialistModel(domain=role)
    if mode != "openai":
        raise ValueError("AIK_DEMO_MODE must be 'mock' or 'openai'")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("AIK_DEMO_MODEL", "gpt-5.4-mini"),
        use_responses_api=True,
        reasoning_effort="low",
        timeout=60,
        max_retries=2,
    )


def build_graph(stage: str) -> TracedAgent:
    mode = os.getenv("AIK_DEMO_MODE", "mock").lower()
    billing_agent = create_agent(
        _model(mode, "billing", stage),
        tools=[],
        system_prompt="You are a billing specialist. Resolve only billing, payment, invoice, charge, and refund questions. Return a concise finding.",
    )
    technical_agent = create_agent(
        _model(mode, "technical", stage),
        tools=[],
        system_prompt="You are a technical support specialist. Resolve only application, login, password, crash, and error questions. Return a concise finding.",
    )
    trace = TracedAgent(agent=None)

    @tool("consult_billing_agent", description="Delegate billing, charge, payment, invoice, or refund work to the billing specialist.")
    def consult_billing_agent(request: str) -> str:
        trace.agents_called.append("billing-agent")
        trace.tool_calls.append({"name": "consult_billing_agent", "arguments": {"request": request}})
        result = billing_agent.invoke({"messages": [{"role": "user", "content": request}]})
        return _message_text(result["messages"][-1])

    @tool("consult_technical_agent", description="Delegate application, login, password, crash, bug, or error work to the technical specialist.")
    def consult_technical_agent(request: str) -> str:
        trace.agents_called.append("technical-agent")
        trace.tool_calls.append({"name": "consult_technical_agent", "arguments": {"request": request}})
        result = technical_agent.invoke({"messages": [{"role": "user", "content": request}]})
        return _message_text(result["messages"][-1])

    baseline_rule = "For a request involving both domains, call only the first matching specialist." if stage == "baseline" else "For a request involving both domains, call both specialists."
    supervisor = create_agent(
        _model(mode, "supervisor", stage),
        tools=[consult_billing_agent, consult_technical_agent],
        system_prompt=(
            "You are a support supervisor. Delegate domain work to the two specialist tools and synthesize their results. "
            "Do not call a specialist for an unrelated greeting or general request. " + baseline_rule
        ),
    )
    trace.agent = supervisor
    return trace


def evaluate(*, case: dict[str, Any], result: dict[str, Any], stage: str) -> dict[str, Any]:
    expected = case.get("expected", {})
    actual = set(result.get("agents_called", [])) - {"supervisor"}
    required = set(expected.get("required_agents", []))
    forbidden = set(expected.get("forbidden_agents", []))
    required_ok = required <= actual
    forbidden_ok = not (forbidden & actual)
    passed = required_ok and forbidden_ok
    return {
        "passed": passed,
        "output": result.get("final_answer", ""),
        "agents": result.get("agents_called", []),
        "tool_calls": result.get("tool_calls", []),
        "scores": {"routing_accuracy": 1.0 if passed else 0.0},
        "contracts": {
            "required-agents-called": "pass" if required_ok else "fail",
            "forbidden-agents-avoided": "pass" if forbidden_ok else "fail",
        },
        "metadata": {"stage": stage, "mode": os.getenv("AIK_DEMO_MODE", "mock")},
    }
