"""Build benchmark C: end-to-end KYB cases with planted problems.

Companies are synthetic (served offline by `FakeWorld`, in the real Companies House response
shape). Sanctioned people and entities are REAL entries sampled from the local OFAC/UN index
(public-domain data). Expected decisions are assigned by construction, never by running the tool.

    uv run python -m benchmarks.c_cases.build            # needs `kyb-tool-mcp update-lists` first
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.common import CO_WORDS, DATA, INDUSTRIES, MISMATCH_SITE, UK_FIRST, UK_LAST, rng
from kyb_tool_mcp.config import Settings
from kyb_tool_mcp.matching.index import SanctionsIndex, entity_name_tokens
from kyb_tool_mcp.schemas.screening import ListEntity
from kyb_tool_mcp.testing import (
    PARKED_HTML,
    FakeCompany,
    FakePerson,
    FakePSC,
    FakeWebsite,
    FakeWorld,
    business_html,
)

HERE = Path(__file__).parent
SEED = 20261107
PER_TYPE = 12
DEV_SHARE = 0.7

CASE_TYPES: dict[str, tuple[str, list[str]]] = {
    # type: (expected decision, planted flag codes that must be raised)
    "clean": ("APPROVE", []),
    "dissolved": ("REJECT", ["COMPANY_INACTIVE"]),
    "wrong_regno": ("ESCALATE", ["NAME_MISMATCH|REGISTRY_NOT_FOUND"]),
    "wrong_address": ("ESCALATE", ["ADDRESS_MISMATCH"]),
    "undeclared_director": ("ESCALATE", ["UNDECLARED_DIRECTOR"]),
    "unknown_director": ("ESCALATE", ["UNKNOWN_DIRECTOR"]),
    "corporate_psc": ("ESCALATE", ["OPAQUE_OWNERSHIP"]),
    "sanctioned_director": ("REJECT", ["SANCTIONS_MATCH"]),
    "sanctioned_owner": ("REJECT", ["SANCTIONS_MATCH"]),
    "sanctioned_company": ("REJECT", ["SANCTIONS_MATCH"]),
    "name_collision": ("APPROVE", []),
    "industry_mismatch": ("ESCALATE", ["INDUSTRY_MISMATCH", "SIC_INDUSTRY_MISMATCH"]),
    "dead_website": ("ESCALATE", ["WEBSITE_UNREACHABLE"]),
    "parked_website": ("ESCALATE", ["PARKED_DOMAIN"]),
}

TOWNS = [
    ("Manchester", "M1"),
    ("Leeds", "LS1"),
    ("Bristol", "BS1"),
    ("Birmingham", "B1"),
    ("London", "EC2A"),
    ("Glasgow", "G1"),
    ("Cardiff", "CF10"),
    ("Sheffield", "S1"),
    ("Norwich", "NR1"),
    ("York", "YO1"),
]
STREETS = [
    "High Street",
    "Station Road",
    "Church Lane",
    "Mill Road",
    "Victoria Street",
    "Park Avenue",
    "Queen Street",
    "King Street",
    "Market Place",
    "Bridge Street",
]


# ---- sampling real list entries ---------------------------------------------------------------


def _latin(name: str) -> bool:
    return all(ord(ch) < 128 for ch in name)


def _unique_name(conn: sqlite3.Connection, ent: ListEntity) -> bool:
    """True if no OTHER entity shares this exact normalised primary name (keeps labels unambiguous)."""
    norm = " ".join(entity_name_tokens(ent.caption, ent.schema_))
    rows = conn.execute("SELECT DISTINCT entity_id FROM names WHERE norm = ?", (norm,)).fetchall()
    return {r[0] for r in rows} == {ent.id}


def sample_list_entities(
    index: SanctionsIndex, r: random.Random
) -> tuple[list[ListEntity], list[ListEntity]]:
    persons, orgs = [], []
    for ent in index.iter_entities():
        if not _latin(ent.caption):
            continue
        toks = entity_name_tokens(ent.caption, ent.schema_)
        if ent.is_person and ent.birth_dates and 2 <= len(toks) <= 4 and all(len(t) > 1 for t in toks):
            persons.append(ent)
        elif ent.schema_ == "Organization" and 2 <= len(toks) <= 5:
            orgs.append(ent)
    r.shuffle(persons)
    r.shuffle(orgs)
    persons = [p for p in persons[:600] if _unique_name(index.conn, p)]
    orgs = [o for o in orgs[:300] if _unique_name(index.conn, o)]
    return persons, orgs


# ---- synthetic companies --------------------------------------------------------------------


class Factory:
    def __init__(self, r: random.Random) -> None:
        self.r = r
        self.numbers: set[str] = set()
        self.names: set[str] = set()

    def number(self) -> str:
        while True:
            n = f"{self.r.randint(1_000_000, 15_999_999):08d}"
            if n not in self.numbers:
                self.numbers.add(n)
                return n

    def company_name(self) -> str:
        while True:
            n = f"{self.r.choice(CO_WORDS)} {self.r.choice(CO_WORDS)} {self.r.choice(['Solutions', 'Services', 'Group', 'Partners', 'Trading', 'Holdings', 'Works', 'Studio'])}"
            if n not in self.names and len(set(n.split())) == 3:
                self.names.add(n)
                return n

    def person(self) -> dict[str, Any]:
        first = self.r.choice(UK_FIRST)
        middle = self.r.choice(UK_FIRST) if self.r.random() < 0.4 else None
        last = self.r.choice(UK_LAST)
        return {
            "first": first,
            "middle": middle,
            "last": last,
            "year": self.r.randint(1950, 1998),
            "month": self.r.randint(1, 12),
            "nationality": "British",
        }


def ch_name(p: dict[str, Any]) -> str:
    given = " ".join(x for x in (p["first"], p.get("middle")) if x)
    return f"{p['last'].upper()}, {given}"


def app_name(p: dict[str, Any], r: random.Random) -> str:
    style = r.random()
    if style < 0.5 or not p.get("middle"):
        return f"{p['first']} {p['last']}" if style < 0.75 else f"Mr {p['first']} {p['last']}"
    return f"{p['first']} {p['middle']} {p['last']}"


def base_case(f: Factory, r: random.Random, case_id: str) -> tuple[FakeWorld, dict[str, Any], dict[str, Any]]:
    """A clean company: registry, application and website all consistent."""
    world = FakeWorld()
    name = f.company_name()
    sic, declared, site_desc, dom_word = r.choice(INDUSTRIES)
    town, pc_area = r.choice(TOWNS)
    premises = str(r.randint(1, 220))
    street = r.choice(STREETS)
    postcode = (
        f"{pc_area} {r.randint(1, 9)}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}"
    )
    people = [f.person() for _ in range(r.randint(1, 3))]
    owners = people[: r.randint(1, min(2, len(people)))]
    if len(owners) == 1:
        bands = [("ownership-of-shares-75-to-100-percent", 100.0)]
    else:
        bands = [
            ("ownership-of-shares-50-to-75-percent", 60.0),
            ("ownership-of-shares-25-to-50-percent", 40.0),
        ]
    company = FakeCompany(
        number=f.number(),
        name=f"{name.upper()} LIMITED",
        incorporated=f"{r.randint(2003, 2023)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}",
        address={"premises": premises, "address_line_1": street, "locality": town, "postal_code": postcode},
        sic_codes=[sic],
        officers=[FakePerson(ch_name(p), p["year"], p["month"], p["nationality"]) for p in people],
        pscs=[
            FakePSC(
                f"{'Mr' if r.random() < 0.5 else 'Ms'} {p['first']} {p['last']}",
                birth_year=p["year"],
                birth_month=p["month"],
                nationality="British",
                natures_of_control=[band],
            )
            for p, (band, _) in zip(owners, bands, strict=False)
        ],
    )
    world.add_company(company)
    slug = name.split()[0].lower() + "-" + dom_word
    url = f"https://www.{slug}.co.uk"
    world.add_website(
        FakeWebsite(
            url=url,
            html=business_html(name, f"{name}. {site_desc}"),
            domain_registered=f"{r.randint(2005, 2022)}-0{r.randint(1, 9)}-1{r.randint(0, 9)}",
        )
    )
    app: dict[str, Any] = {
        "case_id": case_id,
        "legal_name": f"{name} {'Ltd' if r.random() < 0.6 else 'Limited'}",
        "country": "GB",
        "registration_number": company.number.lstrip("0") if r.random() < 0.2 else company.number,
        "registered_address": f"{premises} {street}, {town}",
        "postcode": postcode,
        "declared_industry": declared,
        "website": url.replace("https://www.", "") if r.random() < 0.3 else url,
        "directors": [
            {"name": app_name(p, r), "birth_year": p["year"], "birth_month": p["month"], "nationality": "GB"}
            for p in people
        ],
        "ubos": [
            {
                "name": f"{p['first']} {p['last']}",
                "birth_year": p["year"],
                "birth_month": p["month"],
                "ownership_pct": pct,
            }
            for p, (_, pct) in zip(owners, bands, strict=False)
        ],
    }
    meta = {"company": company, "people": people, "industry": (sic, declared, site_desc), "url": url}
    return world, app, meta


def list_person_parts(ent: ListEntity) -> tuple[str, int, int | None]:
    d = ent.birth_dates[0]
    return ent.caption, d.year, d.month


def plant(
    kind: str, f: Factory, r: random.Random, case_id: str, persons: list[ListEntity], orgs: list[ListEntity]
) -> tuple[FakeWorld, dict[str, Any], list[str]]:
    world, app, meta = base_case(f, r, case_id)
    company: FakeCompany = meta["company"]
    used: list[str] = []
    if kind == "dissolved":
        company.status = r.choice(["dissolved", "liquidation", "administration"])
    elif kind == "wrong_regno":
        if r.random() < 0.5:
            app["registration_number"] = f.number()  # does not exist
        else:  # points at a different real-looking company
            other, _, _ = base_case(f, r, case_id + "_other")
            oc = next(iter(other.companies.values()))
            world.add_company(oc)
            app["registration_number"] = oc.number
    elif kind == "wrong_address":
        town, pc_area = r.choice([t for t in TOWNS if t[0] != company.address["locality"]])
        app["registered_address"] = f"{r.randint(1, 300)} {r.choice(STREETS)}, {town}"
        app["postcode"] = f"{pc_area} {r.randint(1, 9)}AA"
    elif kind == "undeclared_director":
        p = f.person()
        company.officers.append(FakePerson(ch_name(p), p["year"], p["month"], "British"))
    elif kind == "unknown_director":
        p = f.person()
        app["directors"].append({"name": f"{p['first']} {p['last']}", "birth_year": p["year"]})
    elif kind == "corporate_psc":
        company.pscs = [
            FakePSC(
                f"{f.company_name()} Holdings Limited",
                kind="corporate-entity-person-with-significant-control",
            )
        ]
        app["ubos"] = []
    elif kind in {"sanctioned_director", "sanctioned_owner"}:
        ent = persons.pop()
        used.append(ent.id)
        name, year, month = list_person_parts(ent)
        alias = next((n for n in ent.names[1:] if _latin(n) and len(n.split()) >= 2), None)
        declared = alias if (alias and r.random() < 0.3) else name.title()
        nat = "Unknown"
        if kind == "sanctioned_director":
            company.officers.append(FakePerson(name.upper(), year, month, nat))
            app["directors"].append({"name": declared, "birth_year": year, "birth_month": month})
        else:
            company.pscs.append(
                FakePSC(
                    name,
                    birth_year=year,
                    birth_month=month,
                    natures_of_control=["ownership-of-shares-25-to-50-percent"],
                )
            )
            app["ubos"].append(
                {"name": declared, "birth_year": year, "birth_month": month, "ownership_pct": 30}
            )
    elif kind == "sanctioned_company":
        ent = orgs.pop()
        used.append(ent.id)
        company.name = f"{ent.caption.upper()} LIMITED"
        app["legal_name"] = f"{ent.caption.title()} Ltd"
    elif kind == "name_collision":
        ent = persons.pop()
        used.append(ent.id)
        name, year, _ = list_person_parts(ent)
        decoy_year = year + r.choice([-1, 1]) * r.randint(6, 25)
        decoy_year = min(max(decoy_year, 1940), 2003)
        if abs(decoy_year - year) <= 2:
            decoy_year = year + 20 if year < 1980 else year - 20
        company.officers.append(FakePerson(name.upper(), decoy_year, r.randint(1, 12), "British"))
        app["directors"].append({"name": name.title(), "birth_year": decoy_year, "nationality": "GB"})
    elif kind == "industry_mismatch":
        sic, declared, _ = meta["industry"]
        other = r.choice([i for i in INDUSTRIES if i[0] != sic])
        app["declared_industry"] = other[1]
        world.websites[next(iter(world.websites))].html = business_html("Blooms", MISMATCH_SITE)
    elif kind == "dead_website":
        site = world.websites[next(iter(world.websites))]
        if r.random() < 0.5:
            site.unreachable = True
        else:
            site.status = r.choice([404, 410, 500])
    elif kind == "parked_website":
        site = world.websites[next(iter(world.websites))]
        site.html = PARKED_HTML
        site.domain_registered = "2026-10-15"
    return world, app, used


def build(index_path: Path, out: Path = HERE, sample_out: Path = DATA / "list_sample.json") -> dict[str, Any]:
    r = rng(SEED)
    index = SanctionsIndex(index_path)
    persons, orgs = sample_list_entities(index, r)
    f = Factory(r)
    cases: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for kind, (expected, planted) in CASE_TYPES.items():
        for i in range(PER_TYPE):
            case_id = f"{kind}_{i + 1:02d}"
            world, app, used = plant(kind, f, r, case_id, persons, orgs)
            used_ids.update(used)
            cases.append(
                {
                    "case_id": case_id,
                    "type": kind,
                    "expected": expected,
                    "planted": planted,
                    "list_entities": used,
                    "application": app,
                    "world": world.to_dict(),
                }
            )
    # split per type so both splits contain every case type
    for d in ("dev", "holdout"):
        for p in (out / d).glob("*.json"):
            p.unlink()
    split_counts: Counter[str] = Counter()
    by_type: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        by_type.setdefault(c["type"], []).append(c)
    for group in by_type.values():
        r.shuffle(group)
        cut = round(len(group) * DEV_SHARE)
        for j, c in enumerate(group):
            split = "dev" if j < cut else "holdout"
            (out / split).mkdir(parents=True, exist_ok=True)
            (out / split / f"{c['case_id']}.json").write_text(
                json.dumps(c, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            split_counts[split] += 1
    # Deterministic list sample for offline runs / CI: every planted entity + realistic background noise.
    background = [e.id for e in index.iter_entities()]
    r.shuffle(background)
    keep = sorted(used_ids | set(background[:4000]))
    ents = index.get_entities(keep)
    sample_out.parent.mkdir(parents=True, exist_ok=True)
    sample_out.write_text(
        json.dumps(
            {
                "note": "Sample of OFAC SDN (public domain) and UN consolidated list entries used by the "
                "benchmarks. Regenerate with benchmarks.c_cases.build.",
                "index_built_at": index.meta.get("built_at"),
                "entities": [e.model_dump(mode="json", by_alias=True) for e in ents],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"cases": len(cases), "splits": dict(split_counts), "list_sample": len(ents)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, default=None, help="sanctions index (default: ~/.kyb-tool-mcp)")
    a = ap.parse_args()
    print(json.dumps(build(a.index or Settings().index_path), indent=2))


if __name__ == "__main__":
    main()
