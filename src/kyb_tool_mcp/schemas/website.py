"""Website / domain schemas."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class WebsiteSnapshot(BaseModel):
    url: str
    final_url: str | None = None
    reachable: bool
    status_code: int | None = None
    error: str | None = None
    content_type: str | None = None
    title: str | None = None
    text: str = ""
    word_count: int = 0
    redirects: list[str] = Field(default_factory=list)
    truncated: bool = False
    blocked: bool = False  # the site answered but refused automated requests (401/403/429)


class DomainInfo(BaseModel):
    domain: str
    registered_on: date | None = None
    expires_on: date | None = None
    registrar: str | None = None
    found: bool = True


class WebsiteAssessment(BaseModel):
    """Structured LLM output. Quotes must appear verbatim in the fetched text."""

    sells: str = Field(description="One sentence: what the business appears to sell or do")
    industry_consistency: Literal["consistent", "inconsistent", "unclear"]
    confidence: float = Field(ge=0, le=1)
    prohibited_categories: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(
        default_factory=list, description="Short verbatim quotes from the page supporting the assessment"
    )
