"""The four calculation steps. Pure, deterministic functions - no LLM involved.

Step 1  lead_time_demand      how many units get used while waiting for delivery
Step 2  reorder_point         lead_time_demand + safety_stock
Step 3  check_reorder         is on_hand at or below the reorder point?
Step 4  days_until_stockout + classify_urgency + rank_by_urgency
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from .models import URGENCY_RANK, ItemResult, Urgency

# Rounding avoids float noise such as 43.2 * 21 = 907.2000000000001 flipping a
# comparison right at the boundary.
_PRECISION = 6


def _require_non_negative(name: str, value: float) -> None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} cannot be negative, got {value}")


# --------------------------------------------------------------------------- #
# Step 1
# --------------------------------------------------------------------------- #
def lead_time_demand(avg_daily_usage: float, lead_time_days: float) -> float:
    """Units consumed while waiting for a delivery = usage per day x lead time."""
    _require_non_negative("avg_daily_usage", avg_daily_usage)
    _require_non_negative("lead_time_days", lead_time_days)
    return round(avg_daily_usage * lead_time_days, _PRECISION)


# --------------------------------------------------------------------------- #
# Step 2
# --------------------------------------------------------------------------- #
def reorder_point(lead_time_demand_units: float, safety_stock: float) -> float:
    """Reorder point = lead-time demand + safety stock."""
    _require_non_negative("lead_time_demand", lead_time_demand_units)
    _require_non_negative("safety_stock", safety_stock)
    return round(lead_time_demand_units + safety_stock, _PRECISION)


# --------------------------------------------------------------------------- #
# Step 3
# --------------------------------------------------------------------------- #
def check_reorder(on_hand: float, reorder_point_units: float) -> bool:
    """True when stock is AT or BELOW the reorder point.

    on_hand may be negative (bad data / backorders): that is still "below".
    """
    if on_hand is None or reorder_point_units is None:
        raise ValueError("on_hand and reorder_point are required")
    return round(on_hand, _PRECISION) <= round(reorder_point_units, _PRECISION)


def units_below(on_hand: float, reorder_point_units: float) -> float:
    """How far stock is under the reorder point (0 if above)."""
    return round(max(0.0, reorder_point_units - on_hand), 2)


# --------------------------------------------------------------------------- #
# Step 4
# --------------------------------------------------------------------------- #
def days_until_stockout(on_hand: float, avg_daily_usage: float) -> Optional[float]:
    """Days of stock left.

    * usage == 0      -> None (never runs out; avoids divide-by-zero)
    * on_hand <= 0    -> 0.0  (already out of stock)
    * otherwise       -> on_hand / usage
    """
    _require_non_negative("avg_daily_usage", avg_daily_usage)
    if on_hand is None:
        raise ValueError("on_hand is required")
    if avg_daily_usage == 0:
        return None
    if on_hand <= 0:
        return 0.0
    return round(on_hand / avg_daily_usage, 2)


def classify_urgency(
    needs_reorder: bool,
    on_hand: float,
    days_left: Optional[float],
    lead_time_days: float,
    reorder_point_units: float,
    watch_margin: float = 0.10,
) -> Urgency:
    """Turn the numbers into an urgency tier.

    OUT_OF_STOCK  on hand <= 0
    CRITICAL      needs reorder AND days left < lead time
                  (it will run out before a new order can arrive)
    REORDER       needs reorder, safety stock still covers the wait
    WATCH         not yet, but within `watch_margin` above the reorder point
    SAFE          otherwise - no need to reorder right now
    """
    if needs_reorder:
        if on_hand <= 0:
            return Urgency.OUT_OF_STOCK
        if days_left is not None and days_left < lead_time_days:
            return Urgency.CRITICAL
        return Urgency.REORDER
    if reorder_point_units > 0 and on_hand <= reorder_point_units * (1 + watch_margin):
        return Urgency.WATCH
    return Urgency.SAFE


def break_even_lead_time(on_hand: float, safety_stock: float, avg_daily_usage: float) -> Optional[float]:
    """Solve the formula backwards: the lead time at which this item WOULD need a reorder.

    on_hand <= usage x LT + safety  <=>  LT >= (on_hand - safety) / usage
    Returns None when usage is 0 (lead time cannot change the decision).
    A value <= 0 means a reorder is needed whatever the lead time is.
    """
    _require_non_negative("avg_daily_usage", avg_daily_usage)
    _require_non_negative("safety_stock", safety_stock)
    if avg_daily_usage == 0:
        return None
    return round((on_hand - safety_stock) / avg_daily_usage, 2)


def _sort_key(item: ItemResult):
    days = item.days_until_stockout
    days_key = math.inf if days is None else days
    on_hand_key = item.on_hand if item.on_hand is not None else math.inf
    # Zero-usage items ("never runs out") always go to the very end.
    return (item.never_runs_out, days_key, on_hand_key, URGENCY_RANK[item.urgency], item.item_code)


def rank_by_urgency(items: Iterable[ItemResult]) -> list[ItemResult]:
    """Sort by days until stockout (fewest first).

    Zero-usage items always go last. Ties: more negative stock first, then
    urgency tier, then item code, so the order is fully deterministic.
    """
    return sorted(items, key=_sort_key)
