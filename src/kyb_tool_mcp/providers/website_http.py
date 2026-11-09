"""Website fetcher: timeouts, manual redirect following (max 5), size cap, text extraction."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from kyb_tool_mcp.providers.base import ProviderContext
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.website import WebsiteSnapshot

MAX_REDIRECTS = 5
MAX_TEXT_CHARS = 20_000


class _TextExtractor(HTMLParser):
    SKIP = frozenset({"script", "style", "noscript", "svg", "template", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = ((self.title or "") + data).strip()
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def extract_text(html: str) -> tuple[str | None, str]:
    p = _TextExtractor()
    with contextlib.suppress(Exception):  # HTMLParser is lenient; never fail on bad markup
        p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    return p.title, text


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def check_target(url: str, resolve: bool = True) -> str | None:
    """Return a reason if `url` must not be fetched (SSRF guard), else None.

    Only http(s) is allowed. Literal IPs are always checked; hostnames are resolved (when
    `resolve`) and rejected if ANY address is loopback, private, link-local (incl. cloud
    metadata 169.254.169.254), multicast or reserved.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return f"scheme '{parts.scheme}' not allowed"
    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return "no host"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return f"internal host '{host}'"
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return f"internal address {literal}" if _blocked_ip(literal) else None
    if not resolve:
        return None
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None  # unresolvable: let the fetch fail normally -> WEBSITE_UNREACHABLE
    for info in infos:
        addr = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if _blocked_ip(addr):
            return f"'{host}' resolves to internal address {addr}"
    return None


class WebsiteHttpProvider:
    name = "website_http"

    def __init__(self, ctx: ProviderContext, max_bytes: int = 2_000_000, timeout: float = 15) -> None:
        self.ctx = ctx
        self.max_bytes = max_bytes
        self.timeout = timeout

    async def fetch(self, url: str) -> Evidence[WebsiteSnapshot]:
        snap = await self._fetch(url)
        return Evidence[WebsiteSnapshot].build(
            provider=self.name,
            kind="website.snapshot",
            request={"url": url},
            payload=snap,
            raw={
                "status": snap.status_code,
                "final_url": snap.final_url,
                "text": snap.text,
                "error": snap.error,
            },
        )

    async def _fetch(self, url: str) -> WebsiteSnapshot:
        current = url
        redirects: list[str] = []
        try:
            for _ in range(MAX_REDIRECTS + 1):
                blocked = await check_target(current, resolve=self.ctx.resolve_dns)
                if blocked:
                    # SSRF guard: never fetch internal targets, including via redirects.
                    return WebsiteSnapshot(
                        url=url,
                        final_url=current,
                        reachable=False,
                        error=f"blocked: {blocked}",
                        redirects=redirects,
                    )
                async with self.ctx.client.stream("GET", current, timeout=self.timeout) as r:
                    if r.is_redirect and "location" in r.headers:
                        redirects.append(current)
                        current = urljoin(current, r.headers["location"])
                        continue
                    body = bytearray()
                    truncated = False
                    async for chunk in r.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= self.max_bytes:
                            truncated = True
                            break
                    ctype = r.headers.get("content-type", "")
                    status = r.status_code
                    break
            else:
                return WebsiteSnapshot(
                    url=url,
                    final_url=current,
                    reachable=False,
                    error="too many redirects",
                    redirects=redirects,
                )
        except httpx.HTTPError as e:
            # Transport failure of the *target* site is a finding (unreachable), not a provider error.
            return WebsiteSnapshot(
                url=url,
                final_url=current,
                reachable=False,
                error=f"{type(e).__name__}: {e}",
                redirects=redirects,
            )
        html = bytes(body).decode(r.encoding or "utf-8", errors="replace")
        title, text = extract_text(html) if "html" in ctype or "<html" in html[:500].lower() else (None, html)
        return WebsiteSnapshot(
            url=url,
            final_url=current,
            reachable=200 <= status < 400,
            status_code=status,
            error=None if status < 400 else f"HTTP {status}",
            content_type=ctype,
            title=title,
            text=text[:MAX_TEXT_CHARS],
            word_count=len(text.split()),
            redirects=redirects,
            truncated=truncated,
        )
