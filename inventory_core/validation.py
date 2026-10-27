"""Load the CSV and validate every row BEFORE any calculation.

Rules:
* No row is ever dropped silently: every problem becomes a DataIssue on the row.
* Source values are never overwritten (a -12 stays -12 in the output).
* "Blocking" issues stop the normal formula for that row; "warnings" don't.
"""
from __future__ import annotations

import csv
import math
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from .models import DataIssue, IssueCode, ItemResult

REQUIRED_COLUMNS = [
    "item_code",
    "description",
    "on_hand",
    "avg_daily_usage",
    "lead_time_days",
    "safety_stock",
]
NUMERIC_COLUMNS = ["on_hand", "avg_daily_usage", "lead_time_days", "safety_stock"]
# Columns where a negative value is impossible. on_hand is handled separately:
# negative stock is a known real-world situation (backorders / count errors).
NON_NEGATIVE_COLUMNS = ["avg_daily_usage", "lead_time_days", "safety_stock"]

_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


class SchemaError(ValueError):
    """The file does not have the columns we need."""


def parse_number(raw: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """Return (value, problem). problem is 'missing', 'invalid' or None."""
    if raw is None:
        return None, "missing"
    text = raw.strip()
    if text == "" or text.lower() in {"na", "n/a", "nan", "null", "none", "-"}:
        return None, "missing"
    if _THOUSANDS.match(text):
        text = text.replace(",", "")
    try:
        value = float(text)
    except ValueError:
        return None, "invalid"
    if math.isnan(value) or math.isinf(value):
        return None, "invalid"
    return value, None


def read_rows(csv_path: str | Path) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")
    # utf-8-sig strips the BOM that Excel adds to CSV exports
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise SchemaError("The file is empty")
        normalised = {name: name.strip().lower() for name in reader.fieldnames}
        missing = [c for c in REQUIRED_COLUMNS if c not in normalised.values()]
        if missing:
            raise SchemaError(f"Missing required column(s): {', '.join(missing)}")
        rows = []
        for raw in reader:
            rows.append({normalised[k]: (v if v is not None else "") for k, v in raw.items() if k in normalised})
        return rows


def validate_rows(rows: list[dict[str, str]]) -> list[ItemResult]:
    """Parse each row and attach every data issue found."""
    codes = Counter((r.get("item_code") or "").strip() for r in rows)
    items: list[ItemResult] = []

    for index, row in enumerate(rows):
        row_number = index + 2  # +1 for header, +1 for 1-based numbering
        issues: list[DataIssue] = []

        code = (row.get("item_code") or "").strip()
        if not code:
            code = f"ROW-{row_number}"
            issues.append(DataIssue(
                code=IssueCode.MISSING_ITEM_CODE, field="item_code", blocking=False,
                message=f"Item code is blank; identified as {code}",
            ))
        elif codes[code] > 1:
            issues.append(DataIssue(
                code=IssueCode.DUPLICATE_ITEM_CODE, field="item_code", blocking=False,
                message=f"Item code {code} appears on {codes[code]} rows; confirm which row is correct",
            ))

        description = (row.get("description") or "").strip()
        if not description:
            description = "(no description)"
            issues.append(DataIssue(
                code=IssueCode.MISSING_DESCRIPTION, field="description", blocking=False,
                message="Description is blank",
            ))

        values: dict[str, Optional[float]] = {}
        for column in NUMERIC_COLUMNS:
            value, problem = parse_number(row.get(column))
            if problem == "missing":
                issue_code = (
                    IssueCode.MISSING_LEAD_TIME if column == "lead_time_days"
                    else IssueCode.MISSING_VALUE
                )
                issues.append(DataIssue(
                    code=issue_code, field=column, blocking=True,
                    message=f"{column} is blank",
                ))
            elif problem == "invalid":
                issues.append(DataIssue(
                    code=IssueCode.INVALID_NUMBER, field=column, blocking=True,
                    message=f"{column} is not a number: {row.get(column)!r}",
                ))
            elif column in NON_NEGATIVE_COLUMNS and value < 0:
                issues.append(DataIssue(
                    code=IssueCode.NEGATIVE_VALUE, field=column, blocking=True,
                    message=f"{column} cannot be negative (value {value:g})",
                ))
                # keep the raw value for display, but it is not used in the formula
            values[column] = value

        if values["on_hand"] is not None and values["on_hand"] < 0:
            issues.append(DataIssue(
                code=IssueCode.NEGATIVE_STOCK, field="on_hand", blocking=False,
                message=(
                    f"Stock on hand is negative ({values['on_hand']:g}). Likely unfilled "
                    "backorders or a counting error. Treated as already out of stock; "
                    "a physical recount is needed."
                ),
            ))

        if values["avg_daily_usage"] == 0:
            issues.append(DataIssue(
                code=IssueCode.ZERO_USAGE, field="avg_daily_usage", blocking=False,
                message=(
                    "Average daily usage is 0, so this item never runs out. Reorder point "
                    "equals safety stock. Check whether it is dead stock or usage data is missing."
                ),
            ))

        items.append(ItemResult(
            row_number=row_number,
            item_code=code,
            description=description,
            on_hand=values["on_hand"],
            avg_daily_usage=values["avg_daily_usage"],
            lead_time_days=values["lead_time_days"],
            safety_stock=values["safety_stock"],
            issues=issues,
        ))
    return items
