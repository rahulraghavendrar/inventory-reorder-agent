"""Command-line report - runs the deterministic pipeline, no API key needed.

Usage:
    python run_report.py                       # uses data/ex7_inventory.csv
    python run_report.py path/to/other.csv     # any inventory CSV
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from inventory_core import analyze_inventory
from inventory_core.models import AnalysisReport, ItemResult

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

CSV_COLUMNS = [
    "rank", "item_code", "description", "on_hand", "avg_daily_usage", "lead_time_days",
    "safety_stock", "lead_time_demand", "reorder_point", "units_below_reorder_point",
    "days_until_stockout", "urgency", "action", "provisional", "issues", "notes",
]


def _num(v):
    if v is None:
        return ""
    return int(v) if float(v).is_integer() else round(v, 2)


def _row(rank, item: ItemResult) -> dict:
    return {
        "rank": rank,
        "item_code": item.item_code,
        "description": item.description,
        "on_hand": _num(item.on_hand),
        "avg_daily_usage": _num(item.avg_daily_usage),
        "lead_time_days": _num(item.lead_time_days),
        "safety_stock": _num(item.safety_stock),
        "lead_time_demand": _num(item.lead_time_demand),
        "reorder_point": _num(item.reorder_point),
        "units_below_reorder_point": _num(item.units_below_reorder_point),
        "days_until_stockout": "never" if item.never_runs_out else _num(item.days_until_stockout),
        "urgency": item.urgency.value,
        "action": item.action,
        "provisional": "yes" if item.provisional else "",
        "issues": "; ".join(i.code.value for i in item.issues),
        "notes": " | ".join(item.notes),
    }


def write_csv(path: Path, items: list[ItemResult], ranked: bool = True) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for n, item in enumerate(items, start=1):
            writer.writerow(_row(n if ranked else "", item))


def markdown_report(report: AnalysisReport) -> str:
    s = report.summary
    lines = [
        "# Inventory Reorder Alert",
        "",
        f"Source: `{report.source_file}`",
        "",
        f"- Items checked: **{s.total_items}**",
        f"- Items to reorder: **{s.items_to_reorder}** "
        f"(out of stock {s.out_of_stock}, critical {s.critical}, reorder {s.reorder})",
        f"- Watch list (close to reorder point): **{s.watch}**",
        f"- Safe, no order needed now: **{s.safe}**",
        f"- Rows with data issues: **{s.items_with_data_issues}**",
        "",
        "## Reorder list (sorted by days until stockout)",
        "",
        "| # | Item | Description | On hand | Reorder point | Days left | Lead time | Urgency | Action |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, i in enumerate(report.reorder_list, start=1):
        days = "never" if i.never_runs_out else _num(i.days_until_stockout)
        lt = i.lead_time_days if i.lead_time_days is not None else f"{_num(i.assumed_lead_time_days)} (assumed)"
        lines.append(
            f"| {n} | {i.item_code} | {i.description} | {_num(i.on_hand)} | {_num(i.reorder_point)} "
            f"| {days} | {_num(lt) if not isinstance(lt, str) else lt} | {i.urgency.value} | {i.action} |"
        )
    lines += ["", "## Watch list", ""]
    if report.watch_list:
        for i in report.watch_list:
            lines.append(
                f"- {i.item_code} {i.description}: {_num(i.on_hand)} on hand vs reorder point "
                f"{_num(i.reorder_point)}"
            )
    else:
        lines.append("- None")
    lines += ["", "## Data-quality exceptions (need a person to check)", ""]
    for i in report.exceptions:
        codes = ", ".join(x.code.value for x in i.issues)
        lines.append(f"- **{i.item_code} {i.description}** [{codes}]: {' '.join(i.notes)}")
    lt_items = [i for i in report.all_items if i.lead_time_analysis]
    if lt_items:
        lines += ["", "## Missing lead time: decision-robustness check", ""]
        for i in lt_items:
            a = i.lead_time_analysis
            lines += [
                f"**{i.item_code} {i.description}** - verdict **{a['verdict']}**",
                "",
                f"- Break-even lead time = (on hand - safety stock) / usage = "
                f"({_num(i.on_hand)} - {_num(i.safety_stock)}) / {_num(i.avg_daily_usage)} = "
                f"**{a['break_even_lead_time_days']} days**",
                f"- Known lead times tested: {', '.join(str(_num(x)) for x in a['known_lead_times_days'])} days "
                f"-> reorder needed in {a['scenarios_needing_reorder']} of {a['scenarios_tested']}",
                f"- Safety factor vs worst case: {a['safety_factor_vs_worst_case']}x",
                f"- Confirm the real lead time within: {a['confirm_lead_time_within_days']} days",
                "",
                "| Lead time (days) | Reorder point | Reorder needed? |",
                "|---|---|---|",
            ] + [
                f"| {_num(r['lead_time_days'])} | {_num(r['reorder_point'])} | {'YES' if r['needs_reorder'] else 'no'} |"
                for r in a["scenario_results"]
            ] + [""]
    lines += ["", "## Full ranking (all items, zero-usage last)", "",
              "| # | Item | Days left | Urgency |", "|---|---|---|---|"]
    for n, i in enumerate(report.ranked_all, start=1):
        days = "never" if i.never_runs_out else _num(i.days_until_stockout)
        lines.append(f"| {n} | {i.item_code} {i.description} | {days} | {i.urgency.value} |")
    lines += ["", "## Assumptions", ""] + [f"- {a}" for a in report.assumptions]
    return "\n".join(lines) + "\n"


def print_full_list(report: AnalysisReport) -> None:
    """All items, sorted by days until stockout (zero-usage items last)."""
    print(f"\nALL {report.summary.total_items} ITEMS, sorted by days until stockout "
          "(zero-usage items last):\n")
    header = (f"{'#':>2}  {'Item':7} {'Description':24} {'On hand':>8} {'Usage':>6} {'LT':>5} "
              f"{'Safety':>6} {'ROP':>8} {'Days':>6}  {'Reorder?':8} Urgency")
    print(header)
    print("-" * len(header))
    for n, i in enumerate(report.ranked_all, start=1):
        days = "never" if i.never_runs_out else (
            "-" if i.days_until_stockout is None else f"{i.days_until_stockout:.1f}")
        lt = (f"{i.lead_time_days:g}" if i.lead_time_days is not None
              else "blank")
        rop = "-" if i.reorder_point is None else f"{i.reorder_point:.1f}"
        reorder = {True: "YES", False: "no", None: "review"}[i.needs_reorder]
        print(
            f"{n:>2}  {i.item_code:7} {i.description[:24]:24} {_fmt_num(i.on_hand):>8} "
            f"{_fmt_num(i.avg_daily_usage):>6} {lt:>5} {_fmt_num(i.safety_stock):>6} {rop:>8} "
            f"{days:>6}  {reorder:8} {i.urgency.value}"
        )
    for i in report.all_items:
        if i.lead_time_analysis:
            print(f"\nNote: {i.item_code} lead time is blank; its ROP shown uses the worst-case "
                  f"{i.assumed_lead_time_days:g} days (see break-even check below).")


def _fmt_num(v) -> str:
    if v is None:
        return "-"
    return f"{v:g}"


def print_table(report: AnalysisReport) -> None:
    print(f"\nReorder list ({report.summary.items_to_reorder} items), sorted by days until stockout:\n")
    header = f"{'#':>2}  {'Item':7} {'Description':24} {'On hand':>8} {'ROP':>8} {'Days':>6} {'LT':>4}  Urgency"
    print(header)
    print("-" * len(header))
    for n, i in enumerate(report.reorder_list, start=1):
        days = "never" if i.never_runs_out else f"{i.days_until_stockout:.1f}"
        lt = i.lead_time_days if i.lead_time_days is not None else i.assumed_lead_time_days
        flag = " (provisional)" if i.provisional else ""
        print(
            f"{n:>2}  {i.item_code:7} {i.description[:24]:24} {i.on_hand:>8g} "
            f"{i.reorder_point:>8.1f} {days:>6} {lt:>4g}  {i.urgency.value}{flag}"
        )
    print(f"\nWatch list: {', '.join(i.item_code for i in report.watch_list) or 'none'}")
    print("\nData-quality exceptions:")
    for i in report.exceptions:
        print(f"  - {i.item_code} [{', '.join(x.code.value for x in i.issues)}] -> {i.urgency.value}: {i.action}")
        if i.lead_time_analysis:
            a = i.lead_time_analysis
            print(f"      break-even lead time {a['break_even_lead_time_days']} d, "
                  f"reorder in {a['scenarios_needing_reorder']}/{a['scenarios_tested']} known scenarios "
                  f"-> {a['verdict']}, confirm within {a['confirm_lead_time_within_days']} d")
    print(f"\nSafe (no order needed now): {report.summary.safe} items; zero-usage items listed last")


def main(argv: list[str]) -> int:
    csv_path = Path(argv[1]) if len(argv) > 1 else ROOT / "data" / "ex7_inventory.csv"
    report = analyze_inventory(csv_path)
    OUTPUT_DIR.mkdir(exist_ok=True)
    write_csv(OUTPUT_DIR / "reorder_list.csv", report.reorder_list)
    write_csv(OUTPUT_DIR / "watch_list.csv", report.watch_list)
    write_csv(OUTPUT_DIR / "exceptions.csv", report.exceptions, ranked=False)
    write_csv(OUTPUT_DIR / "ranked_all_items.csv", report.ranked_all)
    write_csv(OUTPUT_DIR / "all_items.csv", sorted(report.all_items, key=lambda i: i.row_number), ranked=False)
    (OUTPUT_DIR / "report.md").write_text(markdown_report(report), encoding="utf-8")
    (OUTPUT_DIR / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    print_full_list(report)
    print_table(report)
    print(f"\nFiles written to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
