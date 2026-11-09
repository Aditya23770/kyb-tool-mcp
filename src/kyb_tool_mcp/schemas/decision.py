"""Decision output schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from kyb_tool_mcp.schemas.screening import HitOutcome

Decision = Literal["APPROVE", "REJECT", "ESCALATE"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]
CheckName = Literal["registry", "ownership", "sanctions", "pep", "website", "industry"]

DISCLAIMER = (
    "Decision support only, not legal advice. You remain responsible for your compliance obligations."
)


class Severity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Flag(BaseModel):
    code: str = Field(description="e.g. UNDECLARED_DIRECTOR")
    severity: Severity
    detail: str
    evidence: list[str] = Field(default_factory=list, description="Evidence ids; must exist in the store")
    check: str | None = None


class CheckStatus(BaseModel):
    check: CheckName
    status: Literal["done", "not_configured", "error", "skipped"]
    providers: list[str] = Field(default_factory=list)
    note: str | None = None


class Hit(BaseModel):
    subject: str
    subject_role: str | None = None
    subject_type: str = "person"
    entity_id: str
    source: str
    matched_name: str
    caption: str
    topics: list[str] = Field(default_factory=list)
    programs: list[str] = Field(default_factory=list)
    name_score: float
    score: float
    outcome: HitOutcome
    reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    explanation: dict[str, Any] | None = None


class AmlInfo(BaseModel):
    result: Literal["clear", "hits", "not_screened", "error"] = "not_screened"
    num_subjects: int = 0
    num_hits: int = 0
    hits: list[Hit] = Field(default_factory=list, description="TRUE_MATCH and NEEDS_REVIEW hits")
    cleared: list[Hit] = Field(default_factory=list, description="Auto-cleared false positives with reasons")


class TraceStep(BaseModel):
    step: str
    ms: float
    detail: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class ReviewResult(BaseModel):
    case_id: str
    decision: Decision
    risk_level: RiskLevel
    score: int
    flags: list[Flag]
    checks: list[CheckStatus]
    aml_info: AmlInfo
    analyst_note: str | None = None
    policy_version: str
    kyb_tool_version: str
    evidence: dict[str, Any] = Field(default_factory=dict, description="Evidence summaries keyed by id")
    trace: list[TraceStep] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER
