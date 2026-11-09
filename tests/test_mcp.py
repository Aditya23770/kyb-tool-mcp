"""MCP server tests: in-process client lists and calls every tool and the policy resource."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp import Client

from kyb_tool_mcp.config import Config
from kyb_tool_mcp.mcp_server import build_server
from kyb_tool_mcp.testing import FakeWebsite, business_html
from tests.conftest import TODAY, clean_application, clean_world

EXPECTED_TOOLS = {"review_kyb_case", "registry_lookup", "screen_entity", "check_website", "explain_policy"}


def payload(result: Any) -> Any:
    assert not result.is_error, result
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def server(config: Config, index: Path) -> Any:
    world = clean_world()
    world.add_website(
        FakeWebsite(url="https://example-shop.co.uk", html=business_html("Shop", "We sell shoes."))
    )
    return build_server(config, transport=world.transport(), index_path=index, today=TODAY)


async def test_lists_tools_and_resource(config: Config, mini_index_path: Path) -> None:
    async with Client(server(config, mini_index_path)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS
        for t in tools.values():
            assert t.description and len(t.description) > 40
        for name in EXPECTED_TOOLS - {"explain_policy"}:
            assert (
                "not legal advice" in (tools[name].description or "").lower()
                or "decision support" in (tools[name].description or "").lower()
            )
        resources = (await c.list_resources()).resources
        assert any(str(r.uri) == "kyb://policy" for r in resources)
        res = await c.read_resource("kyb://policy")
        assert "severity_points" in res.contents[0].text  # type: ignore[union-attr]
        assert c.instructions and "review_kyb_case" in c.instructions


async def test_review_kyb_case_pipeline_mode(config: Config, mini_index_path: Path) -> None:
    app = clean_application()
    app.pop("case_id")
    async with Client(server(config, mini_index_path)) as c:
        out = payload(await c.call_tool("review_kyb_case", app))
    assert out["decision"] == "APPROVE"
    assert out["policy_version"] and out["disclaimer"]
    assert {c["check"] for c in out["checks"]} >= {"registry", "sanctions", "website"}


async def test_review_kyb_case_minimal_input(config: Config, mini_index_path: Path) -> None:
    async with Client(server(config, mini_index_path)) as c:
        out = payload(
            await c.call_tool(
                "review_kyb_case",
                {"legal_name": "Brightwater Analytics Limited", "registration_number": "9876543"},
            )
        )
    # Directors/owners not declared => they show up as undeclared => ESCALATE, never APPROVE.
    assert out["decision"] == "ESCALATE"
    assert any(f["code"] == "UNDECLARED_DIRECTOR" for f in out["flags"])


async def test_registry_lookup_tool(config: Config, mini_index_path: Path) -> None:
    async with Client(server(config, mini_index_path)) as c:
        out = payload(await c.call_tool("registry_lookup", {"registration_number": "09876543"}))
        assert out["records"][0]["record"]["name"] == "BRIGHTWATER ANALYTICS LIMITED"
        assert out["records"][0]["evidence_id"].startswith("ev_companies_house_")
        err = payload(await c.call_tool("registry_lookup", {}))
        assert "error" in err


async def test_screen_entity_tool(config: Config, mini_index_path: Path) -> None:
    async with Client(server(config, mini_index_path)) as c:
        hit = payload(await c.call_tool("screen_entity", {"name": "Viktor Borovkov", "birth_year": 1961}))
        clear = payload(await c.call_tool("screen_entity", {"name": "Viktor Borovkov", "birth_year": 1995}))
    assert hit["result"] == "hits" and hit["hits"][0]["entity_id"] == "ofac-1001"
    assert clear["result"] == "clear" and clear["cleared"][0]["entity_id"] == "ofac-1001"


async def test_check_website_tool(config: Config, mini_index_path: Path) -> None:
    async with Client(server(config, mini_index_path)) as c:
        out = payload(
            await c.call_tool(
                "check_website", {"url": "example-shop.co.uk", "declared_industry": "footwear retail"}
            )
        )
    assert out["snapshot"]["reachable"] is True and "shoes" in out["snapshot"]["text"]
    assert out["domain"]["registered_on"] == "2012-05-01"


async def test_explain_policy_tool(config: Config, mini_index_path: Path) -> None:
    async with Client(server(config, mini_index_path)) as c:
        out = await c.call_tool("explain_policy", {})
    text = out.content[0].text  # type: ignore[union-attr]
    assert "REJECT" in text and "not legal advice" in text
