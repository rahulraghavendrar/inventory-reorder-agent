"""Hallucination guardrail: every number an LLM writes must be grounded.

A number in the model's answer is accepted only if it matches (allowing for
normal rounding) a number that came from a tool result, from the user's own
message, or from the deterministic analysis of the file. Anything else is
reported as unverified.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Item codes like IT-113 / ROW-5 are identifiers, not quantities.
_ITEM_CODE = re.compile(r"\b[A-Z]{2,}-\d+\b")
# Markdown list numbering "1." / "2)" at line start.
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """Pull numeric values out of free text, ignoring item codes and list markers."""
    cleaned = _ITEM_CODE.sub(" ", text)
    cleaned = _LIST_MARKER.sub(" ", cleaned)
    values = []
    for token in _NUMBER.findall(cleaned):
        token = token.rstrip(",")
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def collect_numbers(obj: Any) -> set[float]:
    """Recursively collect every number found in a tool result / dict / string."""
    found: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return found
    if isinstance(obj, (int, float)):
        found.add(float(obj))
    elif isinstance(obj, str):
        found.update(extract_numbers(obj))
    elif isinstance(obj, dict):
        for value in obj.values():
            found |= collect_numbers(value)
    elif isinstance(obj, (list, tuple, set)):
        for value in obj:
            found |= collect_numbers(value)
    else:
        try:
            found |= collect_numbers(json.loads(json.dumps(obj, default=str)))
        except (TypeError, ValueError):
            pass
    return found


def expand_with_rounding(values: Iterable[float]) -> set[float]:
    """Accept the usual ways a value gets rounded or negated when written in prose."""
    expanded: set[float] = set()
    for v in values:
        for candidate in (v, abs(v), round(v), round(v, 1), round(v, 2), round(abs(v)), round(abs(v), 1)):
            expanded.add(float(candidate))
    return expanded


# Small counts and percentages are fine in prose ("2 items", "10%").
ALWAYS_ALLOWED = {0.0, 1.0, 2.0, 3.0, 100.0}


@dataclass
class GuardrailResult:
    passed: bool
    unverified: list[float] = field(default_factory=list)
    checked: int = 0


def verify_numbers(text: str, grounded: Iterable[float]) -> GuardrailResult:
    allowed = expand_with_rounding(grounded) | ALWAYS_ALLOWED
    numbers = extract_numbers(text)
    unverified = []
    for n in numbers:
        if n in allowed or any(abs(n - a) <= 0.051 for a in allowed):
            continue
        if n not in unverified:
            unverified.append(n)
    return GuardrailResult(passed=not unverified, unverified=unverified, checked=len(numbers))


def warning_text(result: GuardrailResult) -> str:
    nums = ", ".join(f"{n:g}" for n in result.unverified)
    return (
        "\n\n> **Guardrail warning:** these numbers could not be traced to the calculated "
        f"data and should not be trusted: {nums}. Please re-check with the reorder tools."
    )
