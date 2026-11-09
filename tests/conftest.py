"""Shared fixtures: an offline config, a mini sanctions index and a fake world."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kyb_tool_mcp.config import Config, Settings, load_config
from kyb_tool_mcp.lists import build_index
from kyb_tool_mcp.matching.index import SanctionsIndex
from kyb_tool_mcp.pipeline import ReviewPipeline
from kyb_tool_mcp.testing import FakeCompany, FakePerson, FakePSC, FakeWebsite, FakeWorld, business_html

FIXTURES = Path(__file__).parent / "fixtures"
MINI = FIXTURES / "sanctions_mini"
TODAY = date(2026, 11, 1)


@pytest.fixture(scope="session")
def mini_index_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("index") / "sanctions.sqlite"
    settings = Settings(_env_file=None, kyb_data_dir=path.parent)  # type: ignore[call-arg]
    build_index(
        ["ofac_sdn", "un_consolidated"],
        settings=settings,
        files={"ofac_sdn": MINI / "ofac_sdn.xml", "un_consolidated": MINI / "un_consolidated.xml"},
        index_path=path,
    )
    return path


@pytest.fixture(scope="session")
def mini_index(mini_index_path: Path) -> SanctionsIndex:
    return SanctionsIndex(mini_index_path)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        companies_house_api_key="test-key",
        kyb_data_dir=tmp_path,
        kyb_audit=False,
        kyb_http_cache=False,
    )
    return load_config(settings)


def clean_world() -> FakeWorld:
    """One clean, active UK company with a matching website."""
    w = FakeWorld()
    w.add_company(
        FakeCompany(
            number="09876543",
            name="BRIGHTWATER ANALYTICS LIMITED",
            incorporated="2016-04-12",
            address={
                "premises": "12",
                "address_line_1": "Canal Street",
                "locality": "Manchester",
                "postal_code": "M1 3HE",
            },
            sic_codes=["62012"],
            officers=[
                FakePerson("HOLLOWAY, Sarah Jane", 1979, 6, "British"),
                FakePerson("OKAFOR, Daniel", 1983, 2, "British"),
            ],
            pscs=[
                FakePSC(
                    "Mrs Sarah Jane Holloway",
                    birth_year=1979,
                    birth_month=6,
                    nationality="British",
                    natures_of_control=["ownership-of-shares-50-to-75-percent"],
                ),
                FakePSC(
                    "Mr Daniel Okafor",
                    birth_year=1983,
                    birth_month=2,
                    nationality="British",
                    natures_of_control=["ownership-of-shares-25-to-50-percent"],
                ),
            ],
        )
    )
    w.add_website(
        FakeWebsite(
            url="https://brightwater-analytics.co.uk",
            html=business_html(
                "Brightwater Analytics",
                "Brightwater builds business software and data analytics dashboards. "
                "We develop custom software for retailers and logistics firms.",
            ),
        )
    )
    return w


def clean_application() -> dict[str, object]:
    return {
        "case_id": "clean_01",
        "legal_name": "Brightwater Analytics Ltd",
        "country": "GB",
        "registration_number": "09876543",
        "registered_address": "12 Canal Street, Manchester",
        "postcode": "M1 3HE",
        "declared_industry": "business software development",
        "website": "https://brightwater-analytics.co.uk",
        "directors": [
            {"name": "Sarah Jane Holloway", "birth_year": 1979, "birth_month": 6, "nationality": "GB"},
            {"name": "Daniel Okafor", "birth_year": 1983, "birth_month": 2, "nationality": "GB"},
        ],
        "ubos": [
            {"name": "Sarah Holloway", "birth_year": 1979, "birth_month": 6, "ownership_pct": 60},
            {"name": "Daniel Okafor", "birth_year": 1983, "birth_month": 2, "ownership_pct": 40},
        ],
    }


@pytest.fixture
def make_pipeline(config: Config, mini_index_path: Path):  # type: ignore[no-untyped-def]
    def _make(world: FakeWorld) -> ReviewPipeline:
        return ReviewPipeline(config, transport=world.transport(), index_path=mini_index_path, today=TODAY)

    return _make
