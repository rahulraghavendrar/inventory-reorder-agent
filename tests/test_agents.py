"""Agent wiring tested with a scripted fake LLM - no API key or internet needed.

The fake model returns pre-written responses (tool calls, then text), so we can
prove the routing, tool execution, guardrails and structured output all work.
"""
import asyncio
import json
from typing import Any

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from agents.inventory_agent.agent import build_root_agent


class ScriptedLlm(BaseLlm):
    model: str = "scripted-test-model"
    script: list[Any] = Field(default_factory=list)
    requests: list[Any] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        step = self.script.pop(0)
        if isinstance(step, tuple):
            name, args = step
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        else:
            part = types.Part(text=step)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def run_agent(script, question, use_mcp=False):
    async def _run():
        llm = ScriptedLlm(script=list(script))
        runner = InMemoryRunner(agent=build_root_agent(model=llm, use_mcp=use_mcp), app_name="test")
        session = await runner.session_service.create_session(app_name="test", user_id="u")
        events = []
        async for event in runner.run_async(
            user_id="u", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=question)]),
        ):
            events.append(event)
        session = await runner.session_service.get_session(app_name="test", user_id="u", session_id=session.id)
        texts = [p.text for e in events if e.content for p in e.content.parts if p.text]
        calls = [p.function_call.name for e in events if e.content for p in e.content.parts if p.function_call]
        return texts, calls, session.state, llm

    return asyncio.run(_run())


ROUTE_TO_ANALYST = ("transfer_to_agent", {"agent_name": "reorder_analyst"})


@pytest.mark.parametrize("use_mcp", [False, True])
def test_analyst_uses_tools_and_grounded_answer_passes(use_mcp):
    texts, calls, state, _ = run_agent(
        [ROUTE_TO_ANALYST, ("analyze_inventory_file", {}),
         "IT-113 is OUT_OF_STOCK (on hand -12, reorder point 1458.1). IT-120 has 10 days left vs a 14 day lead time."],
        "What should I reorder?", use_mcp=use_mcp,
    )
    assert calls == ["transfer_to_agent", "analyze_inventory_file"]
    assert state["guardrail_last_result"]["passed"] is True
    assert "Guardrail warning" not in texts[-1]


def test_guardrail_flags_hallucinated_numbers():
    texts, _, state, _ = run_agent(
        [ROUTE_TO_ANALYST, ("analyze_inventory_file", {}), "IT-120 has 8.8 days left; reorder point 999."],
        "What should I reorder?",
    )
    assert state["guardrail_last_result"]["unverified"] == [8.8, 999.0]
    assert "Guardrail warning" in texts[-1]


def test_answer_without_calling_a_tool_is_flagged():
    texts, calls, state, _ = run_agent(
        [ROUTE_TO_ANALYST, "IT-103 has 12.4 days of stock left."],
        "How long will IT-103 last?",
    )
    assert "analyze_inventory_file" not in calls
    assert state["guardrail_last_result"]["passed"] is False


def test_numbers_typed_by_the_user_are_allowed():
    _, _, state, _ = run_agent(
        [ROUTE_TO_ANALYST, ("simulate_scenario", {"item_code": "IT-100", "lead_time_days": 21}),
         "With a 21 day lead time IT-100's reorder point becomes 1200, so it becomes CRITICAL."],
        "What if IT-100's lead time becomes 21 days?",
    )
    assert state["guardrail_last_result"]["passed"] is True


def test_data_quality_agent_gets_validator_output_and_structured_schema():
    good = {
        "assessments": [
            {"item_code": c, "issue_codes": [k], "severity": "HIGH", "likely_causes": ["x"],
             "business_impact": "y", "recommended_action": "z", "owner": "Warehouse",
             "needs_human_review": True}
            for c, k in [("IT-106", "ZERO_USAGE"), ("IT-113", "NEGATIVE_STOCK"), ("IT-118", "MISSING_LEAD_TIME")]
        ],
        "overall_note": "Three rows need review.",
    }
    texts, _, state, llm = run_agent(
        [("transfer_to_agent", {"agent_name": "data_quality_agent"}), json.dumps(good)],
        "Are there any data problems?",
    )
    request = llm.requests[-1]
    prompt = str(request.config.system_instruction)
    assert "IT-118" in prompt and "MISSING_LEAD_TIME" in prompt          # context injected
    assert request.config.response_schema is not None or request.config.response_json_schema is not None
    assert len(state["data_quality_assessment"]["assessments"]) == 3      # validated JSON stored
    assert not any("Guardrail check" in t for t in texts)


def test_data_quality_id_guardrail():
    bad = {
        "assessments": [
            {"item_code": "IT-999", "issue_codes": ["X"], "severity": "LOW", "likely_causes": ["x"],
             "business_impact": "y", "recommended_action": "z", "owner": "Planning",
             "needs_human_review": True}
        ],
        "overall_note": "n",
    }
    texts, _, _, _ = run_agent(
        [("transfer_to_agent", {"agent_name": "data_quality_agent"}), json.dumps(bad)],
        "Any data problems?",
    )
    assert "IT-999" in texts[-1] and "IT-118" in texts[-1]
