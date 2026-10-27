"""Steps 1-4 as pure functions."""
import pytest

from inventory_core import calculations as calc
from inventory_core.models import Urgency


# Step 1 ---------------------------------------------------------------------
def test_lead_time_demand_basic():
    assert calc.lead_time_demand(50, 14) == 700


def test_lead_time_demand_has_no_float_noise():
    # 43.2 * 21 is 907.2000000000001 in raw floating point
    assert calc.lead_time_demand(43.2, 21) == 907.2


@pytest.mark.parametrize("usage,lt", [(-1, 5), (5, -1), (None, 5), (float("nan"), 5), ("10", 5)])
def test_lead_time_demand_rejects_bad_input(usage, lt):
    with pytest.raises(ValueError):
        calc.lead_time_demand(usage, lt)


# Step 2 ---------------------------------------------------------------------
def test_reorder_point():
    assert calc.reorder_point(700, 150) == 850


def test_reorder_point_rejects_negative_safety_stock():
    with pytest.raises(ValueError):
        calc.reorder_point(700, -1)


# Step 3 ---------------------------------------------------------------------
def test_check_reorder_at_boundary_is_reorder():
    assert calc.check_reorder(850, 850) is True  # "at or below"


def test_check_reorder_above():
    assert calc.check_reorder(855, 850) is False


def test_check_reorder_negative_stock_is_below():
    assert calc.check_reorder(-12, 1458.1) is True


def test_units_below():
    assert calc.units_below(465, 1036.2) == 571.2
    assert calc.units_below(900, 850) == 0


# Step 4 ---------------------------------------------------------------------
def test_days_until_stockout_normal():
    assert calc.days_until_stockout(114, 11.4) == 10.0


def test_days_until_stockout_zero_usage_is_never():
    assert calc.days_until_stockout(875, 0) is None


def test_days_until_stockout_negative_stock_is_zero():
    assert calc.days_until_stockout(-12, 52.1) == 0.0


@pytest.mark.parametrize(
    "needs,on_hand,days,lt,rop,expected",
    [
        (True, -12, 0.0, 21, 1458.1, Urgency.OUT_OF_STOCK),
        (True, 465, 10.76, 21, 1036.2, Urgency.CRITICAL),   # runs out before delivery
        (True, 736, 16.43, 14, 761.2, Urgency.REORDER),     # safety stock still covers it
        (False, 855, 17.1, 14, 850, Urgency.WATCH),         # within 10% above
        (False, 3539, 63.54, 30, 1838, Urgency.SAFE),
    ],
)
def test_classify_urgency(needs, on_hand, days, lt, rop, expected):
    assert calc.classify_urgency(needs, on_hand, days, lt, rop) == expected


def test_break_even_lead_time():
    # IT-118: (2494 - 175) / 25
    assert calc.break_even_lead_time(2494, 175, 25) == 92.76
    assert calc.break_even_lead_time(100, 175, 25) < 0      # below safety stock: any lead time
    assert calc.break_even_lead_time(875, 77, 0) is None    # zero usage: lead time irrelevant
