"""The onboarding application: what the business *claims*."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Person(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, description="Full name as declared")
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    birth_month: int | None = Field(default=None, ge=1, le=12)
    nationality: str | None = Field(default=None, description="ISO-3166 alpha-2, e.g. GB")

    @field_validator("nationality")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v


class DeclaredUBO(Person):
    ownership_pct: float | None = Field(default=None, ge=0, le=100)


class Application(BaseModel):
    """A KYB onboarding application. Only `case_id`, `legal_name` and `country` are required."""

    model_config = ConfigDict(extra="ignore")

    case_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    trading_name: str | None = None
    country: str = Field(default="GB", description="ISO-3166 alpha-2 country of registration")
    registration_number: str | None = None
    registered_address: str | None = None
    postcode: str | None = None
    declared_industry: str | None = None
    website: str | None = Field(default=None, description="Business website URL")
    directors: list[Person] = Field(default_factory=list)
    ubos: list[DeclaredUBO] = Field(default_factory=list)

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        v = v.strip().upper()
        return "GB" if v == "UK" else v

    @field_validator("registration_number")
    @classmethod
    def _regno(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip().upper().replace(" ", "")
        # Companies House numbers are 8 chars, zero padded when numeric.
        if v.isdigit() and len(v) < 8:
            v = v.zfill(8)
        return v

    @field_validator("website")
    @classmethod
    def _website(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.strip()
        if "://" not in v:
            v = "https://" + v
        return v
