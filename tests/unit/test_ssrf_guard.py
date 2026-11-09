"""SSRF guard for website fetching: internal targets are never requested, including via redirects."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kyb_tool_mcp.providers.website_http import check_target
from kyb_tool_mcp.testing import FakeWebsite, business_html
from tests.conftest import clean_world


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8080/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.10/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://printer.local/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://example.com/",
    ],
)
async def test_internal_targets_blocked(url: str) -> None:
    assert await check_target(url, resolve=False) is not None


@pytest.mark.parametrize("url", ["https://example.com/", "http://93.184.215.14/", "https://www.gov.uk/"])
async def test_public_targets_allowed(url: str) -> None:
    assert await check_target(url, resolve=False) is None


async def test_hostname_resolving_to_loopback_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def fake_getaddrinfo(host: str, *a: Any, **k: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    reason = await check_target("https://rebind.attacker.example/", resolve=True)
    assert reason and "127.0.0.1" in reason


async def test_check_website_never_requests_internal_url(make_pipeline: Any) -> None:
    world = clean_world()
    pipe = make_pipeline(world)
    out = await pipe.check_website("http://169.254.169.254/latest/meta-data/")
    assert out["snapshot"]["reachable"] is False and out["snapshot"]["error"].startswith("blocked")
    assert not any("169.254.169.254" in u for u in world.requests)
    assert any(f["code"] == "WEBSITE_UNREACHABLE" for f in out["flags"])


async def test_redirect_to_internal_address_blocked(make_pipeline: Any) -> None:
    world = clean_world()
    world.add_website(FakeWebsite(url="https://redirector.example", html=business_html("x", "y")))
    original = world.handle

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.host == "redirector.example":
            world.requests.append(str(req.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1:6379/"})
        return original(req)

    world.handle = handle  # type: ignore[method-assign]
    out = await make_pipeline(world).check_website("https://redirector.example/")
    assert out["snapshot"]["error"].startswith("blocked")
    assert not any("127.0.0.1" in u for u in world.requests)
