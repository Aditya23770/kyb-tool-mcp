"""UK Companies House REST API provider (free key, HTTP Basic with key as username)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from kyb_tool_mcp.matching.normalize import normalize_company
from kyb_tool_mcp.providers.base import NotFoundError, ProviderContext, ProviderError
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.registry import PSC, Address, CompanyRecord, Officer

BASE_URL = "https://api.company-information.service.gov.uk"
CORPORATE_PSC_KINDS = ("corporate-entity", "legal-person")
_PCT = re.compile(r"(\d+)-to-(\d+)-percent")


def _d(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _address(a: dict[str, Any] | None) -> Address | None:
    if not a:
        return None
    lines = [a.get(k) for k in ("premises", "address_line_1", "address_line_2") if a.get(k)]
    if a.get("premises") and a.get("address_line_1"):
        lines = [
            f"{a['premises']} {a['address_line_1']}",
            *([a["address_line_2"]] if a.get("address_line_2") else []),
        ]
    return Address(
        lines=[str(x) for x in lines],
        locality=a.get("locality"),
        region=a.get("region"),
        postal_code=a.get("postal_code"),
        country=a.get("country"),
    )


def ownership_band(natures: list[str]) -> tuple[float | None, float | None]:
    lo: float | None = None
    hi: float | None = None
    for n in natures:
        if not n.startswith("ownership-of-shares"):
            continue
        m = _PCT.search(n)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            lo = a if lo is None else max(lo, a)
            hi = b if hi is None else max(hi, b)
    return lo, hi


def map_officer(o: dict[str, Any]) -> Officer:
    dob = o.get("date_of_birth") or {}
    role = str(o.get("officer_role", "director"))
    return Officer(
        name=str(o.get("name", "")),
        role=role,
        appointed_on=_d(o.get("appointed_on")),
        resigned_on=_d(o.get("resigned_on")),
        birth_year=dob.get("year"),
        birth_month=dob.get("month"),
        nationality=o.get("nationality"),
        is_corporate=role.startswith("corporate"),
    )


def map_psc(p: dict[str, Any]) -> PSC:
    kind = str(p.get("kind", ""))
    natures = [str(n) for n in p.get("natures_of_control", [])]
    lo, hi = ownership_band(natures)
    dob = p.get("date_of_birth") or {}
    ident = p.get("identification") or {}
    return PSC(
        name=str(p.get("name", "")),
        kind=kind,
        is_corporate=any(k in kind for k in CORPORATE_PSC_KINDS),
        natures_of_control=natures,
        ownership_pct_min=lo,
        ownership_pct_max=hi,
        birth_year=dob.get("year"),
        birth_month=dob.get("month"),
        nationality=p.get("nationality"),
        notified_on=_d(p.get("notified_on")),
        ceased_on=_d(p.get("ceased_on"))
        or (_d(p.get("ceased")) if isinstance(p.get("ceased"), str) else None),
        registration_number=ident.get("registration_number"),
    )


def map_company(
    profile: dict[str, Any],
    officers: dict[str, Any] | None,
    pscs: dict[str, Any] | None,
    statements: dict[str, Any] | None = None,
) -> CompanyRecord:
    return CompanyRecord(
        registry="companies_house",
        company_number=str(profile.get("company_number", "")),
        name=str(profile.get("company_name", "")),
        status=str(profile.get("company_status", "unknown")),
        incorporated_on=_d(profile.get("date_of_creation")),
        dissolved_on=_d(profile.get("date_of_cessation")),
        company_type=profile.get("type"),
        address=_address(profile.get("registered_office_address")),
        sic_codes=[str(s) for s in profile.get("sic_codes", [])],
        previous_names=[
            str(p.get("name")) for p in profile.get("previous_company_names", []) if p.get("name")
        ],
        officers=[map_officer(o) for o in (officers or {}).get("items", [])],
        pscs=[map_psc(p) for p in (pscs or {}).get("items", [])],
        psc_statements=[
            str(s.get("statement")) for s in (statements or {}).get("items", []) if s.get("statement")
        ],
        officers_available=officers is not None,
        pscs_available=pscs is not None,
        source_url=f"https://find-and-update.company-information.service.gov.uk/company/{profile.get('company_number')}",
    )


class CompaniesHouseProvider:
    name = "companies_house"

    def __init__(self, ctx: ProviderContext, api_key: str) -> None:
        self.ctx = ctx
        self.auth = (api_key, "")
        # 600 requests / 5 minutes => 2/s sustained
        self.limiter = ctx.limiter(self.name, rate=1.9, burst=10)

    def supports(self, country: str) -> bool:
        return country.upper() in {"GB", "UK"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        r = await self.ctx.get(
            self.name, BASE_URL + path, params=params, auth=self.auth, limiter=self.limiter
        )
        if r.status == 404:
            return None
        if r.status == 401:
            raise ProviderError(self.name, "unauthorised: check COMPANIES_HOUSE_API_KEY")
        if r.status != 200:
            raise ProviderError(self.name, f"HTTP {r.status} for {path}")
        data = r.json()
        if not isinstance(data, dict):
            raise ProviderError(self.name, f"unexpected payload for {path}")
        return data

    async def search(self, name: str, limit: int = 5) -> list[dict[str, Any]]:
        data = await self._get("/search/companies", {"q": name, "items_per_page": limit})
        return list((data or {}).get("items", []))

    async def _resolve_number(self, name: str) -> str | None:
        target = normalize_company(name)
        for item in await self.search(name):
            if normalize_company(str(item.get("title", ""))) == target:
                return str(item.get("company_number"))
        return None

    async def lookup(
        self, country: str, reg_no: str | None, name: str | None
    ) -> Evidence[CompanyRecord] | None:
        number = reg_no
        request: dict[str, Any] = {"country": country, "registration_number": reg_no, "name": name}
        if not number and name:
            number = await self._resolve_number(name)
            request["resolved_number"] = number
        if not number:
            return None
        profile = await self._get(f"/company/{number}")
        if profile is None:
            return None
        officers = await self._get(f"/company/{number}/officers", {"items_per_page": 100})
        pscs = await self._get(f"/company/{number}/persons-with-significant-control", {"items_per_page": 100})
        statements = None
        if not (pscs or {}).get("items"):
            statements = await self._get(f"/company/{number}/persons-with-significant-control-statements")
        record = map_company(profile, officers, pscs, statements)
        raw = {"profile": profile, "officers": officers, "pscs": pscs, "statements": statements}
        return Evidence[CompanyRecord].build(
            provider=self.name, kind="registry.company", request=request, payload=record, raw=raw
        )

    async def lookup_or_raise(self, number: str) -> Evidence[CompanyRecord]:
        ev = await self.lookup("GB", number, None)
        if ev is None:
            raise NotFoundError(self.name, f"company {number} not found")
        return ev
