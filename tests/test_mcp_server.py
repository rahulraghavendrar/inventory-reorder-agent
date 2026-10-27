"""The MCP server, called through a real MCP client (in-memory transport)."""
import asyncio

from fastmcp import Client

from mcp_server.server import mcp


def call(name, args=None):
    async def _run():
        async with Client(mcp) as client:
            return (await client.call_tool(name, args or {})).data
    return asyncio.run(_run())


def test_all_tools_registered():
    async def _run():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}
    names = asyncio.run(_run())
    assert {
        "calculate_lead_time_demand", "calculate_reorder_point", "check_reorder_status",
        "rank_items_by_urgency", "analyze_inventory_file", "get_data_quality_issues",
        "get_item_details", "simulate_scenario",
    } <= names


def test_step_tools_chain():
    ltd = call("calculate_lead_time_demand", {"avg_daily_usage": 11.4, "lead_time_days": 14})
    rop = call("calculate_reorder_point", {"lead_time_demand": ltd["lead_time_demand"], "safety_stock": 34})
    chk = call("check_reorder_status", {"on_hand": 114, "reorder_point": rop["reorder_point"]})
    assert ltd["lead_time_demand"] == 159.6
    assert rop["reorder_point"] == 193.6
    assert chk["needs_reorder"] is True and chk["decision"] == "ORDER NOW"


def test_rank_tool_handles_edge_cases_inline():
    result = call("rank_items_by_urgency", {"items": [
        {"item_code": "A", "description": "a", "on_hand": 10, "avg_daily_usage": 5, "lead_time_days": 7, "safety_stock": 5},
        {"item_code": "B", "description": "b", "on_hand": -1, "avg_daily_usage": 5, "lead_time_days": 7, "safety_stock": 5},
        {"item_code": "C", "description": "c", "on_hand": 10, "avg_daily_usage": 0, "lead_time_days": 7, "safety_stock": 5},
    ]})
    assert [i["item_code"] for i in result["ranked_reorder_list"]] == ["B", "A"]
    assert {i["item_code"] for i in result["exceptions"]} == {"B", "C"}


def test_analyze_file_tool():
    result = call("analyze_inventory_file")
    assert result["summary"]["items_to_reorder"] == 9
    assert result["reorder_list"][0]["item_code"] == "IT-113"


def test_bad_input_returns_error_not_crash():
    assert "error" in call("calculate_lead_time_demand", {"avg_daily_usage": -5, "lead_time_days": 7})
    assert "error" in call("get_item_details", {"item_code": "NOPE"})


def test_cannot_read_files_outside_data_folder():
    assert "error" in call("analyze_inventory_file", {"csv_file": "../run_report.py"})
    assert "error" in call("analyze_inventory_file", {"csv_file": "/etc/passwd"})


def test_what_if_scenario():
    result = call("simulate_scenario", {"item_code": "IT-100", "lead_time_days": 21})
    assert result["before"]["urgency"] == "WATCH"
    assert result["after"]["reorder_point"] == 1200  # 50 x 21 + 150
    assert result["after"]["urgency"] == "CRITICAL"  # 17.1 days left < 21 day lead time


def test_full_sorted_list_tool():
    result = call("get_full_sorted_list")
    codes = [i["item_code"] for i in result["items"]]
    assert result["count"] == 25 and len(codes) == 25
    assert codes[0] == "IT-113" and codes[-1] == "IT-106"   # most urgent first, zero usage last
    assert result["items"][0]["reorder_now"] == "YES"
