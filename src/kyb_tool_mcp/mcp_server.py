"""MCP server. Two transports, same tools:

- stdio (`kyb-tool-mcp`): the client starts the server on the user's machine.
- Streamable HTTP (`kyb-tool-mcp serve --http`): a hosted connector. Each caller sends their own
  Companies House key in the `X-Companies-House-Key` request header; it is used for that call only and
  never stored, logged or returned.

Two modes:
- Pipeline mode: `review_kyb_case` runs every configured check and returns a policy decision.
- Tool mode: `registry_lookup`, `screen_entity`, `check_website` give the client's own LLM raw,
  evidence-backed data; `explain_policy` tells it how decisions are made.

Never print to stdout here: stdout is the MCP transport. Log to stderr.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from kyb_tool_mcp import __version__
from kyb_tool_mcp.config import Config, Settings, default_policy_text, load_config
from kyb_tool_mcp.logs import TraceMiddleware, setup_logging
from kyb_tool_mcp.matching.index import IndexMissingError, SanctionsIndex
from kyb_tool_mcp.pipeline import ReviewPipeline
from kyb_tool_mcp.policy import explain_policy as _explain_policy
from kyb_tool_mcp.schemas.application import Application, DeclaredUBO, Person
from kyb_tool_mcp.schemas.screening import ScreeningSubject

DISCLAIMER = "Results are decision support, not legal advice; the operator remains responsible for compliance decisions."

INSTRUCTIONS = f"""kyb-tool-mcp performs KYB (Know Your Business) checks on UK companies using public data:
Companies House + GLEIF (registry), OFAC SDN + UN consolidated sanctions lists (local index), and the
business website (+ RDAP domain age).

