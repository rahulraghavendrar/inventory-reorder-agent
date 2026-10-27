"""Structured-output schema for the Data Quality agent.

Gemini is forced to answer in this exact JSON shape, and Pydantic validates it,
so the result can go straight into a ticketing system or dashboard.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IssueAssessment(BaseModel):
    item_code: str = Field(description="Item code exactly as in the input")
    issue_codes: list[str] = Field(description="Issue codes copied from the input")
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    likely_causes: list[str] = Field(description="2-3 realistic causes")
    business_impact: str
    recommended_action: str
    owner: Literal["Warehouse", "Procurement", "Planning", "Master data"]
    needs_human_review: bool = True


class DataQualityAssessment(BaseModel):
    assessments: list[IssueAssessment]
    overall_note: str = Field(description="One sentence on overall data quality")
