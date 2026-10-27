"""Data models shared by the core engine, the MCP server and the agents.

Pydantic models give us validated, typed, JSON-serialisable records, so the
same objects can be returned by MCP tools and read by LLM agents.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class IssueCode(str, Enum):
    """Every data-quality problem the validator can detect."""

    ZERO_USAGE = "ZERO_USAGE"                    # usage = 0 -> never runs out
    NEGATIVE_STOCK = "NEGATIVE_STOCK"            # on_hand < 0 -> count error / backorders
    MISSING_LEAD_TIME = "MISSING_LEAD_TIME"      # lead time blank
    MISSING_VALUE = "MISSING_VALUE"              # any other required number blank
    INVALID_NUMBER = "INVALID_NUMBER"            # text in a number column
    NEGATIVE_VALUE = "NEGATIVE_VALUE"            # negative usage / lead time / safety stock
    DUPLICATE_ITEM_CODE = "DUPLICATE_ITEM_CODE"  # same code on more than one row
    MISSING_ITEM_CODE = "MISSING_ITEM_CODE"      # blank item code
    MISSING_DESCRIPTION = "MISSING_DESCRIPTION"  # blank description


class Urgency(str, Enum):
    """Urgency tiers, most urgent first."""

    OUT_OF_STOCK = "OUT_OF_STOCK"  # on hand <= 0: already empty
    CRITICAL = "CRITICAL"          # runs out before a new order can arrive
    REORDER = "REORDER"            # at/below reorder point, safety stock still covers the wait
    WATCH = "WATCH"                # safe for now, but just above the reorder point
    SAFE = "SAFE"                  # comfortably above the reorder point: no order needed now
    REVIEW = "REVIEW"              # cannot be decided until a person fixes the data


ACTIONS = {
    Urgency.OUT_OF_STOCK: "ORDER IMMEDIATELY - already out of stock, also cover backorders",
    Urgency.CRITICAL: "ORDER TODAY - will run out before a new delivery can arrive",
    Urgency.REORDER: "ORDER NOW - at/below reorder point, safety stock covers the wait",
    Urgency.WATCH: "NO ORDER YET - close to reorder point, monitor",
    Urgency.SAFE: "NO ORDER NEEDED - stock is safe for now",
    Urgency.REVIEW: "FIX DATA FIRST - cannot decide until a person corrects this row",
}

URGENCY_RANK = {
    Urgency.OUT_OF_STOCK: 0,
    Urgency.CRITICAL: 1,
    Urgency.REORDER: 2,
    Urgency.WATCH: 3,
    Urgency.SAFE: 4,
    Urgency.REVIEW: 5,
}


class DataIssue(BaseModel):
    code: IssueCode
    field: Optional[str] = None
    message: str
    blocking: bool = Field(
        description="True if the issue stops the normal reorder calculation for this row"
    )


class ItemResult(BaseModel):
    """One inventory row after validation and calculation."""

    row_number: int = Field(description="Line number in the CSV (header = line 1)")
    item_code: str
    description: str

    # Raw inputs (None = missing or not a valid number)
    on_hand: Optional[float] = None
    avg_daily_usage: Optional[float] = None
    lead_time_days: Optional[float] = None
    safety_stock: Optional[float] = None

    # Step 1-4 outputs
    lead_time_demand: Optional[float] = Field(
        None, description="Units used while waiting for a delivery = usage x lead time"
    )
    reorder_point: Optional[float] = Field(
        None, description="lead_time_demand + safety_stock"
    )
    needs_reorder: Optional[bool] = Field(
        None, description="on_hand <= reorder_point; None when it cannot be decided"
    )
    units_below_reorder_point: Optional[float] = None
    days_until_stockout: Optional[float] = Field(
        None, description="on_hand / usage; 0 if already out; None if it never runs out"
    )
    never_runs_out: bool = False
    urgency: Urgency = Urgency.SAFE
    action: str = ""

    # Edge-case handling
    provisional: bool = Field(
        False, description="Result uses a worst-case assumption because data was missing"
    )
    assumed_lead_time_days: Optional[float] = None
    lead_time_analysis: Optional[dict] = Field(
        None, description="Break-even / robustness analysis when the lead time is missing"
    )
    issues: list[DataIssue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def has_blocking_issue(self) -> bool:
        return any(i.blocking for i in self.issues)


class AnalysisSummary(BaseModel):
    total_items: int
    items_to_reorder: int
    out_of_stock: int
    critical: int
    reorder: int
    watch: int
    safe: int
    items_with_data_issues: int
    provisional_items: int


class AnalysisReport(BaseModel):
    source_file: str
    summary: AnalysisSummary
    reorder_list: list[ItemResult] = Field(
        description="Items at or below reorder point, sorted by days until stockout"
    )
    watch_list: list[ItemResult]
    ranked_all: list[ItemResult] = Field(
        description="Every item ranked by days until stockout; zero-usage items last"
    )
    exceptions: list[ItemResult] = Field(description="Rows with any data-quality issue")
    all_items: list[ItemResult]
    assumptions: list[str]
