"""OpenSanctions bulk `default` dataset loader (FollowTheMoney JSON lines).

LICENCE: OpenSanctions data is CC BY-NC 4.0. Non-commercial use only unless you hold a
commercial licence. This provider is opt-in and never enabled by default.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from kyb_tool_mcp.matching.normalize import parse_partial_dates
from kyb_tool_mcp.schemas.screening import ListEntity, PartialDate

SOURCE = "opensanctions_bulk"
URL = "https://data.opensanctions.org/datasets/latest/default/entities.ftm.json"
NON_COMMERCIAL = True
SCHEMAS = {"Person", "Organization", "Company", "LegalEntity", "PublicBody"}


def load(path: Path) -> Iterator[ListEntity]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("schema") not in SCHEMAS or not row.get("target", True):
                continue
            props = row.get("properties", {})
            names = list(dict.fromkeys(props.get("name", []) + props.get("alias", [])))
            if not names:
                continue
            dobs: list[PartialDate] = []
            for d in props.get("birthDate", []):
                dobs.extend(parse_partial_dates(d))
            yield ListEntity(
                id=f"os-{row['id']}",
                source=SOURCE,
                schema=row["schema"],
                caption=row.get("caption") or names[0],
                names=names,
                weak_names=list(dict.fromkeys(props.get("weakAlias", []))),
                birth_dates=dobs,
                nationalities=list(
                    dict.fromkeys(props.get("nationality", []) + props.get("citizenship", []))
                ),
                topics=props.get("topics", []) or ["sanction"],
                programs=row.get("datasets", [])[:5],
                url=f"https://www.opensanctions.org/entities/{row['id']}/",
            )
