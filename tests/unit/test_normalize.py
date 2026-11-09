from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kyb_tool_mcp.matching.countries import to_iso2
from kyb_tool_mcp.matching.normalize import (
    domain_label,
    fold,
    normalize_company,
    normalize_person,
    normalize_postcode,
    parse_partial_dates,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACME WIDGETS LIMITED", "acme widgets"),
        ("Acme Widgets Ltd.", "acme widgets"),
        ("The Acme Group plc", "acme group"),
        ("Nordstar Shipping L.L.C.", "nordstar shipping"),
        ("Müller & Söhne GmbH", "muller and sohne"),
        ("OOO Rosneft", "rosneft"),  # leading CIS legal form stripped (loop 1)
        ("ООО Ромашка", "romashka"),
        ("Open Joint-Stock Company Rosneft Oil Company", "rosneft oil"),
        ("Limited", "limited"),  # never strip to empty
    ],
)
def test_normalize_company(raw: str, expected: str) -> None:
    assert normalize_company(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HOLLOWAY, Sarah Jane", "sarah jane holloway"),
        ("Mrs Sarah Jane Holloway", "sarah jane holloway"),
        ("Dr. José Álvarez", "jose alvarez"),
        ("Владимир Путин", "vladimir putin"),
        ("Sheikh Mohammed Al-Rashidi", "mohammed al rashidi"),
    ],
)
def test_normalize_person(raw: str, expected: str) -> None:
    assert normalize_person(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12 Mar 1961", [(1961, 3, 12)]),
        ("Jun 1958", [(1958, 6, None)]),
        ("1975", [(1975, None, None)]),
        ("1968-04-02", [(1968, 4, 2)]),
        ("1960 to 1962", [(1960, None, None), (1961, None, None), (1962, None, None)]),
        ("circa 1970", [(1970, None, None)]),
        ("", []),
    ],
)
def test_parse_partial_dates(raw: str, expected: list[tuple[int, int | None, int | None]]) -> None:
    assert [(d.year, d.month, d.day) for d in parse_partial_dates(raw)] == expected


def test_postcode_and_domain() -> None:
    assert normalize_postcode("12 Canal Street, Manchester M1 3HE") == "m13he"
    assert normalize_postcode("m13he") == "m13he"
    assert domain_label("https://www.brightwater-analytics.co.uk/about") == "brightwater analytics"


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("Russia", "RU"),
        ("British", "GB"),
        ("GB", "GB"),
        ("Democratic People's Republic of Korea", "KP"),
        ("Yemen", "YE"),
        ("Atlantis", None),
        (None, None),
    ],
)
def test_countries(raw: str | None, iso: str | None) -> None:
    assert to_iso2(raw) == iso


@given(st.text(max_size=60))
def test_property_fold_idempotent(s: str) -> None:
    assert fold(fold(s)) == fold(s)
