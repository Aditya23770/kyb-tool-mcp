"""End-to-end reviews with a FakeWorld (no network) and the mini sanctions index."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from kyb_tool_mcp.config import Config
from kyb_tool_mcp.pipeline import ReviewPipeline
from kyb_tool_mcp.schemas.decision import ReviewResult
from kyb_tool_mcp.testing import PARKED_HTML, FakeCompany, FakePerson, FakePSC, FakeWebsite, business_html
from tests.conftest import TODAY, clean_application, clean_world


def codes(r: ReviewResult) -> set[str]:
    return {f.code for f in r.flags}


async def review(make_pipeline: Any, world: Any, app: dict[str, Any]) -> ReviewResult:
    pipe: ReviewPipeline = make_pipeline(world)
    return await pipe.review(app)


# ---- the six reference cases (PLAN.md phase 5) -------------------------------------------


async def test_clean_company_approves(make_pipeline: Any) -> None:
    r = await review(make_pipeline, clean_world(), clean_application())
    assert r.decision == "APPROVE", r.flags
    assert codes(r) <= {"CHECK_NOT_CONFIGURED"}  # PEP not configured in free mode
    assert {c.check: c.status for c in r.checks} == {
        "registry": "done",
        "ownership": "done",
        "sanctions": "done",
        "pep": "not_configured",
        "website": "done",
    }
    assert r.aml_info.result == "clear" and r.aml_info.num_subjects >= 3
    assert r.analyst_note and "APPROVE" in r.analyst_note


async def test_dissolved_company_rejects(make_pipeline: Any) -> None:
    w = clean_world()
    w.companies["09876543"].status = "dissolved"
    r = await review(make_pipeline, w, clean_application())
    assert r.decision == "REJECT"
    assert "COMPANY_INACTIVE" in codes(r)


async def test_sanctioned_director_rejects(make_pipeline: Any) -> None:
    w = clean_world()
    w.companies["09876543"].officers.append(FakePerson("BOROVKOV, Viktor Ivanovich", 1961, 3, "Russian"))
    app = clean_application()
    app["directors"].append({"name": "Viktor Ivanovich Borovkov", "birth_year": 1961, "birth_month": 3})  # type: ignore[attr-defined]
    r = await review(make_pipeline, w, app)
    assert r.decision == "REJECT"
    sanc = [f for f in r.flags if f.code == "SANCTIONS_MATCH"]
    assert sanc and all(e in r.evidence for e in sanc[0].evidence)
    assert any(h.entity_id == "ofac-1001" and h.outcome == "TRUE_MATCH" for h in r.aml_info.hits)


async def test_corporate_psc_escalates(make_pipeline: Any) -> None:
    w = clean_world()
    w.companies["09876543"].pscs = [
        FakePSC("Brightwater Holdings Ltd", kind="corporate-entity-person-with-significant-control")
    ]
    app = clean_application()
    app["ubos"] = []
    r = await review(make_pipeline, w, app)
    assert r.decision == "ESCALATE"
    assert "OPAQUE_OWNERSHIP" in codes(r)


async def test_name_collision_approves_with_cleared_hit(make_pipeline: Any) -> None:
    w = clean_world()
    w.companies["09876543"].officers.append(FakePerson("HARGREAVES, David", 1987, 9, "British"))
    app = clean_application()
    app["directors"].append(
        {"name": "David Hargreaves", "birth_year": 1987, "birth_month": 9, "nationality": "GB"}
    )  # type: ignore[attr-defined]
    r = await review(make_pipeline, w, app)
    assert r.decision == "APPROVE", r.flags
    cleared = {h.entity_id: h for h in r.aml_info.cleared}
    assert "ofac-1005" in cleared and any("birth year differs" in x for x in cleared["ofac-1005"].reasons)


async def test_parked_website_escalates(make_pipeline: Any) -> None:
    w = clean_world()
    w.websites["brightwater-analytics.co.uk"].html = PARKED_HTML
    w.websites["brightwater-analytics.co.uk"].domain_registered = "2026-10-01"
    r = await review(make_pipeline, w, clean_application())
    assert {"PARKED_DOMAIN", "NEW_DOMAIN"} <= codes(r)
    assert r.decision == "ESCALATE"


# ---- registry flags ------------------------------------------------------------------------


async def test_registry_not_found(make_pipeline: Any) -> None:
    app = clean_application()
    app["registration_number"] = "11111111"
    r = await review(make_pipeline, clean_world(), app)
    assert "REGISTRY_NOT_FOUND" in codes(r) and r.decision == "ESCALATE"


async def test_lookup_by_name_when_number_missing(make_pipeline: Any) -> None:
    app = clean_application()
    app["registration_number"] = None
    r = await review(make_pipeline, clean_world(), app)
    assert r.decision == "APPROVE", r.flags


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda a: a.update(legal_name="Brightwater Analytica Ltd"), "NAME_MISMATCH"),
        (lambda a: a.update(legal_name="Completely Different Trading Ltd"), "NAME_MISMATCH"),
        (
            lambda a: a.update(postcode="SW1A 1AA", registered_address="10 Downing Street, London"),
            "ADDRESS_MISMATCH",
        ),
        (lambda a: a["directors"].pop(), "UNDECLARED_DIRECTOR"),
        (
            lambda a: a["directors"].append({"name": "Gregory Pembleton", "birth_year": 1960}),
            "UNKNOWN_DIRECTOR",
        ),
        (lambda a: a["ubos"].pop(), "OWNERSHIP_MISMATCH"),
        (lambda a: a["ubos"][0].update(ownership_pct=90), "OWNERSHIP_MISMATCH"),
        (lambda a: a.update(declared_industry="wholesale of live animals"), "SIC_INDUSTRY_MISMATCH"),
    ],
)
async def test_registry_rules(make_pipeline: Any, mutate: Any, expected: str) -> None:
    app = copy.deepcopy(clean_application())
    mutate(app)
    r = await review(make_pipeline, clean_world(), app)
    assert expected in codes(r), r.flags


async def test_new_entity_and_no_ubo(make_pipeline: Any) -> None:
    w = clean_world()
    c = w.companies["09876543"]
    c.incorporated = "2026-08-01"
    c.pscs = []
    c.psc_statement = "no-individual-or-entity-with-signficant-control"
    app = clean_application()
    app["ubos"] = []
    r = await review(make_pipeline, w, app)
    assert {"NEW_ENTITY", "NO_UBO_DATA"} <= codes(r)


async def test_resigned_director_and_ceased_psc_ignored(make_pipeline: Any) -> None:
    w = clean_world()
    c = w.companies["09876543"]
    c.officers.append(FakePerson("FORMER, Person", 1970, role="director", resigned_on="2020-01-01"))
    c.pscs.append(FakePSC("Old Owner", birth_year=1950, ceased_on="2019-01-01"))
    r = await review(make_pipeline, w, clean_application())
    assert r.decision == "APPROVE", r.flags


# ---- website flags -------------------------------------------------------------------------


async def test_unreachable_website(make_pipeline: Any) -> None:
    w = clean_world()
    w.websites["brightwater-analytics.co.uk"].unreachable = True
    r = await review(make_pipeline, w, clean_application())
    assert "WEBSITE_UNREACHABLE" in codes(r)


async def test_prohibited_goods(make_pipeline: Any) -> None:
    w = clean_world()
    w.websites["brightwater-analytics.co.uk"].html = business_html(
        "Brightwater",
        "Business software. Also AAA replica watches and replica designer bags shipped worldwide.",
    )
    r = await review(make_pipeline, w, clean_application())
    assert "PROHIBITED_GOODS" in codes(r) and r.decision == "ESCALATE"


async def test_negated_prohibited_phrase_is_ignored(make_pipeline: Any) -> None:
    w = clean_world()
    w.websites["brightwater-analytics.co.uk"].html = business_html(
        "Brightwater", "Business software development. We never sell counterfeit goods."
    )
    r = await review(make_pipeline, w, clean_application())
    assert "PROHIBITED_GOODS" not in codes(r)


async def test_industry_mismatch_website_and_sic(make_pipeline: Any) -> None:
    w = clean_world()
    w.websites["brightwater-analytics.co.uk"].html = business_html(
        "Brightwater", "Fresh flowers, bouquets and wedding floristry delivered same day across Manchester."
    )
    app = clean_application()
    app["declared_industry"] = "electronics wholesale"
    r = await review(make_pipeline, w, app)
    assert {"INDUSTRY_MISMATCH", "SIC_INDUSTRY_MISMATCH"} <= codes(r)
    assert r.decision == "ESCALATE"


async def test_domain_name_mismatch(make_pipeline: Any) -> None:
    w = clean_world()
    w.add_website(FakeWebsite(url="https://cheap-loans-4u.biz", html=business_html("x", "Business software")))
    app = clean_application()
    app["website"] = "cheap-loans-4u.biz"
    r = await review(make_pipeline, w, app)
    assert "DOMAIN_NAME_MISMATCH" in codes(r)


async def test_no_website_declared(make_pipeline: Any) -> None:
    app = clean_application()
    app["website"] = None
    r = await review(make_pipeline, clean_world(), app)
    assert "NO_WEBSITE_DECLARED" in codes(r) and r.decision == "APPROVE"


# ---- system behaviour ----------------------------------------------------------------------


async def test_missing_index_fails_closed(config: Config, tmp_path: Path) -> None:
    pipe = ReviewPipeline(
        config, transport=clean_world().transport(), index_path=tmp_path / "none.sqlite", today=TODAY
    )
    r = await pipe.review(clean_application())
    assert "PROVIDER_ERROR" in codes(r) and r.decision == "ESCALATE"
    assert next(c for c in r.checks if c.check == "sanctions").status == "error"


async def test_registry_http_error_fails_closed(make_pipeline: Any) -> None:
    import httpx

    w = clean_world()
    original = w.handle

    def broken(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.company-information.service.gov.uk":
            return httpx.Response(503)
        return original(req)

    w.handle = broken  # type: ignore[method-assign]
    r = await review(make_pipeline, w, clean_application())
    assert "PROVIDER_ERROR" in codes(r) and r.decision == "ESCALATE"


async def test_no_companies_house_key_is_not_configured(config: Config, mini_index_path: Path) -> None:
    config.settings.companies_house_api_key = None
    config.providers.registry = ["companies_house"]
    pipe = ReviewPipeline(
        config, transport=clean_world().transport(), index_path=mini_index_path, today=TODAY
    )
    r = await pipe.review(clean_application())
    statuses = {c.check: c.status for c in r.checks}
    assert statuses["registry"] == "not_configured" and statuses["ownership"] == "not_configured"
    assert "CHECK_NOT_CONFIGURED" in codes(r)


async def test_same_input_twice_identical(make_pipeline: Any) -> None:
    w = clean_world()
    w.companies["09876543"].officers.append(FakePerson("HARGREAVES, David", 1987, 9, "British"))
    a = await review(make_pipeline, w, clean_application())
    b = await review(make_pipeline, w, clean_application())
    strip = {"trace", "evidence"}
    assert a.model_dump(exclude=strip) == b.model_dump(exclude=strip)
    assert set(a.evidence) == set(b.evidence)


async def test_every_flag_cites_existing_evidence(make_pipeline: Any) -> None:
    w = clean_world()
    w.companies["09876543"].status = "liquidation"
    w.companies["09876543"].officers.append(FakePerson("BOROVKOV, Viktor", 1961, 3))
    r = await review(make_pipeline, w, clean_application())
    for f in r.flags:
        assert all(e in r.evidence for e in f.evidence), f


async def test_audit_log_written(config: Config, mini_index_path: Path) -> None:
    from kyb_tool_mcp.audit import AuditLog

    config.settings.kyb_audit = True
    pipe = ReviewPipeline(
        config, transport=clean_world().transport(), index_path=mini_index_path, today=TODAY
    )
    r = await pipe.review(clean_application())
    rows = AuditLog(config.settings.audit_path).history("clean_01")
    assert rows and rows[0]["decision"] == r.decision and rows[0]["policy_version"] == r.policy_version


async def test_company_screened_against_entities(make_pipeline: Any) -> None:
    w = clean_world()
    w.add_company(
        FakeCompany(
            number="07777777",
            name="NORDSTAR SHIPPING LIMITED",
            officers=[FakePerson("SMITH, Anna", 1980)],
            pscs=[FakePSC("Anna Smith", birth_year=1980)],
        )
    )
    app = {
        "case_id": "c",
        "legal_name": "Nordstar Shipping Ltd",
        "registration_number": "07777777",
        "directors": [{"name": "Anna Smith", "birth_year": 1980}],
        "ubos": [{"name": "Anna Smith", "birth_year": 1980}],
    }
    r = await review(make_pipeline, w, app)
    assert r.decision == "REJECT" and "SANCTIONS_MATCH" in codes(r)