Use `review_kyb_case` for a full, deterministic APPROVE / REJECT / ESCALATE decision. Every flag cites
evidence ids. Never override the decision: you may explain it, summarise it, or suggest next steps.
Use the raw tools (`registry_lookup`, `screen_entity`, `check_website`) to investigate details, and
`explain_policy` to learn the rules. A check reported as "not_configured" was NOT performed.
{DISCLAIMER}"""

KEY_HEADER = "x-companies-house-key"
CH_KEY_URL = "https://developer.company-information.service.gov.uk/"
HOSTED_KEY_HINT = (
    f"add your free Companies House key ({CH_KEY_URL}) as the X-Companies-House-Key request header "
    "in your connector settings"
)
HOSTED_INSTRUCTIONS = (
    "\nHosted connector: registry checks use the caller's own free Companies House key, sent in the "
    "X-Companies-House-Key request header of the connector settings. If the registry check is "
    '"not_configured", tell the user how to add that header; never ask them to paste the key into the chat.'
)
WEBSITE_URL = "https://trustledger.vercel.app/"

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
log: logging.Logger = logging.getLogger("kyb_tool_mcp")


def _log_tool(tool: str, t0: float, **fields: Any) -> None:
    """One structured line per tool call. Outcome only: never names, URLs, inputs or keys."""
    log.info(
        "tool_call %s",
        tool,
        extra={"fields": {"event": "tool_call", "tool": tool, "duration_ms": _ms(t0), **fields}},
    )


def _log_tool_error(tool: str, t0: float, err: Exception) -> None:
    log.error(
        "tool_error %s",
        tool,
        exc_info=err,
        extra={
            "fields": {
                "event": "tool_error",
                "tool": tool,
                "duration_ms": _ms(t0),
                "error_type": type(err).__name__,
            }
        },
    )


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _header_key(ctx: Context | None) -> str | None:
    """The caller's Companies House key from the request header (HTTP only; None on stdio).

    The value is passed straight to the pipeline for this call. Never log, store or return it.
    """
    if ctx is None:
        return None
    try:
        headers = ctx.headers
    except ValueError:  # called outside a request
        return None
    if not headers:
        return None
    value = (headers.get(KEY_HEADER) or "").strip()
    return value or None


def index_health(settings: Settings, index_path: Path | None = None) -> tuple[bool, dict[str, Any]]:
    """Is the sanctions index present and fresh? Returns (ok, public status). No settings or paths."""
    try:
        idx = SanctionsIndex(index_path or settings.index_path)
    except IndexMissingError:
        return False, {"index": "missing"}
    meta = idx.meta
    idx.close()
    built = str(meta.get("built_at", ""))
    try:
        age_h = (datetime.now(UTC) - datetime.fromisoformat(built)).total_seconds() / 3600
    except ValueError:
        return False, {"index": "unknown build time"}
    ok = age_h <= settings.kyb_index_max_age_hours
    return ok, {
        "index": "ok" if ok else "stale",
        "index_built_at": built,
        "index_entities": meta.get("entities"),
    }


def build_server(config: Config | None = None, *, hosted: bool = False, **pipeline_kwargs: Any) -> MCPServer:
    cfg = config or load_config()
    if hosted:
        pipeline_kwargs.setdefault("missing_key_hint", HOSTED_KEY_HINT)
    pipeline = ReviewPipeline(cfg, **pipeline_kwargs)
    server: MCPServer = MCPServer(
        name="kyb-tool-mcp",
        title="TrustLedger KYB",
        instructions=INSTRUCTIONS + (HOSTED_INSTRUCTIONS if hosted else ""),
        version=__version__,
        website_url=WEBSITE_URL,
    )

    @server.tool(annotations=READ_ONLY)
    async def review_kyb_case(
        legal_name: str,
        registration_number: str | None = None,
        country: str = "GB",
        case_id: str | None = None,
        trading_name: str | None = None,
        registered_address: str | None = None,
        postcode: str | None = None,
        declared_industry: str | None = None,
        website: str | None = None,
        directors: list[Person] | None = None,
        ubos: list[DeclaredUBO] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Run the full KYB review on a business onboarding application (pipeline mode).

        Checks the registry (status, name, address, age, directors, beneficial owners, SIC industry),
        screens the company, directors and owners against sanctions lists, and checks the website.
        Returns decision (APPROVE | REJECT | ESCALATE), risk_level, score, flags (each citing evidence
        ids), check statuses, AML hits and auto-cleared false positives, and an analyst note.
        Decision support only, not legal advice.
        """
        t0 = time.perf_counter()
        key = _header_key(ctx)
        app = Application(
            case_id=case_id or f"mcp-{(registration_number or legal_name)}".replace(" ", "-").lower(),
            legal_name=legal_name,
            trading_name=trading_name,
            country=country,
            registration_number=registration_number,
            registered_address=registered_address,
            postcode=postcode,
            declared_industry=declared_industry,
            website=website,
            directors=directors or [],
            ubos=ubos or [],
        )
        try:
            result = await pipeline.review(app, companies_house_api_key=key)
        except Exception as e:
            _log_tool_error("review_kyb_case", t0, e)
            raise
        _log_tool(
            "review_kyb_case",
            t0,
            key_supplied=key is not None,
            decision=result.decision,
            risk_level=result.risk_level,
            score=result.score,
            flags=sorted({f.code for f in result.flags}),
            checks={c.check: c.status for c in result.checks},
            directors=len(app.directors),
            ubos=len(app.ubos),
            has_website=app.website is not None,
        )
        return result.model_dump(mode="json")

    @server.tool(annotations=READ_ONLY)
    async def registry_lookup(
        country: str = "GB",
        registration_number: str | None = None,
        name: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Look up a company in the configured registries (Companies House, GLEIF).

        Returns raw canonical records: status, incorporation date, registered address, SIC codes,
        officers and persons with significant control (owners), each with an evidence id.
        Provide a registration number (preferred) or an exact company name. Decision support only.
        """
        if not registration_number and not name:
            return {"error": "provide registration_number or name"}
        t0 = time.perf_counter()
        key = _header_key(ctx)
        try:
            out = await pipeline.registry_lookup(
                country, registration_number, name, companies_house_api_key=key
            )
        except Exception as e:
            _log_tool_error("registry_lookup", t0, e)
            raise
        _log_tool(
            "registry_lookup",
            t0,
            key_supplied=key is not None,
            country=country,
            providers=[r["provider"] for r in out["records"]],
            errors=len(out["errors"]),
            not_configured=bool(out["not_configured"]),
        )
        return out

    @server.tool(annotations=READ_ONLY)
    async def screen_entity(
        name: str,
        type: Literal["person", "company"] = "person",
        birth_year: int | None = None,
        birth_month: int | None = None,
        nationality: str | None = None,
    ) -> dict[str, Any]:
        """Screen one person or company against the local sanctions index (OFAC SDN, UN; optional OpenSanctions).

        Returns hits (TRUE_MATCH or NEEDS_REVIEW) and auto-cleared false positives, each with reasons
        (name similarity, birth-date relation, nationality, schema). Supplying birth_year/month and
        nationality lets false positives be cleared. Decision support only, not legal advice.
        """
        t0 = time.perf_counter()
        subject = ScreeningSubject(
            name=name, type=type, birth_year=birth_year, birth_month=birth_month, nationality=nationality
        )
        try:
            out = await pipeline.screen(subject)
        except Exception as e:
            _log_tool_error("screen_entity", t0, e)
            raise
        _log_tool(
            "screen_entity",
            t0,
            subject_type=type,
            has_dob=birth_year is not None,
            result=out.get("result"),
            hits=len(out.get("hits", [])),
            cleared=len(out.get("cleared", [])),
        )
        return out

    @server.tool(annotations=READ_ONLY)
    async def check_website(
        url: str, declared_industry: str | None = None, company_name: str | None = None
    ) -> dict[str, Any]:
        """Fetch a business website and check legitimacy signals.

        Returns reachability, page title and text excerpt (so you can judge what the business sells),
        domain registration date (RDAP), and flags: WEBSITE_UNREACHABLE, PARKED_DOMAIN, NEW_DOMAIN,
        PROHIBITED_GOODS, INDUSTRY_MISMATCH, DOMAIN_NAME_MISMATCH, WEBSITE_BLOCKS_AUTOMATION (online but
        refuses bots, so content was not checked). Decision support only.
        """
        t0 = time.perf_counter()
        try:
            out = await pipeline.check_website(url, declared_industry, company_name)
        except Exception as e:
            _log_tool_error("check_website", t0, e)
            raise
        _log_tool("check_website", t0, flags=sorted({f.get("code", "") for f in out.get("flags", [])}))
        return out

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    def explain_policy() -> str:
        """Explain how kyb-tool-mcp makes decisions: flags, severities, thresholds, decision rules."""
        return _explain_policy(cfg.policy) + f"\n{DISCLAIMER}"

    @server.resource(
        "kyb://policy",
        name="policy",
        description="Current decision policy (YAML)",
        mime_type="application/yaml",
    )
    def policy_resource() -> str:
        path = cfg.settings.kyb_policy_file
        return path.read_text(encoding="utf-8") if path else default_policy_text()

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def healthz(_: Request) -> Response:
        """Liveness and freshness: 503 when the sanctions index is missing or stale. Public data only."""
        ok, status = index_health(cfg.settings, pipeline.index_path)
        body = {
            "status": "ok" if ok else "unhealthy",
            "version": __version__,
            "policy_version": cfg.policy.version,
        }
        return JSONResponse({**body, **status}, status_code=200 if ok else 503)

    return server


def _quiet_http_logs() -> None:
    # Keep stderr readable: no per-request HTTP client lines.
    for noisy in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def hosted_settings(settings: Settings | None = None) -> Settings:
    """Settings for the shared HTTP server: no audit log, no HTTP cache (many users, ephemeral disk)."""
    s = settings or Settings()
    return s.model_copy(update={"kyb_audit": False, "kyb_http_cache": False})


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def transport_security_for(allowed_hosts: list[str], bind_host: str) -> TransportSecuritySettings | None:
    """Host/Origin checks against DNS rebinding.

    - `KYB_ALLOWED_HOSTS` set: only those Host headers (and https Origins) are accepted.
    - Bound to loopback: None, so the MCP SDK applies its localhost-only protection. Never disable it
      there, or any web page could reach the local server through DNS rebinding.
    - Bound to all interfaces with no list (a container behind Cloud Run's front end): no Host check.
    """
    if allowed_hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"https://{h}" for h in allowed_hosts],
        )
    if bind_host in LOOPBACK_HOSTS:
        return None
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def build_http_app(
    config: Config | None = None, *, host: str = "127.0.0.1", **pipeline_kwargs: Any
) -> Starlette:
    """The hosted Streamable HTTP app: `/mcp` (stateless, JSON responses) and `/healthz`."""
    cfg = config or load_config(hosted_settings())
    security = transport_security_for(cfg.settings.kyb_allowed_hosts, host)
    server = build_server(cfg, hosted=True, **pipeline_kwargs)
    app = server.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=security, host=host
    )
    app.add_middleware(TraceMiddleware)  # Cloud Run trace id -> log lines
    return app


def run_stdio() -> None:
    _quiet_http_logs()
    build_server().run("stdio")


def run_http(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Serve the hosted connector. Logs go to stderr (JSON for Google Cloud Logging when
    KYB_LOG_FORMAT=json). Cloud Run already records each HTTP request, so uvicorn's access log is off
    in JSON mode; locally it shows method and path only (never headers)."""
    import uvicorn

    cfg = load_config(hosted_settings())
    s = cfg.settings
    setup_logging(s.kyb_log_format, s.kyb_gcp_project)
    ok, status = index_health(s)
    log.info(
        "startup",
        extra={
            "fields": {
                "event": "startup",
                "version": __version__,
                "policy_version": cfg.policy.version,
                "host": host,
                "port": port,
                "allowed_hosts_set": bool(s.kyb_allowed_hosts),
                "server_key_set": s.companies_house_api_key is not None,
                **status,
            }
        },
    )
    if not ok:
        log.warning("sanctions index not healthy", extra={"fields": {"event": "index_unhealthy", **status}})
    uvicorn.run(
        build_http_app(cfg, host=host),
        host=host,
        port=port,
        log_config=None,  # keep our handlers/format
        access_log=s.kyb_log_format != "json",
        proxy_headers=True,
    )
