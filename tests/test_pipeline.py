"""End-to-end on the real ex7_inventory.csv, checked against hand calculations."""
import pytest

from inventory_core import analyze_inventory
from inventory_core.models import Urgency

EXPECTED_ORDER = [
    # item,     reorder point, days left, urgency
    ("IT-113", 1458.1, 0.0, Urgency.OUT_OF_STOCK),
    ("IT-120", 193.6, 10.0, Urgency.CRITICAL),
    ("IT-103", 1036.2, 10.76, Urgency.CRITICAL),
    ("IT-121", 577.6, 12.57, Urgency.CRITICAL),
    ("IT-119", 761.2, 16.43, Urgency.REORDER),
    ("IT-122", 1984.0, 17.02, Urgency.CRITICAL),
    ("IT-116", 1755.0, 19.38, Urgency.CRITICAL),
    ("IT-111", 1343.0, 29.78, Urgency.CRITICAL),
    ("IT-101", 1931.0, 30.8, Urgency.REORDER),
]


@pytest.fixture(scope="module")
def report():
    from pathlib import Path
    return analyze_inventory(Path(__file__).resolve().parent.parent / "data" / "ex7_inventory.csv")


def test_reorder_list_exact_order_and_values(report):
    got = [(i.item_code, i.reorder_point, i.days_until_stockout, i.urgency) for i in report.reorder_list]
    assert got == EXPECTED_ORDER


def test_sorted_by_days_until_stockout(report):
    days = [i.days_until_stockout for i in report.reorder_list]
    assert days == sorted(days)


def test_hand_checked_example_it100_not_reordered(report):
    item = next(i for i in report.all_items if i.item_code == "IT-100")
    assert item.lead_time_demand == 700 and item.reorder_point == 850
    assert item.needs_reorder is False and item.urgency == Urgency.WATCH


def test_watch_list(report):
    assert [i.item_code for i in report.watch_list] == ["IT-115", "IT-100", "IT-112"]


def test_exceptions_are_the_three_planted_rows(report):
    assert [i.item_code for i in report.exceptions] == ["IT-106", "IT-113", "IT-118"]


def test_summary(report):
    s = report.summary
    assert (s.total_items, s.items_to_reorder, s.out_of_stock, s.critical, s.reorder) == (25, 9, 1, 6, 2)
    assert s.items_with_data_issues == 3


def test_it118_unique_lead_time_answer(report):
    item = next(i for i in report.all_items if i.item_code == "IT-118")
    a = item.lead_time_analysis
    assert a["known_lead_times_days"] == [7, 14, 21, 30, 45]
    assert (a["break_even_lead_time_days"], a["verdict"], a["confirm_lead_time_within_days"]) == (92.76, "ROBUST_SAFE", 47.8)
    assert item.urgency == Urgency.SAFE


def test_full_ranking_zero_usage_last_and_safe_tier(report):
    assert report.ranked_all[-1].item_code == "IT-106"
    assert report.summary.safe == 13
    assert len(report.ranked_all) == 25
