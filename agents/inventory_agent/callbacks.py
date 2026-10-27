"""ADK callbacks = guardrails and context engineering around the LLM.

record_grounded_numbers   after every tool call, remember all numbers the tool returned
number_guardrail          after every model answer, flag numbers that were never returned
                          by a tool or typed by the user (hallucination check)
load_data_issues          before the Data Quality agent runs, inject the validator's
                          findings into its prompt (no LLM-side detection)
check_assessment_ids      after the Data Quality agent, make sure it only talks about
                          item codes that really have issues
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from google.genai import types

from inventory_core import tools as inventory_tools
from inventory_core.guardrails import collect_numbers, verify_numbers, warning_text

logger = logging.getLogger("inventory_agent.guardrail")

GROUNDED_KEY = "grounded_numbers"
GUARDRAIL_KEY = "guardrail_last_result"


def _text_of(content: Optional[types.Content]) -> str:
    if not content or not content.parts:
        return ""
    return "".join(p.text for p in content.parts if getattr(p, "text", None))


def record_grounded_numbers(tool, args: dict[str, Any], tool_context, tool_response) -> None:
    """after_tool_callback: every number in a tool's input/output becomes 'grounded'."""
    existing = set(tool_context.state.get(GROUNDED_KEY, []))
    existing |= collect_numbers(tool_response)
    existing |= collect_numbers(args)
    tool_context.state[GROUNDED_KEY] = sorted(existing)
    return None  # keep the original tool response


def number_guardrail(callback_context, llm_response):
    """after_model_callback: block silent hallucinated numbers."""
    if getattr(llm_response, "partial", False):
        return None  # only check the complete answer when streaming
    content = llm_response.content
    if not content or not content.parts:
        return None
    if any(getattr(p, "function_call", None) for p in content.parts):
        return None  # the model is calling a tool, not answering yet
    text = _text_of(content)
    if not text.strip():
        return None

    grounded = set(callback_context.state.get(GROUNDED_KEY, []))
    grounded |= collect_numbers(_text_of(callback_context.user_content))

    result = verify_numbers(text, grounded)
    callback_context.state[GUARDRAIL_KEY] = {
        "passed": result.passed,
        "numbers_checked": result.checked,
        "unverified": result.unverified,
    }
    if result.passed:
        return None

    logger.warning("Guardrail flagged unverified numbers: %s", result.unverified)
    for part in reversed(content.parts):
        if getattr(part, "text", None):
            part.text = part.text + warning_text(result)
            break
    return llm_response


def load_data_issues(callback_context) -> None:
    """before_agent_callback: put the validator's findings into session state."""
    result = inventory_tools.get_data_quality_issues()
    issues = result.get("issues", []) if "error" not in result else []
    compact = [
        {
            "item_code": i["item_code"],
            "description": i["description"],
            "on_hand": i["on_hand"],
            "avg_daily_usage": i["avg_daily_usage"],
            "lead_time_days": i["lead_time_days"],
            "safety_stock": i["safety_stock"],
            "issue_codes": [x["code"] for x in i["issues"]],
            "detected": [x["message"] for x in i["issues"]],
            "how_engine_handled_it": i["notes"],
            "urgency": i["urgency"],
            "action": i["action"],
            "lead_time_analysis": i["lead_time_analysis"],
        }
        for i in issues
    ]
    callback_context.state["data_issues"] = json.dumps(compact, indent=1)
    callback_context.state["data_issue_codes"] = [c["item_code"] for c in compact]
    return None


def check_assessment_ids(callback_context) -> Optional[types.Content]:
    """after_agent_callback: the structured answer may only mention real problem items."""
    assessment = callback_context.state.get("data_quality_assessment")
    if not assessment:
        return None
    if isinstance(assessment, str):
        try:
            assessment = json.loads(assessment)
        except json.JSONDecodeError:
            return None
    allowed = set(callback_context.state.get("data_issue_codes", []))
    mentioned = {a.get("item_code") for a in assessment.get("assessments", [])}
    unknown = sorted(c for c in mentioned if c not in allowed)
    missing = sorted(allowed - mentioned)
    if not unknown and not missing:
        return None
    msg = "Guardrail check on data-quality assessment:"
    if unknown:
        msg += f" Item codes not in the validator output were ignored: {', '.join(unknown)}."
    if missing:
        msg += f" These flagged items were not assessed and still need review: {', '.join(missing)}."
    return types.Content(role="model", parts=[types.Part(text=msg)])
