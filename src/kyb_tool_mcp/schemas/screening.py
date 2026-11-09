"""Sanctions / PEP screening schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SubjectType = Literal["person", "company"]


class PartialDate(BaseModel):
    """A date that may be known only to the year, or year + month."""

    year: int
    month: int | None = None
    day: int | None = None

    def __str__(self) -> str:
        if self.month and self.day:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        if self.month:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}"


class ScreeningSubject(BaseModel):
    name: str = Field(min_length=1)
    type: SubjectType = "person"
    birth_year: int | None = None
    birth_month: int | None = None
    nationality: str | None = None
    role: str | None = Field(default=None, description="company | director | ubo | psc")


class ListEntity(BaseModel):
    """An entry on a sanctions / PEP / crime list, normalised across sources."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    source: str
    schema_: str = Field(alias="schema", default="Person")
    caption: str
    names: list[str] = Field(default_factory=list)
    weak_names: list[str] = Field(default_factory=list)
    birth_dates: list[PartialDate] = Field(default_factory=list)
    nationalities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=lambda: ["sanction"])
    programs: list[str] = Field(default_factory=list)
    url: str | None = None

    @property
    def is_person(self) -> bool:
        return self.schema_.lower() == "person"


class Candidate(BaseModel):
    entity: ListEntity
    matched_name: str
    name_score: float
    features: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0


class HitOutcome(StrEnum):
    TRUE_MATCH = "TRUE_MATCH"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
