"""Turn tool results into display tables.

Tables in the UI are built from the tools' own JSON (exact, verified numbers),
not parsed from the LLM's text.
"""
from __future__ import annotations

from typing import Any


def _n(v: Any) -> Any:
    if isinstance(v, float):
        return int(v) if v.is_integer() else round(v, 1)
    return "" if v is None else v


def unwrap_tool_response(response: Any) -> dict:
    """Normalise a tool response from direct calls or from MCP into a plain dict."""
    if not isinstance(response, dict):
        return {}
    data = response
    if "structuredContent" in data and isinstance(data["structuredContent"], dict):
        data = data["structuredContent"]
    if set(data.keys()) == {"result"} and isinstance(data["result"], dict):
        data = data["result"]
    return data


def _item_rows(items: list[dict], ranked: bool = True) -> list[list]:
    rows = []
    for n, i in enumerate(items, start=1):
        lt = i.get("lead_time_days")
        if lt is None:
            lt = f"missing (assumed {_n(i.get('assumed_lead_time_days'))})" if i.get("assumed_lead_time_days") else "missing"
        row = [
            i.get("item_code"), i.get("description"), _n(i.get("on_hand")),
            _n(i.get("reorder_point")), _n(i.get("days_until_stockout")), _n(lt),
            i.get("urgency"), i.get("action", ""),
        ]
        rows.append(([n] if ranked else []) + row)
    return rows


ITEM_COLUMNS = ["Item", "Description", "On hand", "Reorder point", "Days left", "Lead time", "Urgency", "Action"]


def tables_from_tool(name: str, response: Any) -> list[dict]:
    data = unwrap_tool_response(response)
    if not data or "error" in data:
        return []

    if name == "get_full_sorted_list":
        rows = [
            [i["rank"], i["item_code"], i["description"], _n(i["on_hand"]), _n(i["reorder_point"]),
             _n(i["days_until_stockout"]), _n(i["lead_time_days"]), i["reorder_now"], i["urgency"]]
            for i in data.get("items", [])
        ]
        return [{
            "title": f"Full sorted list ({len(rows)} items, by days until stockout, zero usage last)",
            "columns": ["#", "Item", "Description", "On hand", "Reorder point", "Days left",
                        "Lead time", "Reorder now", "Urgency"],
            "rows": rows,
            "urgency_col": 8,
        }]

    if name == "analyze_inventory_file":
        tables = [{
            "title": f"Reorder list ({len(data.get('reorder_list', []))} items, most urgent first)",
            "columns": ["#"] + ITEM_COLUMNS,
            "rows": _item_rows(data.get("reorder_list", [])),
            "urgency_col": 7,
        }]
        if data.get("watch_list"):
            tables.append({
                "title": "Watch list (close to reorder point)",
                "columns": ITEM_COLUMNS,
                "rows": _item_rows(data["watch_list"], ranked=False),
                "urgency_col": 6,
            })
        if data.get("exceptions"):
            tables.append(_exceptions_table(data["exceptions"]))
        return tables

    if name == "get_data_quality_issues":
        return [_exceptions_table(data.get("issues", []))]

    if name == "get_item_details":
        return [{
            "title": "Item details",
            "columns": ITEM_COLUMNS,
            "rows": _item_rows(data.get("items", []), ranked=False),
            "urgency_col": 6,
        }]

    if name == "simulate_scenario":
        before, after = data.get("before", {}), data.get("after", {})
        fields = [
            ("Lead time (days)", "lead_time_days"), ("Daily usage", "avg_daily_usage"),
            ("Safety stock", "safety_stock"), ("On hand", "on_hand"),
            ("Reorder point", "reorder_point"), ("Days left", "days_until_stockout"),
            ("Reorder needed", "needs_reorder"), ("Urgency", "urgency"),
        ]
        rows = [[label, _n(before.get(key)), _n(after.get(key))] for label, key in fields]
        return [{
            "title": f"What-if for {data.get('item_code')}: {data.get('changed_inputs')}",
            "columns": ["", "Before", "After"],
            "rows": rows,
            "urgency_col": None,
        }]
    return []


def _exceptions_table(items: list[dict]) -> dict:
    rows = [
        [i.get("item_code"), i.get("description"),
         ", ".join(x["code"] for x in i.get("issues", [])),
         i.get("urgency"), " ".join(i.get("notes", []))]
        for i in items
    ]
    return {
        "title": "Data-quality exceptions (need a person to check)",
        "columns": ["Item", "Description", "Issue", "Urgency", "How it was handled"],
        "rows": rows,
        "urgency_col": 3,
    }
