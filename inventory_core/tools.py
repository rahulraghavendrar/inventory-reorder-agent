"""Tool functions - the single source of truth used by BOTH the MCP server and
the ADK agents (direct mode). Every function returns plain JSON-friendly dicts
and never raises: errors come back as {"error": "..."} so an LLM can read them.
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any, Optional

from . import calculations as calc
from .models import ItemResult
from .pipeline import analyze_inventory, build_report
from .validation import SchemaError, validate_rows

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("INVENTORY_DATA_DIR", PROJECT_ROOT / "data")).resolve()
DEFAULT_CSV = os.getenv("INVENTORY_CSV", "ex7_inventory.csv")


def _safe(fn):
    """Turn expected errors into {"error": ...} so agents can recover."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, FileNotFoundError, SchemaError, PermissionError) as exc:
            return {"error": str(exc)}

    return wrapper


def resolve_csv(csv_file: Optional[str] = None) -> Path:
    """Only allow CSV files inside the data folder (no reading arbitrary files)."""
    name = (csv_file or "").strip() or DEFAULT_CSV
    path = (DATA_DIR / name).resolve()
    if DATA_DIR not in path.parents and path != DATA_DIR:
        raise PermissionError(f"Only files inside the data folder are allowed: {name}")
    if path.suffix.lower() != ".csv":
        raise ValueError("Only .csv files are supported")
    if not path.exists():
        raise FileNotFoundError(f"{name} not found in the data folder")
    return path


def item_to_dict(item: ItemResult) -> dict[str, Any]:
    """Compact view of one item for LLM consumption."""
    return {
        "item_code": item.item_code,
        "description": item.description,
        "on_hand": item.on_hand,
        "avg_daily_usage": item.avg_daily_usage,
        "lead_time_days": item.lead_time_days,
        "safety_stock": item.safety_stock,
        "lead_time_demand": item.lead_time_demand,
        "reorder_point": item.reorder_point,
        "needs_reorder": item.needs_reorder,
        "units_below_reorder_point": item.units_below_reorder_point,
        "days_until_stockout": "never" if item.never_runs_out else item.days_until_stockout,
        "urgency": item.urgency.value,
        "action": item.action,
        "provisional": item.provisional,
        "assumed_lead_time_days": item.assumed_lead_time_days,
        "lead_time_analysis": (
            {k: v for k, v in item.lead_time_analysis.items() if k != "scenario_results"}
            if item.lead_time_analysis else None
        ),
        "issues": [{"code": i.code.value, "message": i.message, "blocking": i.blocking} for i in item.issues],
        "notes": item.notes,
    }


# --------------------------------------------------------------------------- #
# The four step tools (generic: usable for any item, any dataset)
# --------------------------------------------------------------------------- #
@_safe
def calculate_lead_time_demand(avg_daily_usage: float, lead_time_days: float) -> dict:
    """Step 1: units used while waiting for a delivery (usage per day x lead time in days)."""
    value = calc.lead_time_demand(avg_daily_usage, lead_time_days)
    return {
        "lead_time_demand": value,
        "formula": f"{avg_daily_usage:g} x {lead_time_days:g} = {value:g}",
    }


@_safe
def calculate_reorder_point(lead_time_demand: float, safety_stock: float) -> dict:
    """Step 2: reorder point = lead-time demand + safety stock."""
    value = calc.reorder_point(lead_time_demand, safety_stock)
    return {
        "reorder_point": value,
        "formula": f"{lead_time_demand:g} + {safety_stock:g} = {value:g}",
    }


@_safe
def check_reorder_status(on_hand: float, reorder_point: float) -> dict:
    """Step 3: should we order now? True when stock on hand is at or below the reorder point."""
    needs = calc.check_reorder(on_hand, reorder_point)
    return {
        "needs_reorder": needs,
        "units_below_reorder_point": calc.units_below(on_hand, reorder_point),
        "decision": "ORDER NOW" if needs else "NO ORDER NEEDED YET",
    }


@_safe
def rank_items_by_urgency(items: list[dict]) -> dict:
    """Step 4: validate, evaluate and sort any list of items by days until stockout.

    Each item needs: item_code, description, on_hand, avg_daily_usage,
    lead_time_days, safety_stock. Edge cases (zero usage, negative stock,
    missing values) are handled exactly as for the CSV.
    """
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    rows = [
        {k: "" if item.get(k) is None else str(item.get(k)) for k in (
            "item_code", "description", "on_hand", "avg_daily_usage", "lead_time_days", "safety_stock"
        )}
        for item in items
    ]
    report = build_report(validate_rows(rows), "inline-items")
    return {
        "ranked_reorder_list": [item_to_dict(i) for i in report.reorder_list],
        "watch_list": [item_to_dict(i) for i in report.watch_list],
        "exceptions": [item_to_dict(i) for i in report.exceptions],
    }


# --------------------------------------------------------------------------- #
# Dataset-level tools
# --------------------------------------------------------------------------- #
@_safe
def analyze_inventory_file(csv_file: str = "") -> dict:
    """Run the full reorder analysis on an inventory CSV in the data folder.

    Returns the reorder list sorted by days until stockout, the watch list,
    the data-quality exceptions, a summary and the assumptions used.
    Leave csv_file empty to use the default file.
    """
    report = analyze_inventory(resolve_csv(csv_file))
    return {
        "source_file": report.source_file,
        "summary": report.summary.model_dump(),
        "reorder_list": [item_to_dict(i) for i in report.reorder_list],
        "watch_list": [item_to_dict(i) for i in report.watch_list],
        "exceptions": [item_to_dict(i) for i in report.exceptions],
        "full_ranking": _full_ranking(report),
        "assumptions": report.assumptions,
    }


