"""Canonical registry records (what the *sources* show)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Address(BaseModel):
    lines: list[str] = Field(default_factory=list)
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None

    def one_line(self) -> str:
        parts = [*self.lines, self.locality, self.region, self.postal_code, self.country]
        return ", ".join(p for p in parts if p)


class Officer(BaseModel):
    name: str
    role: str = "director"
    appointed_on: date | None = None
    resigned_on: date | None = None
    birth_year: int | None = None
    birth_month: int | None = None
    nationality: str | None = None
    is_corporate: bool = False

    @property
    def active(self) -> bool:
        return self.resigned_on is None


class PSC(BaseModel):
    """Person (or entity) with Significant Control, i.e. a beneficial owner."""

    name: str
    kind: str = "individual"
    is_corporate: bool = False
    natures_of_control: list[str] = Field(default_factory=list)
    ownership_pct_min: float | None = None
    ownership_pct_max: float | None = None
    birth_year: int | None = None
    birth_month: int | None = None
    nationality: str | None = None
    notified_on: date | None = None
    ceased_on: date | None = None
    registration_number: str | None = None

    @property
    def active(self) -> bool:
        return self.ceased_on is None


class CompanyRecord(BaseModel):
    registry: str
    company_number: str
    name: str
    status: str
    incorporated_on: date | None = None
    dissolved_on: date | None = None
    company_type: str | None = None
    address: Address | None = None
    sic_codes: list[str] = Field(default_factory=list)
    previous_names: list[str] = Field(default_factory=list)
    officers: list[Officer] = Field(default_factory=list)
    pscs: list[PSC] = Field(default_factory=list)
    psc_statements: list[str] = Field(default_factory=list)
    officers_available: bool = True
    pscs_available: bool = True
    lei: str | None = None
    source_url: str | None = None
