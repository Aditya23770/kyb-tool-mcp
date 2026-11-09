"""GLEIF LEI API provider (no key). Global coverage, but no officers or owners."""

from __future__ import annotations

from typing import Any

from kyb_tool_mcp.providers.base import ProviderContext, ProviderError
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.registry import Address, CompanyRecord

BASE_URL = "https://api.gleif.org/api/v1/lei-records"

STATUS_MAP = {"ACTIVE": "active", "INACTIVE": "dissolved", "NULL": "unknown"}


def map_record(item: dict[str, Any]) -> CompanyRecord:
    attrs = item.get("attributes", {})
    ent = attrs.get("entity", {})
    la = ent.get("legalAddress") or {}
    status = STATUS_MAP.get(str(ent.get("status", "NULL")).upper(), "unknown")
    return CompanyRecord(
        registry="gleif",
        company_number=str(ent.get("registeredAs") or ""),
        name=str((ent.get("legalName") or {}).get("name", "")),
        status=status,
        address=Address(
            lines=[str(x) for x in la.get("addressLines", [])],
            locality=la.get("city"),
            region=la.get("region"),
            postal_code=la.get("postalCode"),
            country=la.get("country"),
        ),
        previous_names=[str(n.get("name")) for n in ent.get("otherNames", []) if n.get("name")],
        officers_available=False,
        pscs_available=False,
        lei=str(attrs.get("lei") or item.get("id") or "") or None,
        source_url=f"https://search.gleif.org/#/record/{attrs.get('lei') or item.get('id')}",
    )


class GleifProvider:
    name = "gleif"

    def __init__(self, ctx: ProviderContext) -> None:
        self.ctx = ctx
        self.limiter = ctx.limiter(self.name, rate=5, burst=10)

    def supports(self, country: str) -> bool:
        return True

    async def _query(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        r = await self.ctx.get(self.name, BASE_URL, params={**params, "page[size]": 5}, limiter=self.limiter)
        if r.status != 200:
            raise ProviderError(self.name, f"HTTP {r.status}")
        data = r.json()
        return list(data.get("data", [])) if isinstance(data, dict) else []

    async def lookup(
        self, country: str, reg_no: str | None, name: str | None
    ) -> Evidence[CompanyRecord] | None:
        items: list[dict[str, Any]] = []
        request: dict[str, Any] = {"country": country, "registration_number": reg_no, "name": name}
        if reg_no:
            items = await self._query(
                {"filter[entity.registeredAs]": reg_no, "filter[entity.legalAddress.country]": country}
            )
        if not items and name:
            items = await self._query(
                {"filter[entity.legalName]": name, "filter[entity.legalAddress.country]": country}
            )
        if not items:
            return None
        record = map_record(items[0])
        return Evidence[CompanyRecord].build(
            provider=self.name, kind="registry.lei", request=request, payload=record, raw=items[0]
        )
