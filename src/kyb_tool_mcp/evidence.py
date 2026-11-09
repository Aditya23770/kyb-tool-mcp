"""EvidenceStore and the evidence validator ("evidence or it didn't happen")."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from kyb_tool_mcp.schemas.decision import Flag
from kyb_tool_mcp.schemas.evidence import Evidence


class EvidenceStore:
    def __init__(self) -> None:
        self._items: dict[str, Evidence[Any]] = {}

    def add(self, ev: Evidence[Any]) -> str:
        self._items[ev.id] = ev
        return ev.id

    def get(self, ev_id: str) -> Evidence[Any] | None:
        return self._items.get(ev_id)

    def __contains__(self, ev_id: object) -> bool:
        return ev_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    def ids(self) -> list[str]:
        return list(self._items)

    def items(self) -> list[Evidence[Any]]:
        return list(self._items.values())

    def summaries(self) -> dict[str, Any]:
        """Compact, JSON-safe evidence index for the decision output (payloads omitted)."""
        out: dict[str, Any] = {}
        for ev in self._items.values():
            out[ev.id] = {
                "provider": ev.provider,
                "kind": ev.kind,
                "fetched_at": ev.fetched_at.isoformat(),
                "request": ev.request,
                "raw_hash": ev.raw_hash,
            }
        return out

    def dump(self) -> dict[str, Any]:
        """Full evidence including payloads (for audit / --evidence output)."""
        result: dict[str, Any] = {}
        for ev in self._items.values():
            payload = ev.payload
            if isinstance(payload, BaseModel):
                payload = payload.model_dump(mode="json")
            elif isinstance(payload, list):
                payload = [p.model_dump(mode="json") if isinstance(p, BaseModel) else p for p in payload]
            result[ev.id] = {**self.summaries()[ev.id], "payload": payload}
        return result


def validate_flags(flags: list[Flag], store: EvidenceStore) -> tuple[list[Flag], list[Flag]]:
    """Split flags into (valid, rejected).

    A flag is valid only if it cites at least one evidence id and every cited id exists.
    System flags about *missing* data (CHECK_NOT_CONFIGURED, NO_WEBSITE_DECLARED) are exempt
    from the "at least one" rule because there is nothing to cite.
    """
    exempt = {"CHECK_NOT_CONFIGURED", "NO_WEBSITE_DECLARED", "INSUFFICIENT_INPUT"}
    valid: list[Flag] = []
    rejected: list[Flag] = []
    for f in flags:
        if not f.evidence and f.code not in exempt:
            rejected.append(f)
        elif all(e in store for e in f.evidence):
            valid.append(f)
        else:
            rejected.append(f)
    return valid, rejected
