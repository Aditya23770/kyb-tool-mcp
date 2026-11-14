"""Hosted Streamable HTTP server: per-request Companies House key header, /healthz, host checks.

Runs the real ASGI app in-process (httpx2 ASGI transport); no network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from kyb_tool_mcp.config import Config
from kyb_tool_mcp.mcp_server import build_http_app, hosted_settings, transport_security_for
from kyb_tool_mcp.testing import FakeWorld
from tests.conftest import TODAY, clean_application, clean_world
from tests.test_mcp import EXPECTED_TOOLS, payload

KEY = "SECRET-TEST-KEY-123"
BASE = "http://127.0.0.1:8080"  # loopback Host, accepted by the SDK's localhost protection


def hosted(config: Config) -> Config:
    """The hosted server has no key of its own: callers bring theirs."""
    config.settings = hosted_settings(config.settings).model_copy(update={"companies_house_api_key": None})
    return config


def world_requiring_key() -> FakeWorld:
    w = clean_world()
    w.companies_house_key = KEY
    return w


def app_for(config: Config, index: Path, world: FakeWorld) -> Starlette:
    return build_http_app(hosted(config), transport=world.transport(), index_path=index, today=TODAY)


@asynccontextmanager
async def http(app: Starlette, headers: dict[str, str] | None = None) -> AsyncIterator[httpx2.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers or {}
        ) as client,
    ):
        yield client


@asynccontextmanager
async def mcp_client(app: Starlette, headers: dict[str, str] | None = None) -> AsyncIterator[Client]:
    async with http(app, headers) as hc, Client(streamable_http_client(f"{BASE}/mcp", http_client=hc)) as c:
        yield c


async def test_lists_tools_over_http_and_ctx_is_not_a_tool_argument(
    config: Config, mini_index_path: Path
) -> None:
    async with mcp_client(app_for(config, mini_index_path, clean_world())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS
        for t in tools.values():
            assert "ctx" not in json.dumps(t.input_schema)
        assert c.instructions and "X-Companies-House-Key" in c.instructions


async def test_header_key_is_used_for_registry(config: Config, mini_index_path: Path) -> None:
    app = app_for(config, mini_index_path, world_requiring_key())
    async with mcp_client(app, {"X-Companies-House-Key": KEY}) as c:
        out = payload(await c.call_tool("registry_lookup", {"registration_number": "09876543"}))
        review = payload(await c.call_tool("review_kyb_case", clean_application()))
    assert out["records"] and not out["errors"] and not out["not_configured"]
    statuses = {ch["check"]: ch["status"] for ch in review["checks"]}
    assert statuses["registry"] == "done"
    assert review["decision"] == "APPROVE"


async def test_missing_header_is_not_configured_with_setup_hint(
    config: Config, mini_index_path: Path
) -> None:
    app = app_for(config, mini_index_path, world_requiring_key())
    async with mcp_client(app) as c:
        out = payload(await c.call_tool("registry_lookup", {"registration_number": "09876543"}))
        review = payload(await c.call_tool("review_kyb_case", clean_application()))
    assert any("X-Companies-House-Key" in m for m in out["not_configured"])
    reg = next(ch for ch in review["checks"] if ch["check"] == "registry")
    # Without a key, Companies House is skipped (reported, never silent) and only GLEIF runs. This fake
    # company has no GLEIF record either, so the registry finds nothing and the case escalates.
    assert "X-Companies-House-Key" in (reg["note"] or "")
    assert review["decision"] != "APPROVE"


async def test_wrong_header_key_is_a_provider_error(config: Config, mini_index_path: Path) -> None:
    app = app_for(config, mini_index_path, world_requiring_key())
    async with mcp_client(app, {"X-Companies-House-Key": "wrong-key"}) as c:
        review = payload(await c.call_tool("review_kyb_case", clean_application()))
    codes = {f["code"] for f in review["flags"]}
    assert "PROVIDER_ERROR" in codes and review["decision"] != "APPROVE"


async def test_healthz_ok_with_fresh_index(config: Config, mini_index_path: Path) -> None:
    async with http(app_for(config, mini_index_path, clean_world())) as hc:
        r = await hc.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["index"] == "ok" and body["version"]


async def test_healthz_fails_when_index_missing_or_stale(
    config: Config, mini_index_path: Path, tmp_path: Path
) -> None:
    async with http(app_for(config, tmp_path / "nope.sqlite", clean_world())) as hc:
        missing = await hc.get("/healthz")
    assert missing.status_code == 503 and missing.json()["index"] == "missing"
    assert str(tmp_path) not in missing.text

    config.settings = config.settings.model_copy(update={"kyb_index_max_age_hours": -1})
    async with http(app_for(config, mini_index_path, clean_world())) as hc:
        stale = await hc.get("/healthz")
    assert stale.status_code == 503 and stale.json()["index"] == "stale"


async def test_allowed_hosts_rejects_other_host_headers(config: Config, mini_index_path: Path) -> None:
    config.settings = config.settings.model_copy(update={"kyb_allowed_hosts": ["kyb.example.org"]})
    app = app_for(config, mini_index_path, clean_world())
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with http(app) as hc:
        r = await hc.post("/mcp", json=body, headers={"accept": "application/json, text/event-stream"})
    assert r.status_code in (400, 403, 421)


async def test_loopback_server_blocks_dns_rebinding(config: Config, mini_index_path: Path) -> None:
    """A page on another origin that rebinds its DNS to 127.0.0.1 must not reach the local server."""
    app = app_for(config, mini_index_path, clean_world())
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    accept = {"accept": "application/json, text/event-stream"}
    async with http(app) as hc:
        bad_host = await hc.post("/mcp", json=body, headers={**accept, "host": "attacker.example"})
        bad_origin = await hc.post("/mcp", json=body, headers={**accept, "origin": "http://attacker.example"})
    assert bad_host.status_code in (400, 403, 421)
    assert bad_origin.status_code in (400, 403, 421)


def test_transport_security_choices() -> None:
    assert transport_security_for([], "127.0.0.1") is None  # SDK localhost protection applies
    container = transport_security_for([], "0.0.0.0")
    assert container is not None and container.enable_dns_rebinding_protection is False
    listed = transport_security_for(["kyb.example.org"], "0.0.0.0")
    assert listed is not None and listed.enable_dns_rebinding_protection is True
    assert listed.allowed_hosts == ["kyb.example.org"]
    assert listed.allowed_origins == ["https://kyb.example.org"]


def test_hosted_settings_turn_off_audit_and_cache(config: Config) -> None:
    s = hosted_settings(config.settings)
    assert s.kyb_audit is False and s.kyb_http_cache is False
