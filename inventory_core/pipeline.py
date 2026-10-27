"""Full pipeline: load -> validate -> calculate (steps 1-4) -> report.

Edge-case policy lives here, in one place, so it is easy to explain and test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import calculations as calc
from .models import (
    ACTIONS,
    AnalysisReport,
    AnalysisSummary,
    IssueCode,
    ItemResult,
    Urgency,
)
from .validation import read_rows, validate_rows

DEFAULT_WATCH_MARGIN = 0.10


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.1f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value):,}"


def worst_case_lead_time(items: list[ItemResult]) -> Optional[float]:
    """Longest valid lead time in the file - used when a lead time is missing."""
    valid = [
        i.lead_time_days for i in items
        if i.lead_time_days is not None and i.lead_time_days >= 0
    ]
    return max(valid) if valid else None


def known_lead_times(items: list[ItemResult]) -> list[float]:
    """Distinct valid lead times in the file = the realistic scenarios."""
    return sorted({
        i.lead_time_days for i in items
        if i.lead_time_days is not None and i.lead_time_days >= 0
    })


def _robustness_check(item: ItemResult, scenarios: list[float], watch_margin: float) -> None:
    """Missing lead time: test the decision against every lead time seen in the file.

    1. Break-even lead time = (on_hand - safety_stock) / usage: the lead time at
       which this item WOULD need a reorder.
    2. Run the reorder check for every known lead time (the realistic scenarios).
    3. Verdict:
       ROBUST_SAFE          no scenario needs a reorder -> safe, no guessing needed
       ROBUST_REORDER       every scenario needs a reorder -> order, no guessing needed
       DEPENDS_ON_LEAD_TIME answer changes with the lead time -> provisional reorder
                            using the worst case, confirm the lead time urgently
    4. Confirm-by: how many days until even the worst case would trigger a reorder.
    """
    usage, on_hand, safety = item.avg_daily_usage, item.on_hand, item.safety_stock
    worst = max(scenarios)
    results = []
    for lt in scenarios:
        rop = calc.reorder_point(calc.lead_time_demand(usage, lt), safety)
        results.append({"lead_time_days": lt, "reorder_point": rop,
                        "needs_reorder": calc.check_reorder(on_hand, rop)})
    n_reorder = sum(r["needs_reorder"] for r in results)
    break_even = calc.break_even_lead_time(on_hand, safety, usage)

    if n_reorder == 0:
        verdict = "ROBUST_SAFE"
    elif n_reorder == len(results):
        verdict = "ROBUST_REORDER"
    else:
        verdict = "DEPENDS_ON_LEAD_TIME"

    worst_rop = results[-1]["reorder_point"]
    confirm_within = None
    if usage > 0 and on_hand > worst_rop:
        confirm_within = round((on_hand - worst_rop) / usage, 1)

    item.assumed_lead_time_days = worst
    item.lead_time_demand = calc.lead_time_demand(usage, worst)
    item.reorder_point = worst_rop
    item.needs_reorder = verdict != "ROBUST_SAFE"
    item.units_below_reorder_point = calc.units_below(on_hand, worst_rop)
    item.provisional = verdict == "DEPENDS_ON_LEAD_TIME"
    item.urgency = calc.classify_urgency(
        item.needs_reorder, on_hand, item.days_until_stockout, worst, worst_rop, watch_margin,
    )
    item.lead_time_analysis = {
        "method": "decision robustness (break-even lead time + scenario test)",
        "break_even_lead_time_days": break_even,
        "known_lead_times_days": scenarios,
        "scenarios_needing_reorder": n_reorder,
        "scenarios_tested": len(results),
        "safety_factor_vs_worst_case": (
            round(break_even / worst, 2) if break_even is not None and worst > 0 else None
        ),
        "verdict": verdict,
        "confirm_lead_time_within_days": confirm_within,
        "scenario_results": results,
    }

    if verdict == "ROBUST_SAFE":
        be_text = (
            f"a reorder would only be needed if the lead time were above {_fmt(break_even)} days"
            if break_even is not None else "with zero usage the lead time cannot change the decision"
        )
        item.notes.append(
            f"Lead time missing - not guessed. Break-even analysis: {be_text}. "
            f"The longest lead time in the file is {_fmt(worst)} days, so it is safe in all "
            f"{len(results)} known scenarios. Confirm the real lead time within "
            f"{_fmt(confirm_within)} days (before stock reaches the worst-case reorder point "
            f"of {_fmt(worst_rop)})."
        )
        item.action = (
            f"NO ORDER NEEDED - safe for any known lead time; confirm lead time with "
            f"supplier within {_fmt(confirm_within)} days"
        )
    elif verdict == "ROBUST_REORDER":
        item.notes.append(
            f"Lead time missing - not needed: stock is below the reorder point in all "
            f"{len(results)} known lead-time scenarios (break-even {_fmt(break_even)} days). "
            "Order regardless of the lead time; urgency shown uses the worst case."
        )
    else:
        item.notes.append(
            f"PROVISIONAL: lead time missing and the decision depends on it (needs a reorder in "
            f"{n_reorder} of {len(results)} known scenarios; break-even {_fmt(break_even)} days). "
            f"Reorder flagged using the worst case ({_fmt(worst)} days) so it is not missed; "
            "confirm the lead time with the supplier today."
        )


def evaluate_item(
    item: ItemResult,
    known_lts: list[float],
    watch_margin: float = DEFAULT_WATCH_MARGIN,
) -> ItemResult:
    """Apply steps 1-4 to one validated row, following the edge-case policy."""
    blocking_codes = {i.code for i in item.issues if i.blocking}
    usage_ok = item.avg_daily_usage is not None and item.avg_daily_usage >= 0
    stock_ok = item.on_hand is not None
    safety_ok = item.safety_stock is not None and item.safety_stock >= 0

    # Days left only needs stock + usage, so compute it whenever possible.
    if usage_ok and stock_ok:
        item.days_until_stockout = calc.days_until_stockout(item.on_hand, item.avg_daily_usage)
        item.never_runs_out = item.days_until_stockout is None

    # ---- Case A: normal row (no blocking issue) --------------------------------
    if not blocking_codes:
        item.lead_time_demand = calc.lead_time_demand(item.avg_daily_usage, item.lead_time_days)
        item.reorder_point = calc.reorder_point(item.lead_time_demand, item.safety_stock)
        item.needs_reorder = calc.check_reorder(item.on_hand, item.reorder_point)
        item.units_below_reorder_point = calc.units_below(item.on_hand, item.reorder_point)
        item.urgency = calc.classify_urgency(
            item.needs_reorder, item.on_hand, item.days_until_stockout,
            item.lead_time_days, item.reorder_point, watch_margin,
        )
        if item.never_runs_out:
            item.notes.append(
                f"No usage recorded: reorder point = safety stock ({_fmt(item.safety_stock)}). "
                "Never runs out at current usage; review for dead stock."
            )
        if item.on_hand < 0:
            item.notes.append(
                f"Negative stock ({_fmt(item.on_hand)}) treated as already out of stock; "
                f"order must also cover the {_fmt(-item.on_hand)} backordered units."
            )
        return item

    # ---- Case B: only the lead time is missing -> decision-robustness check ---
    # Instead of guessing the missing value, ask: does it change the decision?
    if blocking_codes == {IssueCode.MISSING_LEAD_TIME} and usage_ok and stock_ok and safety_ok:
        if not known_lts:
            item.urgency = Urgency.REVIEW
            item.notes.append("Lead time missing and no other lead times to compare against.")
            return item
        _robustness_check(item, known_lts, watch_margin)
        return item

    # ---- Case C: anything else blocking -> needs a person ---------------------
    item.urgency = Urgency.REVIEW
    item.needs_reorder = None
    problems = "; ".join(i.message for i in item.issues if i.blocking)
    item.notes.append(f"Cannot calculate reorder point until data is fixed: {problems}.")
    return item


def build_report(
    items: list[ItemResult],
    source_file: str,
    watch_margin: float = DEFAULT_WATCH_MARGIN,
) -> AnalysisReport:
    worst_lt = worst_case_lead_time(items)
    scenarios = known_lead_times(items)
    evaluated = [evaluate_item(i, scenarios, watch_margin) for i in items]
    for i in evaluated:
        i.action = i.action or ACTIONS[i.urgency]

    reorder_list = calc.rank_by_urgency([i for i in evaluated if i.needs_reorder])
    watch_list = calc.rank_by_urgency([i for i in evaluated if i.urgency == Urgency.WATCH])
    exceptions = sorted([i for i in evaluated if i.issues], key=lambda i: i.row_number)
    ranked_all = calc.rank_by_urgency(evaluated)

    summary = AnalysisSummary(
        total_items=len(evaluated),
        items_to_reorder=len(reorder_list),
        out_of_stock=sum(i.urgency == Urgency.OUT_OF_STOCK for i in reorder_list),
        critical=sum(i.urgency == Urgency.CRITICAL for i in reorder_list),
        reorder=sum(i.urgency == Urgency.REORDER for i in reorder_list),
        watch=len(watch_list),
        safe=sum(i.urgency == Urgency.SAFE for i in evaluated),
        items_with_data_issues=len(exceptions),
        provisional_items=sum(i.provisional for i in evaluated),
    )

    assumptions = [
        "Reorder point = average daily usage x lead time (days) + safety stock.",
        "An item is reordered when stock on hand is AT or BELOW its reorder point.",
        "Days until stockout = on hand / average daily usage; 0 if stock is zero or negative; "
        "'never' if usage is zero.",
        "The file has no 'quantity already on order' column, so stock position = on hand only.",
        "CRITICAL = days until stockout is less than the lead time (it will run out before a "
        "new order can arrive, even if ordered today).",
        f"WATCH = not yet at the reorder point but within {int(watch_margin * 100)}% above it.",
        "Negative stock is kept as-is in the data and treated as already out of stock.",
        "A missing lead time is never guessed. The formula is solved backwards for the "
        "break-even lead time and the decision is tested against every lead time in the file: "
        "ROBUST_SAFE / ROBUST_REORDER need no guess; DEPENDS_ON_LEAD_TIME is reordered "
        "provisionally at the worst case. A confirm-by deadline is given in every case.",
        "Items with zero usage never run out and are always listed last.",
        "SAFE = no need to reorder right now.",
    ]

    return AnalysisReport(
        source_file=str(source_file),
        summary=summary,
        reorder_list=reorder_list,
        watch_list=watch_list,
        ranked_all=ranked_all,
        exceptions=exceptions,
        all_items=evaluated,
        assumptions=assumptions,
    )


def analyze_inventory(
    csv_path: str | Path,
    watch_margin: float = DEFAULT_WATCH_MARGIN,
) -> AnalysisReport:
    """Load, validate and analyse an inventory CSV."""
    rows = read_rows(csv_path)
    items = validate_rows(rows)
    return build_report(items, Path(csv_path).name, watch_margin)