def _full_ranking(report) -> list[dict]:
    return [
        {
            "rank": n,
            "item_code": i.item_code,
            "description": i.description,
            "on_hand": i.on_hand,
            "avg_daily_usage": i.avg_daily_usage,
            "lead_time_days": i.lead_time_days if i.lead_time_days is not None else "blank",
            "safety_stock": i.safety_stock,
            "reorder_point": i.reorder_point,
            "days_until_stockout": "never" if i.never_runs_out else i.days_until_stockout,
            "needs_reorder": i.needs_reorder,
            "urgency": i.urgency.value,
        }
        for n, i in enumerate(report.ranked_all, start=1)
    ]


@_safe
def get_full_sorted_list(csv_file: str = "") -> dict:
    """Every item in the file, sorted by days until stockout (most urgent first,
    zero-usage items last), with reorder point, reorder decision and urgency.
    Use this when the user asks for the whole / full / complete sorted list."""
    report = analyze_inventory(resolve_csv(csv_file))
    rows = []
    for n, i in enumerate(report.ranked_all, start=1):
        rows.append({
            "rank": n,
            "item_code": i.item_code,
            "description": i.description,
            "on_hand": i.on_hand,
            "reorder_point": i.reorder_point,
            "days_until_stockout": "never" if i.never_runs_out else i.days_until_stockout,
            "lead_time_days": i.lead_time_days if i.lead_time_days is not None else "missing",
            "reorder_now": "YES" if i.needs_reorder else ("REVIEW" if i.needs_reorder is None else "NO"),
            "urgency": i.urgency.value,
        })
    return {"source_file": report.source_file, "count": len(rows), "items": rows}


@_safe
def get_data_quality_issues(csv_file: str = "") -> dict:
    """List every row with a data-quality problem, what was detected and how it was handled."""
    report = analyze_inventory(resolve_csv(csv_file))
    return {
        "source_file": report.source_file,
        "count": len(report.exceptions),
        "issues": [item_to_dict(i) for i in report.exceptions],
    }


@_safe
def get_item_details(item_code: str, csv_file: str = "") -> dict:
    """Full calculation details for one item code (case-insensitive)."""
    report = analyze_inventory(resolve_csv(csv_file))
    wanted = item_code.strip().upper()
    matches = [i for i in report.all_items if i.item_code.upper() == wanted]
    if not matches:
        raise ValueError(f"Item {item_code} not found")
    return {"items": [item_to_dict(i) for i in matches]}


@_safe
def simulate_scenario(
    item_code: str,
    avg_daily_usage: Optional[float] = None,
    lead_time_days: Optional[float] = None,
    safety_stock: Optional[float] = None,
    on_hand: Optional[float] = None,
    csv_file: str = "",
) -> dict:
    """What-if analysis for one item: override any input and recalculate.

    Example: 'what if the supplier lead time for IT-100 becomes 21 days?'
    Only the values you pass are changed; the rest come from the file.
    """
    report = analyze_inventory(resolve_csv(csv_file))
    wanted = item_code.strip().upper()
    base = next((i for i in report.all_items if i.item_code.upper() == wanted), None)
    if base is None:
        raise ValueError(f"Item {item_code} not found")

    def pick(override, original):
        return "" if (override if override is not None else original) is None else str(
            override if override is not None else original
        )

    row = {
        "item_code": base.item_code,
        "description": base.description,
        "on_hand": pick(on_hand, base.on_hand),
        "avg_daily_usage": pick(avg_daily_usage, base.avg_daily_usage),
        "lead_time_days": pick(lead_time_days, base.lead_time_days),
        "safety_stock": pick(safety_stock, base.safety_stock),
    }
    # Evaluate with the rest of the file present, so the worst-case lead time
    # used for missing values stays the same as in the real analysis.
    others = [
        {
            "item_code": i.item_code, "description": i.description,
            "on_hand": "" if i.on_hand is None else str(i.on_hand),
            "avg_daily_usage": "" if i.avg_daily_usage is None else str(i.avg_daily_usage),
            "lead_time_days": "" if i.lead_time_days is None else str(i.lead_time_days),
            "safety_stock": "" if i.safety_stock is None else str(i.safety_stock),
        }
        for i in report.all_items if i.row_number != base.row_number
    ]
    scenario = build_report(validate_rows([row] + others), "scenario")
    after = scenario.all_items[0]
    return {
        "item_code": base.item_code,
        "changed_inputs": {k: v for k, v in {
            "avg_daily_usage": avg_daily_usage, "lead_time_days": lead_time_days,
            "safety_stock": safety_stock, "on_hand": on_hand,
        }.items() if v is not None},
        "before": item_to_dict(base),
        "after": item_to_dict(after),
    }


ALL_TOOLS = [
    calculate_lead_time_demand,
    calculate_reorder_point,
    check_reorder_status,
    rank_items_by_urgency,
    analyze_inventory_file,
    get_full_sorted_list,
    get_data_quality_issues,
    get_item_details,
    simulate_scenario,
]
