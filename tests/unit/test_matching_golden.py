"""Golden screening cases against the mini index (fixtures/sanctions_mini)."""

from __future__ import annotations

import pytest

from kyb_tool_mcp.config import load_policy
from kyb_tool_mcp.matching.adjudicate import adjudicate
from kyb_tool_mcp.matching.blocking import generate_candidates
from kyb_tool_mcp.matching.index import SanctionsIndex
from kyb_tool_mcp.schemas.screening import HitOutcome, ScreeningSubject

POLICY = load_policy().screening


def outcomes(index: SanctionsIndex, **subject: object) -> dict[str, HitOutcome]:
    s = ScreeningSubject.model_validate(subject)
    return {c.entity.id: adjudicate(s, c, POLICY)[0] for c in generate_candidates(index, s, POLICY)}


GOLDEN = [
    # (description, subject, entity id, expected outcome)
    (
        "exact name + DOB",
        dict(name="Viktor Ivanovich Borovkov", birth_year=1961, birth_month=3),
        "ofac-1001",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "Companies House order",
        dict(name="BOROVKOV, Viktor Ivanovich", birth_year=1961),
        "ofac-1001",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "Cyrillic transliteration",
        dict(name="Виктор Иванович Боровков", birth_year=1961),
        "ofac-1001",
        HitOutcome.TRUE_MATCH,
    ),
    ("strong alias", dict(name="Viktor Borovkoff", birth_year=1961), "ofac-1001", HitOutcome.TRUE_MATCH),
    ("dropped middle name, no DOB", dict(name="Viktor Borovkov"), "ofac-1001", HitOutcome.NEEDS_REVIEW),
    ("typo", dict(name="Viktor Ivanovich Borovkof", birth_year=1961), "ofac-1001", HitOutcome.TRUE_MATCH),
    (
        "DOB conflict clears",
        dict(name="Viktor Borovkov", birth_year=1990),
        "ofac-1001",
        HitOutcome.FALSE_POSITIVE,
    ),
    (
        "DOB off by one -> review",
        dict(name="Viktor Ivanovich Borovkov", birth_year=1962),
        "ofac-1001",
        HitOutcome.NEEDS_REVIEW,
    ),
    (
        "spelling variant Muhammad",
        dict(name="Muhammad Abdullah Al Rashidi", birth_year=1975),
        "ofac-1003",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "particle merged",
        dict(name="Mohammed Abdullah Alrashidi", birth_year=1975),
        "ofac-1003",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "partial DOB year+month",
        dict(name="David Hargreaves", birth_year=1958, birth_month=6),
        "ofac-1005",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "same name, different person",
        dict(name="David Hargreaves", birth_year=1987, nationality="GB"),
        "ofac-1005",
        HitOutcome.FALSE_POSITIVE,
    ),
    (
        "DOB range on list",
        dict(name="Aleksandr Petrovich Volkov", birth_year=1961),
        "un-2003",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "no DOB on list -> review",
        dict(name="Elena Korzhakova", birth_year=1970),
        "ofac-1006",
        HitOutcome.NEEDS_REVIEW,
    ),
    (
        "company + legal suffix",
        dict(name="Nordstar Shipping Ltd", type="company"),
        "ofac-1002",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "company alias",
        dict(name="Nord Star Maritime Company", type="company"),
        "ofac-1002",
        HitOutcome.TRUE_MATCH,
    ),
    (
        "UN entity",
        dict(name="Golden Crescent Trading Company", type="company"),
        "un-2002",
        HitOutcome.TRUE_MATCH,
    ),
    ("person vs vessel schema", dict(name="Ocean Pearl"), "ofac-1004", HitOutcome.FALSE_POSITIVE),
]


@pytest.mark.parametrize(("desc", "subject", "entity_id", "expected"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden(
    mini_index: SanctionsIndex, desc: str, subject: dict[str, object], entity_id: str, expected: HitOutcome
) -> None:
    got = outcomes(mini_index, **subject)
    assert entity_id in got, f"{desc}: {entity_id} not a candidate; got {got}"
    assert got[entity_id] == expected, f"{desc}: {got}"


@pytest.mark.parametrize(
    "subject",
    [
        dict(name="Sarah Jane Holloway", birth_year=1979),
        dict(name="Brightwater Analytics", type="company"),
        dict(name="Daniel Okafor"),
        dict(name="Kim Smith", birth_year=1980),
    ],
)
def test_clean_names_produce_no_alerts(mini_index: SanctionsIndex, subject: dict[str, object]) -> None:
    got = outcomes(mini_index, **subject)
    assert all(o == HitOutcome.FALSE_POSITIVE for o in got.values()), got


def test_weak_alias_never_confirms(mini_index: SanctionsIndex) -> None:
    # An exact weak-alias hit with matching DOB is at most NEEDS_REVIEW, never TRUE_MATCH.
    got = outcomes(mini_index, name="Chol", birth_year=1968)
    assert got.get("un-2001") == HitOutcome.NEEDS_REVIEW
    # ...and a near-miss on a weak alias is auto-cleared.
    got = outcomes(mini_index, name="Cholo", birth_year=1968)
    assert got.get("un-2001", HitOutcome.FALSE_POSITIVE) == HitOutcome.FALSE_POSITIVE


def test_index_meta(mini_index: SanctionsIndex) -> None:
    assert mini_index.meta["sources"] == {"ofac_sdn": 6, "un_consolidated": 3}
    ent = mini_index.get_entity("ofac-1001")
    assert ent is not None and ent.weak_names == ["THE BEAR"] and str(ent.birth_dates[0]) == "1961-03-12"
