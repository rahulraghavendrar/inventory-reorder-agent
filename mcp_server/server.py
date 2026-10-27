"""MCP server exposing the reorder tools with FastMCP.

Any MCP client can use these tools: the ADK agents in this project, Claude
Desktop, Cursor, or another team's agent.

Run (stdio, used by the ADK agents):   python mcp_server/server.py
Run (HTTP, for remote clients):        python mcp_server/server.py --http 8000
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastmcp import FastMCP  # noqa: E402

from inventory_core import tools  # noqa: E402

mcp = FastMCP(
    name="inventory-reorder",
    instructions=(
        "Deterministic inventory reorder tools. Steps: calculate_lead_time_demand -> "
        "calculate_reorder_point -> check_reorder_status -> rank_items_by_urgency. "
        "Use analyze_inventory_file for the whole dataset. All numbers are exact; "
        "never recalculate them yourself."
    ),
)

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

for fn in tools.ALL_TOOLS:
    mcp.tool(fn, annotations=READ_ONLY)


@mcp.resource("inventory://assumptions")
def assumptions() -> str:
    """The business rules and assumptions behind every calculation."""
    report = tools.analyze_inventory_file()
    return "\n".join(f"- {a}" for a in report.get("assumptions", []))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        mcp.run(transport="http", host="127.0.0.1", port=port)
    else:
        mcp.run(show_banner=False)
