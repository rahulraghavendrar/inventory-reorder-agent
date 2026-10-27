import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HEADER = "item_code,description,on_hand,avg_daily_usage,lead_time_days,safety_stock\n"


@pytest.fixture
def data_file():
    return ROOT / "data" / "ex7_inventory.csv"


@pytest.fixture
def make_csv(tmp_path):
    """Write a small CSV with the standard header and return its path."""

    def _make(*rows: str, header: str = HEADER, name: str = "test.csv"):
        path = tmp_path / name
        path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
        return path

    return _make
