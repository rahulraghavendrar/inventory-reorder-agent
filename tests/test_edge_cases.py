"""Every edge case: the three in the brief plus the ones a reviewer might try."""
import pytest

from inventory_core import SchemaError, analyze_inventory
from inventory_core.models import IssueCode, Urgency


def _item(report, code):
    return next(i for i in report.all_items if i.item_code == code)


def _codes(item):
    return {i.code for i in item.issues}


# ---- The three edge cases from the problem statement -----------------------
def test_zero_usage_never_runs_out_and_rop_equals_safety_stock(make_csv):
    report = analyze_inventory(make_csv("A,Calibration kit,875,0,21,77"))
    item = _item(report, "A")
    assert item.reorder_point == 77
    assert item.days_until_stockout is None and item.never_runs_out
    assert item.needs_reorder is False
    assert IssueCode.ZERO_USAGE in _codes(item)
    assert item in report.exceptions  # still sent for review


def test_zero_usage_below_safety_stock_is_reordered(make_csv):
    report = analyze_inventory(make_csv("A,Spare,10,0,21,77"))
    item = _item(report, "A")
    assert item.needs_reorder is True
    assert item.days_until_stockout is None  # does not crash, sorts last


def test_negative_stock_kept_as_is_and_ranked_first(make_csv):
    report = analyze_inventory(make_csv(
        "A,Normal,10,5,7,5",           # 2 days left
        "B,Data logger,-12,52.1,21,364",
    ))
    item = _item(report, "B")
    assert item.on_hand == -12                     # source value not overwritten
    assert item.days_until_stockout == 0.0
    assert item.urgency == Urgency.OUT_OF_STOCK
    assert IssueCode.NEGATIVE_STOCK in _codes(item)
    assert report.reorder_list[0].item_code == "B"


def test_missing_lead_time_robust_safe(make_csv):
    report = analyze_inventory(make_csv(
        "A,Other,100,1,7,10", "C,Other,100,1,45,10",
        "B,Battery pack,2494,25,,175",
    ))
    item = _item(report, "B")
    a = item.lead_time_analysis
    assert IssueCode.MISSING_LEAD_TIME in _codes(item)
    assert a["break_even_lead_time_days"] == 92.76          # (2494-175)/25
    assert a["scenarios_needing_reorder"] == 0 and a["verdict"] == "ROBUST_SAFE"
    assert a["confirm_lead_time_within_days"] == 47.8       # (2494-1300)/25
    assert item.needs_reorder is False and item.urgency == Urgency.SAFE
    assert item not in report.reorder_list and item in report.exceptions


def test_missing_lead_time_depends_is_provisional_reorder(make_csv):
    report = analyze_inventory(make_csv(
        "A,Other,100,1,7,10", "C,Other,100,1,45,10",
        "B,Battery pack,500,25,,175",   # break-even 13 days: safe at 7, reorder at 45
    ))
    item = _item(report, "B")
    assert item.lead_time_analysis["verdict"] == "DEPENDS_ON_LEAD_TIME"
    assert item.needs_reorder is True and item.provisional is True
    assert item in report.reorder_list                      # never silently dropped
    assert any("PROVISIONAL" in n for n in item.notes)


def test_missing_lead_time_robust_reorder(make_csv):
    report = analyze_inventory(make_csv(
        "A,Other,100,1,7,10", "C,Other,100,1,45,10",
        "B,Battery pack,100,25,,175",   # below safety stock: reorder at any lead time
    ))
    item = _item(report, "B")
    assert item.lead_time_analysis["verdict"] == "ROBUST_REORDER"
    assert item.needs_reorder is True and item.provisional is False


def test_negative_stock_action_is_order_immediately(make_csv):
    report = analyze_inventory(make_csv("B,Data logger,-12,52.1,21,364"))
    assert report.reorder_list[0].action.startswith("ORDER IMMEDIATELY")


def test_zero_usage_always_last_in_full_ranking(make_csv):
    report = analyze_inventory(make_csv(
        "Z,Zero usage,5,0,7,10",        # needs reorder (below safety) but never runs out
        "A,Slow,1000,1,7,5",            # 1000 days left
        "B,Fast,10,5,7,5",
    ))
    assert report.ranked_all[-1].item_code == "Z"
    assert report.reorder_list[-1].item_code == "Z"


# ---- Extra cases a reviewer might try --------------------------------------
@pytest.mark.parametrize(
    "row,code",
    [
        ("A,Bad,abc,5,7,5", IssueCode.INVALID_NUMBER),
        ("A,Bad,10,-5,7,5", IssueCode.NEGATIVE_VALUE),
        ("A,Bad,10,5,-7,5", IssueCode.NEGATIVE_VALUE),
        ("A,Bad,10,5,7,-5", IssueCode.NEGATIVE_VALUE),
        ("A,Bad,,5,7,5", IssueCode.MISSING_VALUE),
        ("A,Bad,10,N/A,7,5", IssueCode.MISSING_VALUE),
    ],
)
def test_invalid_values_go_to_review_not_crash(make_csv, row, code):
    report = analyze_inventory(make_csv(row))
    item = _item(report, "A")
    assert code in _codes(item)
    assert item.urgency == Urgency.REVIEW
    assert item.needs_reorder is None
    assert item not in report.reorder_list


def test_duplicate_item_codes_are_flagged(make_csv):
    report = analyze_inventory(make_csv("A,One,10,5,7,5", "A,Two,500,5,7,5"))
    assert all(IssueCode.DUPLICATE_ITEM_CODE in _codes(i) for i in report.all_items)


def test_blank_item_code_gets_row_identifier(make_csv):
    report = analyze_inventory(make_csv(",No code,10,5,7,5"))
    item = report.all_items[0]
    assert item.item_code == "ROW-2"
    assert IssueCode.MISSING_ITEM_CODE in _codes(item)


def test_thousands_separator_and_whitespace(make_csv):
    report = analyze_inventory(make_csv('A,Quoted," 1,200 ", 40 ,30,100'))
    item = _item(report, "A")
    assert item.on_hand == 1200 and item.avg_daily_usage == 40


def test_excel_bom_and_column_case(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_text(
        "﻿Item_Code,Description,On_Hand,Avg_Daily_Usage,Lead_Time_Days,Safety_Stock\nA,x,10,5,7,5\n",
        encoding="utf-8",
    )
    assert analyze_inventory(path).summary.total_items == 1


def test_missing_column_raises_clear_error(make_csv):
    path = make_csv("A,x,10,5,7", header="item_code,description,on_hand,avg_daily_usage,lead_time_days\n")
    with pytest.raises(SchemaError, match="safety_stock"):
        analyze_inventory(path)


def test_all_lead_times_missing_cannot_estimate(make_csv):
    report = analyze_inventory(make_csv("A,x,10,5,,5"))
    item = _item(report, "A")
    assert item.urgency == Urgency.REVIEW and item.provisional is False


def test_no_row_is_ever_dropped(make_csv):
    rows = ["A,ok,10,5,7,5", "B,bad,abc,5,7,5", "C,zero,10,0,7,5", "D,neg,-3,5,7,5", "E,nolt,10,5,,5"]
    report = analyze_inventory(make_csv(*rows))
    assert report.summary.total_items == len(rows)
    assert {i.item_code for i in report.all_items} == set("ABCDE")
