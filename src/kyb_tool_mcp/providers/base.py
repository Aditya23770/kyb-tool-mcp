"""Provider protocols, shared HTTP context and the provider registry.

Providers are dumb adapters: fetch + map to canonical schemas. No business logic.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from kyb_tool_mcp.config import Config
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.registry import CompanyRecord
from kyb_tool_mcp.schemas.screening import Candidate, ScreeningSubject
from kyb_tool_mcp.schemas.website import DomainInfo, WebsiteSnapshot


class ProviderError(Exception):
    """Raised by providers on any failure; the pipeline converts it to a PROVIDER_ERROR flag."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message


class NotFoundError(ProviderError):
    pass


# ---- protocols -----------------------------------------------------------------


@runtime_checkable
class RegistryProvider(Protocol):
    name: str

    def supports(self, country: str) -> bool: ...

    async def lookup(
        self, country: str, reg_no: str | None, name: str | None
    ) -> Evidence[CompanyRecord] | None: ...


@runtime_checkable
class ScreeningProvider(Protocol):
    name: str
    topics: set[str]

    async def screen(self, subject: ScreeningSubject) -> Evidence[list[Candidate]]: ...


@runtime_checkable
class WebsiteProvider(Protocol):
    name: str

    async def fetch(self, url: str) -> Evidence[WebsiteSnapshot]: ...


@runtime_checkable
class DomainProvider(Protocol):
    name: str

    async def domain(self, url: str) -> Evidence[DomainInfo]: ...


# ---- HTTP ------------------------------------------------------------------------


class RateLimiter:
    """Simple async token bucket."""

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class HttpCache:
    """Tiny SQLite GET cache (respects provider rate limits; disabled in tests)."""

    def __init__(self, path: Path, ttl: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, at REAL, status INTEGER, body BLOB, headers TEXT)"
        )
        self.ttl = ttl

    def get(self, key: str) -> tuple[int, bytes, dict[str, str]] | None:
        row = self.conn.execute("SELECT at, status, body, headers FROM cache WHERE key=?", (key,)).fetchone()
        if not row or time.time() - row[0] > self.ttl:
            return None
        return int(row[1]), bytes(row[2]), json.loads(row[3])

    def put(self, key: str, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
            (key, time.time(), status, body, json.dumps(headers)),
        )
        self.conn.commit()


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class ProviderContext:
    """Shared by all providers in one pipeline run."""

    config: Config
    client: httpx.AsyncClient
    cache: HttpCache | None = None
    limiters: dict[str, RateLimiter] = field(default_factory=dict)

    @classmethod
    def create(cls, config: Config, transport: httpx.AsyncBaseTransport | None = None) -> ProviderContext:
        s = config.settings
        client = httpx.AsyncClient(
            transport=transport,
            timeout=s.kyb_http_timeout,
            headers={"User-Agent": s.user_agent},
            follow_redirects=False,
        )
        cache = (
            HttpCache(s.cache_path, s.kyb_http_cache_ttl)
            if (s.kyb_http_cache and transport is None)
            else None
        )
        return cls(config=config, client=client, cache=cache)

    async def aclose(self) -> None:
        await self.client.aclose()

    def limiter(self, name: str, rate: float, burst: int) -> RateLimiter:
        if name not in self.limiters:
            self.limiters[name] = RateLimiter(rate, burst)
        return self.limiters[name]

    async def get(
        self,
        provider: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        limiter: RateLimiter | None = None,
        use_cache: bool = True,
        attempts: int = 3,
    ) -> HttpResponse:
        """GET with retries on 429/5xx/transport errors, optional cache. Returns any final status."""
        req = self.client.build_request("GET", url, params=params)
        key = str(req.url)
        if use_cache and self.cache is not None:
            hit = self.cache.get(key)
            if hit:
                return HttpResponse(hit[0], hit[1], hit[2], key, from_cache=True)
        resp: httpx.Response | None = None
        last_error = ""
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(min(0.5 * 2**attempt, 8))
            try:
                if limiter:
                    await limiter.acquire()
                resp = await self.client.get(url, params=params, auth=auth)
            except httpx.TransportError as e:
                last_error = f"{type(e).__name__}: {e}"
                resp = None
                continue
            except httpx.HTTPError as e:
                raise ProviderError(provider, f"{type(e).__name__}: {e}") from e
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code} from {key}"
                continue
            break
        else:
            raise ProviderError(provider, last_error or "request failed")
        assert resp is not None
        out = HttpResponse(resp.status_code, resp.content, dict(resp.headers), key)
        if use_cache and self.cache is not None and resp.status_code in (200, 404):
            self.cache.put(
                key, resp.status_code, resp.content, {"content-type": resp.headers.get("content-type", "")}
            )
        return out
