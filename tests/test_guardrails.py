from inventory_core.guardrails import collect_numbers, extract_numbers, verify_numbers


def test_item_codes_and_list_markers_are_not_numbers():
    text = "1. IT-113 has 0 days left\n2. IT-120 needs 193.6"
    assert extract_numbers(text) == [0.0, 193.6]


def test_thousands_separators():
    assert extract_numbers("reorder point 1,458.1 units") == [1458.1]


def test_grounded_numbers_pass_with_rounding():
    grounded = collect_numbers({"days": 10.76, "rop": 1036.2, "lt": 21})
    assert verify_numbers("IT-103: 10.8 days left (about 11), ROP 1,036.2, lead time 21", grounded).passed


def test_invented_numbers_fail():
    result = verify_numbers("IT-120 has 6.5 days left, reorder point 999", {10.0, 193.6})
    assert not result.passed
    assert result.unverified == [6.5, 999.0]


def test_negative_stock_written_without_sign_passes():
    assert verify_numbers("12 units are backordered", {-12.0}).passed
