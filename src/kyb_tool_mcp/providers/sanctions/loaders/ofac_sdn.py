"""OFAC Specially Designated Nationals (SDN) list loader (US Treasury, public domain)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from kyb_tool_mcp.matching.normalize import parse_partial_dates
from kyb_tool_mcp.providers.sanctions.loaders._xml import iter_elements, text, texts
from kyb_tool_mcp.schemas.screening import ListEntity, PartialDate

SOURCE = "ofac_sdn"
URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
SCHEMA_MAP = {"individual": "Person", "entity": "Organization", "vessel": "Vessel", "aircraft": "Aircraft"}


def _full(first: str, last: str) -> str:
    return f"{first} {last}".strip()


def load(path: Path) -> Iterator[ListEntity]:
    for e in iter_elements(path, "sdnEntry"):
        uid = text(e, "uid")
        schema = SCHEMA_MAP.get(text(e, "sdnType").lower(), "LegalEntity")
        primary = _full(text(e, "firstName"), text(e, "lastName"))
        if not primary:
            continue
        names = [primary]
        weak: list[str] = []
        for aka in e.findall("akaList/aka"):
            n = _full(text(aka, "firstName"), text(aka, "lastName"))
            if n:
                (weak if text(aka, "category").lower() == "weak" else names).append(n)
        dobs: list[PartialDate] = []
        for d in texts(e, "dateOfBirthList/dateOfBirthItem/dateOfBirth"):
            dobs.extend(parse_partial_dates(d))
        nats = texts(e, "nationalityList/nationality/country") + texts(
            e, "citizenshipList/citizenship/country"
        )
        yield ListEntity(
            id=f"ofac-{uid}",
            source=SOURCE,
            schema=schema,
            caption=primary,
            names=list(dict.fromkeys(names)),
            weak_names=list(dict.fromkeys(weak)),
            birth_dates=dobs,
            nationalities=list(dict.fromkeys(nats)),
            topics=["sanction"],
            programs=texts(e, "programList/program"),
            url=f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={uid}",
        )
