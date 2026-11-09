"""RDAP domain lookup (registration date) via rdap.org bootstrap; no key."""

from __future__ import annotations

from datetime import date
from typing import Any

from kyb_tool_mcp.matching.normalize import domain_of
from kyb_tool_mcp.providers.base import ProviderContext, ProviderError
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.website import DomainInfo

BASE_URL = "https://rdap.org/domain/"


def _event(data: dict[str, Any], action: str) -> date | None:
    for ev in data.get("events", []):
        if ev.get("eventAction") == action and ev.get("eventDate"):
            try:
                return date.fromisoformat(str(ev["eventDate"])[:10])
            except ValueError:
                return None
    return None


def _registrar(data: dict[str, Any]) -> str | None:
    for ent in data.get("entities", []):
        if "registrar" in ent.get("roles", []):
            for item in (ent.get("vcardArray") or [None, []])[1]:
                if item and item[0] == "fn":
                    return str(item[3])
    return None


def registrable_domain(host: str) -> str:
    """Rough eTLD+1: keep 3 labels for known two-part suffixes (co.uk, com.au...)."""
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov", "ltd", "plc", "me"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class RdapProvider:
    name = "rdap"

    def __init__(self, ctx: ProviderContext) -> None:
        self.ctx = ctx

    async def domain(self, url: str) -> Evidence[DomainInfo]:
        dom = registrable_domain(domain_of(url))
        r = await self._follow(BASE_URL + dom)
        if r is None:
            info = DomainInfo(domain=dom, found=False)
            raw: Any = {"status": 404}
        else:
            info = DomainInfo(
                domain=dom,
                registered_on=_event(r, "registration"),
                expires_on=_event(r, "expiration"),
                registrar=_registrar(r),
            )
            raw = r
        return Evidence[DomainInfo].build(
            provider=self.name, kind="website.domain", request={"domain": dom}, payload=info, raw=raw
        )

    async def _follow(self, url: str) -> dict[str, Any] | None:
        for _ in range(4):
            resp = await self.ctx.get(self.name, url)
            if resp.status in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = resp.headers["location"]
                continue
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise ProviderError(self.name, f"HTTP {resp.status}")
            data = resp.json()
            return data if isinstance(data, dict) else None
        raise ProviderError(self.name, "too many redirects")
