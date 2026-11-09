"""Offline test harness: a fake "world" of registry records, websites and domains.

`FakeWorld` serves Companies House, GLEIF, RDAP and arbitrary website responses through an
`httpx.MockTransport`, using the real API response shapes. Tests, benchmarks and anyone
writing a provider can review cases with zero network access and fully deterministic results.

    world = FakeWorld()
    world.add_company(FakeCompany(number="01234567", name="ACME WIDGETS LIMITED", ...))
    pipeline = ReviewPipeline(config, transport=world.transport(), today=date(2026, 11, 1))
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from kyb_tool_mcp.matching.normalize import domain_of, normalize_company
from kyb_tool_mcp.providers.rdap import registrable_domain


@dataclass
class FakePerson:
    name: str  # Companies House style "SURNAME, Forenames" is fine
    birth_year: int | None = None
    birth_month: int | None = None
    nationality: str | None = None
    role: str = "director"
    resigned_on: str | None = None


@dataclass
class FakePSC:
    name: str
    kind: str = "individual-person-with-significant-control"
    natures_of_control: list[str] = field(default_factory=lambda: ["ownership-of-shares-75-to-100-percent"])
    birth_year: int | None = None
    birth_month: int | None = None
    nationality: str | None = None
    ceased_on: str | None = None


@dataclass
class FakeCompany:
    number: str
    name: str
    status: str = "active"
    incorporated: str = "2015-03-01"
    address: dict[str, str] = field(
        default_factory=lambda: {
            "address_line_1": "1 High Street",
            "locality": "London",
            "postal_code": "EC1A 1BB",
        }
    )
    sic_codes: list[str] = field(default_factory=lambda: ["62012"])
    officers: list[FakePerson] = field(default_factory=list)
    pscs: list[FakePSC] = field(default_factory=list)
    previous_names: list[str] = field(default_factory=list)
    company_type: str = "ltd"
    psc_statement: str | None = None

    def profile(self) -> dict[str, Any]:
        p: dict[str, Any] = {
            "company_number": self.number,
            "company_name": self.name,
            "company_status": self.status,
            "date_of_creation": self.incorporated,
            "type": self.company_type,
            "registered_office_address": self.address,
            "sic_codes": self.sic_codes,
        }
        if self.previous_names:
            p["previous_company_names"] = [{"name": n} for n in self.previous_names]
        if self.status == "dissolved":
            p["date_of_cessation"] = "2024-01-01"
        return p

    def officers_json(self) -> dict[str, Any]:
        items = []
        for o in self.officers:
            item: dict[str, Any] = {"name": o.name, "officer_role": o.role, "appointed_on": self.incorporated}
            if o.birth_year:
                item["date_of_birth"] = {
                    "year": o.birth_year,
                    **({"month": o.birth_month} if o.birth_month else {}),
                }
            if o.nationality:
                item["nationality"] = o.nationality
            if o.resigned_on:
                item["resigned_on"] = o.resigned_on
            items.append(item)
        return {"items": items, "total_results": len(items)}

    def pscs_json(self) -> dict[str, Any]:
        items = []
        for p in self.pscs:
            item: dict[str, Any] = {
                "name": p.name,
                "kind": p.kind,
                "natures_of_control": p.natures_of_control,
                "notified_on": self.incorporated,
            }
            if p.birth_year:
                item["date_of_birth"] = {
                    "year": p.birth_year,
                    **({"month": p.birth_month} if p.birth_month else {}),
                }
            if p.nationality:
                item["nationality"] = p.nationality
            if p.ceased_on:
                item["ceased_on"] = p.ceased_on
            items.append(item)
        return {"items": items, "total_results": len(items)}


@dataclass
class FakeWebsite:
    url: str
    html: str = ""
    status: int = 200
    unreachable: bool = False
    domain_registered: str | None = "2012-05-01"


class FakeWorld:
    def __init__(self) -> None:
        self.companies: dict[str, FakeCompany] = {}
        self.websites: dict[str, FakeWebsite] = {}
        self.requests: list[str] = []

    # ---- building ------------------------------------------------------------
    def add_company(self, c: FakeCompany) -> FakeCompany:
        self.companies[c.number] = c
        return c

    def add_website(self, w: FakeWebsite) -> FakeWebsite:
        self.websites[domain_of(w.url)] = w
        return w

    # ---- persistence (fixtures / benchmark cases) ------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "companies": [asdict(c) for c in self.companies.values()],
            "websites": [asdict(w) for w in self.websites.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FakeWorld:
        w = cls()
        for c in data.get("companies", []):
            c = dict(c)
            c["officers"] = [FakePerson(**o) for o in c.get("officers", [])]
            c["pscs"] = [FakePSC(**p) for p in c.get("pscs", [])]
            w.add_company(FakeCompany(**c))
        for s in data.get("websites", []):
            w.add_website(FakeWebsite(**s))
        return w

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> FakeWorld:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # ---- HTTP -------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        host = request.url.host
        if host == "api.company-information.service.gov.uk":
            return self._companies_house(request)
        if host == "api.gleif.org":
            return httpx.Response(200, json={"data": []})
        if host == "rdap.org":
            return self._rdap(request)
        site = self.websites.get(domain_of(url))
        if site is None or site.unreachable:
            raise httpx.ConnectError(f"cannot connect to {host}", request=request)
        return httpx.Response(
            site.status, html=site.html, headers={"content-type": "text/html; charset=utf-8"}
        )

    def _companies_house(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.strip("/").split("/")
        if path[:2] == ["search", "companies"]:
            q = normalize_company(parse_qs(urlparse(str(request.url)).query).get("q", [""])[0])
            items = [
                {"company_number": c.number, "title": c.name, "company_status": c.status}
                for c in self.companies.values()
                if q and q in normalize_company(c.name)
            ]
            return httpx.Response(200, json={"items": items[:5]})
        if len(path) < 2 or path[0] != "company":
            return httpx.Response(404, json={})
        c = self.companies.get(path[1])
        if c is None:
            return httpx.Response(404, json={"errors": [{"error": "company-profile-not-found"}]})
        if len(path) == 2:
            return httpx.Response(200, json=c.profile())
        if path[2] == "officers":
            return httpx.Response(200, json=c.officers_json())
        if path[2] == "persons-with-significant-control-statements":
            items = [{"statement": c.psc_statement}] if c.psc_statement else []
            return httpx.Response(200, json={"items": items})
        if path[2] == "persons-with-significant-control":
            return httpx.Response(200, json=c.pscs_json())
        return httpx.Response(404, json={})

    def _rdap(self, request: httpx.Request) -> httpx.Response:
        dom = request.url.path.rsplit("/", 1)[-1]
        site = next((w for d, w in self.websites.items() if registrable_domain(d) == dom), None)
        if site is None or site.domain_registered is None:
            return httpx.Response(404, json={})
        return httpx.Response(
            200,
            json={
                "ldhName": dom,
                "events": [
                    {"eventAction": "registration", "eventDate": f"{site.domain_registered}T00:00:00Z"}
                ],
            },
        )


def business_html(title: str, body: str, words: int = 260) -> str:
    """A plausible business homepage with at least `words` words of content."""
    filler = (
        "Our team works with customers across the United Kingdom and Europe. We publish clear terms, "
        "a returns policy, a privacy notice and our company registration details. Contact our office "
        "during business hours for quotes, delivery information and account support. "
    )
    text = body + " "
    while len(text.split()) < words:
        text += filler
    return (
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{text}</p>"
        "<footer>Registered in England and Wales.</footer></body></html>"
    )


PARKED_HTML = (
    "<html><head><title>example domain</title></head><body><h1>This domain is for sale</h1>"
    "<p>Buy this domain today. Related searches: loans, insurance.</p></body></html>"
)
