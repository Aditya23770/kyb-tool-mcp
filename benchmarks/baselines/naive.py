"""Naive baseline: what a quick rules script typically does.

- registry: company missing -> ESCALATE, inactive -> REJECT (no claim reconciliation)
- screening: rapidfuzz token_sort_ratio against every list name; >= 95 -> REJECT, >= 85 -> ESCALATE
  (no DOB / nationality / schema adjudication, no transliteration)
- website: unreachable -> ESCALATE
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz, process

from kyb_tool_mcp.matching.index import SanctionsIndex
from kyb_tool_mcp.matching.normalize import fold
from kyb_tool_mcp.testing import FakeWorld


class NaiveBaseline:
    name = "naive-fuzzy"

    def __init__(self, index: SanctionsIndex) -> None:
        names: list[str] = []
        for ent in index.iter_entities():
            names.extend(fold(n) for n in ent.names)
        self.names = names

    def decide(self, app: dict[str, Any], world: FakeWorld) -> tuple[str, list[str]]:
        flags: list[str] = []
        reg = app.get("registration_number")
        company = world.companies.get(str(reg).zfill(8)) if reg else None
        if company is None:
            flags.append("REGISTRY_NOT_FOUND")
        elif company.status in {"dissolved", "liquidation", "administration", "receivership"}:
            return "REJECT", ["COMPANY_INACTIVE"]
        subjects = [
            app["legal_name"],
            *(d["name"] for d in app.get("directors", [])),
            *(u["name"] for u in app.get("ubos", [])),
        ]
        if company:
            subjects += [company.name, *(o.name for o in company.officers), *(p.name for p in company.pscs)]
        best = 0.0
        for s in subjects:
            m = process.extractOne(fold(s), self.names, scorer=fuzz.token_sort_ratio, score_cutoff=85)
            if m:
                best = max(best, float(m[1]))
        if best >= 95:
            return "REJECT", [*flags, "SANCTIONS_MATCH"]
        if best >= 85:
            flags.append("POSSIBLE_MATCH")
        site = world.websites.get(
            app.get("website", "").replace("https://", "").replace("www.", "").split("/")[0]
        )
        if site and (site.unreachable or site.status >= 400):
            flags.append("WEBSITE_UNREACHABLE")
        return ("ESCALATE" if flags else "APPROVE"), flags
