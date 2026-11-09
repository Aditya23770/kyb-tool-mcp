from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyb_tool_mcp.lists import build_index
from kyb_tool_mcp.providers.companies_house import map_company, ownership_band
from kyb_tool_mcp.providers.gleif import map_record
from kyb_tool_mcp.providers.sanctions.loaders import ofac_sdn, opensanctions_bulk, un_consolidated
from tests.conftest import MINI


def test_ofac_loader() -> None:
    ents = {e.id: e for e in ofac_sdn.load(MINI / "ofac_sdn.xml")}
    assert len(ents) == 6
    v = ents["ofac-1001"]
    assert v.schema_ == "Person" and v.names == ["Viktor Ivanovich BOROVKOV", "Viktor BOROVKOFF"]
    assert v.nationalities == ["Russia"] and v.programs == ["RUSSIA-EO14024"]
    assert ents["ofac-1004"].schema_ == "Vessel"
    assert ents["ofac-1002"].schema_ == "Organization"


def test_un_loader() -> None:
    ents = {e.id: e for e in un_consolidated.load(MINI / "un_consolidated.xml")}
    assert set(ents) == {"un-2001", "un-2002", "un-2003"}
    kim = ents["un-2001"]
    assert "김철수" in kim.names and kim.weak_names == ["Chol"] and str(kim.birth_dates[0]) == "1968-04-02"
    assert [d.year for d in ents["un-2003"].birth_dates] == [1960, 1961, 1962]
    assert ents["un-2002"].schema_ == "Organization"


def test_opensanctions_loader(tmp_path: Path) -> None:
    rows = [
        {
            "id": "Q1",
            "schema": "Person",
            "caption": "Jane Roe",
            "target": True,
            "properties": {
                "name": ["Jane Roe"],
                "alias": ["J. Roe"],
                "birthDate": ["1970-05"],
                "nationality": ["gb"],
                "topics": ["role.pep"],
            },
            "datasets": ["gb_pep"],
        },
        {"id": "Q2", "schema": "Address", "properties": {"full": ["x"]}},
        {"id": "Q3", "schema": "Company", "target": False, "properties": {"name": ["Not A Target Ltd"]}},
    ]
    p = tmp_path / "e.json"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    ents = list(opensanctions_bulk.load(p))
    assert [e.id for e in ents] == ["os-Q1"] and ents[0].topics == ["role.pep"]


def test_build_index_fails_loudly_on_empty_list(tmp_path: Path) -> None:
    empty = tmp_path / "empty.xml"
    empty.write_text("<sdnList></sdnList>")
    target = tmp_path / "idx.sqlite"
    with pytest.raises(RuntimeError, match="0 entities"):
        build_index(["ofac_sdn"], files={"ofac_sdn": empty}, index_path=target)
    assert not target.exists()


def test_companies_house_mapping() -> None:
    rec = map_company(
        {
            "company_number": "1",
            "company_name": "X LTD",
            "company_status": "active",
            "date_of_creation": "2020-01-02",
            "registered_office_address": {
                "premises": "5",
                "address_line_1": "Road",
                "postal_code": "AB1 2CD",
            },
            "sic_codes": ["62012"],
        },
        {
            "items": [
                {
                    "name": "DOE, Jane",
                    "officer_role": "director",
                    "date_of_birth": {"year": 1980, "month": 2},
                },
                {"name": "ACME SECRETARIES LTD", "officer_role": "corporate-secretary"},
            ]
        },
        {
            "items": [
                {
                    "name": "Holdco Ltd",
                    "kind": "corporate-entity-person-with-significant-control",
                    "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                }
            ]
        },
    )
    assert rec.address and rec.address.lines == ["5 Road"]
    assert rec.officers[0].birth_month == 2 and rec.officers[1].is_corporate
    assert rec.pscs[0].is_corporate and rec.pscs[0].ownership_pct_min == 75
    assert ownership_band(["ownership-of-shares-25-to-50-percent", "voting-rights-25-to-50-percent"]) == (
        25,
        50,
    )


def test_gleif_mapping() -> None:
    rec = map_record(
        {
            "id": "LEI1",
            "attributes": {
                "lei": "LEI1",
                "entity": {
                    "legalName": {"name": "ACME LIMITED"},
                    "status": "ACTIVE",
                    "registeredAs": "01234567",
                    "legalAddress": {
                        "addressLines": ["1 Road"],
                        "city": "London",
                        "postalCode": "E1",
                        "country": "GB",
                    },
                },
            },
        }
    )
    assert rec.lei == "LEI1" and rec.status == "active" and not rec.officers_available
