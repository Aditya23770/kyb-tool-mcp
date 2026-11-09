"""UN Security Council Consolidated List loader (public)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from kyb_tool_mcp.matching.normalize import parse_partial_dates
from kyb_tool_mcp.providers.sanctions.loaders._xml import iter_elements, text, texts
from kyb_tool_mcp.schemas.screening import ListEntity, PartialDate

SOURCE = "un_consolidated"
URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"


def _name(el: ET.Element) -> str:
    parts = [text(el, t) for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
    return " ".join(p for p in parts if p)


def _aliases(el: ET.Element, tag: str) -> tuple[list[str], list[str]]:
    strong: list[str] = []
    weak: list[str] = []
    for a in el.findall(tag):
        n = text(a, "ALIAS_NAME")
        if not n:
            continue
        (weak if text(a, "QUALITY").lower() in {"low", "weak"} else strong).append(n)
    return strong, weak


def _dobs(el: ET.Element) -> list[PartialDate]:
    out: list[PartialDate] = []
    for d in el.findall("INDIVIDUAL_DATE_OF_BIRTH"):
        if text(d, "DATE"):
            out.extend(parse_partial_dates(text(d, "DATE")))
        elif text(d, "YEAR"):
            out.extend(parse_partial_dates(text(d, "YEAR")))
        elif text(d, "FROM_YEAR"):
            out.extend(
                parse_partial_dates(f"{text(d, 'FROM_YEAR')} to {text(d, 'TO_YEAR') or text(d, 'FROM_YEAR')}")
            )
    return out


def _entity(el: ET.Element, schema: str, alias_tag: str) -> ListEntity | None:
    primary = _name(el)
    if not primary:
        return None
    names = [primary]
    original = text(el, "NAME_ORIGINAL_SCRIPT")
    if original:
        names.append(original)
    strong, weak = _aliases(el, alias_tag)
    names.extend(strong)
    ref = text(el, "REFERENCE_NUMBER")
    return ListEntity(
        id=f"un-{text(el, 'DATAID')}",
        source=SOURCE,
        schema=schema,
        caption=primary,
        names=list(dict.fromkeys(names)),
        weak_names=list(dict.fromkeys(weak)),
        birth_dates=_dobs(el) if schema == "Person" else [],
        nationalities=texts(el, "NATIONALITY/VALUE"),
        topics=["sanction"],
        programs=[p for p in [text(el, "UN_LIST_TYPE"), ref] if p],
        url="https://main.un.org/securitycouncil/content/un-sc-consolidated-list",
    )


def load(path: Path) -> Iterator[ListEntity]:
    for el in iter_elements(path, "INDIVIDUAL"):
        ent = _entity(el, "Person", "INDIVIDUAL_ALIAS")
        if ent:
            yield ent
    for el in iter_elements(path, "ENTITY"):
        ent = _entity(el, "Organization", "ENTITY_ALIAS")
        if ent:
            yield ent
