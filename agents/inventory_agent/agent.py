"""Google ADK multi-agent system for the inventory reorder alert.

          inventory_coordinator  (root, routes the request)
           /            |               \\
  reorder_analyst  data_quality_agent  report_writer
        |                 |                  |
   MCP tools        validator output     MCP tools
  (exact math)     + structured JSON    (exact math)

LLMs reason and explain; the MCP tools do all calculations.

Environment (.env in the project root):
  GOOGLE_API_KEY=...            Gemini API key
  GEMINI_MODEL=gemini-flash-latest
  USE_MCP=true                  false = call the same tools directly (no MCP process)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # python-dotenv is optional
    pass

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

from google.adk.agents import LlmAgent  # noqa: E402

from inventory_core import tools as inventory_tools  # noqa: E402

from . import prompts  # noqa: E402
from .callbacks import (  # noqa: E402
    check_assessment_ids,
    load_data_issues,
    number_guardrail,
    record_grounded_numbers,
)
from .schemas import DataQualityAssessment  # noqa: E402

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
MCP_SERVER = PROJECT_ROOT / "mcp_server" / "server.py"

ANALYST_TOOLS = [
    "get_full_sorted_list",
    "analyze_inventory_file",
    "get_item_details",
    "simulate_scenario",
    "calculate_lead_time_demand",
    "calculate_reorder_point",
    "check_reorder_status",
]
WRITER_TOOLS = ["analyze_inventory_file"]


def _use_mcp(flag: bool | None) -> bool:
    if flag is not None:
        return flag
    return os.getenv("USE_MCP", "true").strip().lower() not in {"0", "false", "no"}


def build_tools(names: list[str], use_mcp: bool | None = None) -> list:
    """Same tools either through the MCP server or as direct Python functions."""
    if _use_mcp(use_mcp):
        from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
        from mcp import StdioServerParameters

        return [
            McpToolset(
                connection_params=StdioConnectionParams(
                    server_params=StdioServerParameters(
                        command=sys.executable,
                        args=[str(MCP_SERVER)],
                        env=dict(os.environ),
                    ),
                    timeout=30,
                ),
                tool_filter=names,
            )
        ]
    by_name = {fn.__name__: fn for fn in inventory_tools.ALL_TOOLS}
    return [by_name[n] for n in names]


def default_model():
    """Gemini with automatic retries for overload (503) and rate-limit (429) errors."""
    from google.adk.models import Gemini
    from google.genai import types

    return Gemini(
        model=DEFAULT_MODEL,
        retry_options=types.HttpRetryOptions(
            attempts=6, initial_delay=2, max_delay=30, http_status_codes=[429, 500, 503],
        ),
    )


def build_root_agent(model=None, use_mcp: bool | None = None) -> LlmAgent:
    """Factory so tests can inject a fake model and run without an API key."""
    model = model or default_model()

    reorder_analyst = LlmAgent(
        name="reorder_analyst",
        model=model,
        description="Answers what to reorder, why, how urgent, and what-if scenarios using exact tools.",
        instruction=prompts.REORDER_ANALYST,
        tools=build_tools(ANALYST_TOOLS, use_mcp),
        after_tool_callback=record_grounded_numbers,
        after_model_callback=number_guardrail,
    )

    data_quality_agent = LlmAgent(
        name="data_quality_agent",
        model=model,
        description="Explains bad or missing inventory data and who should fix it (structured JSON).",
        instruction=prompts.DATA_QUALITY,
        output_schema=DataQualityAssessment,
        output_key="data_quality_assessment",
        before_agent_callback=load_data_issues,
        after_agent_callback=check_assessment_ids,
    )

    report_writer = LlmAgent(
        name="report_writer",
        model=model,
        description="Writes a short reorder summary for the purchasing team.",
        instruction=prompts.REPORT_WRITER,
        tools=build_tools(WRITER_TOOLS, use_mcp),
        output_key="procurement_summary",
        after_tool_callback=record_grounded_numbers,
        after_model_callback=number_guardrail,
    )

    return LlmAgent(
        name="inventory_coordinator",
        model=model,
        description="Routes inventory questions to the right specialist agent.",
        instruction=prompts.COORDINATOR,
        sub_agents=[reorder_analyst, data_quality_agent, report_writer],
    )


root_agent = build_root_agent()
