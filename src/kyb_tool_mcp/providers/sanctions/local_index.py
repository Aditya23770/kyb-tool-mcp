"""Screening provider backed by the local SQLite sanctions index."""

from __future__ import annotations

import asyncio

from kyb_tool_mcp.config import ScreeningPolicy
from kyb_tool_mcp.matching.blocking import generate_candidates
from kyb_tool_mcp.matching.index import SanctionsIndex
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.screening import Candidate, ScreeningSubject

TOPICS_BY_SOURCE = {
    "ofac_sdn": {"sanction"},
    "un_consolidated": {"sanction"},
    "opensanctions_bulk": {"sanction", "role.pep", "crime", "debarment"},
}


class LocalIndexScreeningProvider:
    """One provider per configured list source, sharing a single index connection."""

    def __init__(self, name: str, index: SanctionsIndex, policy: ScreeningPolicy) -> None:
        self.name = name
        self.index = index
        self.policy = policy
        self.topics = TOPICS_BY_SOURCE.get(name, {"sanction"})

    def available(self) -> bool:
        return self.name in self.index.sources()

    async def screen(self, subject: ScreeningSubject) -> Evidence[list[Candidate]]:
        def run() -> list[Candidate]:
            with self.index.lock:  # one SQLite connection; serialise queries across threads
                return generate_candidates(self.index, subject, self.policy, {self.name})

        cands = await asyncio.to_thread(run)
        request = subject.model_dump(mode="json", exclude_none=True)
        raw = {
            "index_built_at": self.index.meta.get("built_at"),
            "candidates": [(c.entity.id, c.matched_name, c.name_score) for c in cands],
        }
        return Evidence[list[Candidate]].build(
            provider=self.name, kind="screening.candidates", request=request, payload=cands, raw=raw
        )
