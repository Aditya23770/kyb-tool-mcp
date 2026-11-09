"""Local MCP server (stdio). Two modes:

- Pipeline mode: `review_kyb_case` runs every configured check and returns a policy decision.
- Tool mode: `registry_lookup`, `screen_entity`, `check_website` give the client's own LLM raw,
  evidence-backed data; `explain_policy` tells it how decisions are made.

Never print to stdout here: stdout is the MCP transport.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from kyb_tool_mcp import __version__
from kyb_tool_mcp.config import Config, default_policy_text, load_config
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

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def build_server(config: Config | None = None, **pipeline_kwargs: Any) -> MCPServer:
    cfg = config or load_config()
    pipeline = ReviewPipeline(cfg, **pipeline_kwargs)
    server: MCPServer = MCPServer(
        name="kyb-tool-mcp",
        title="KYB Tool",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url="https://aditya23770.github.io/kyb-tool-mcp/",
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
    ) -> dict[str, Any]:
        """Run the full KYB review on a business onboarding application (pipeline mode).

        Checks the registry (status, name, address, age, directors, beneficial owners, SIC industry),
        screens the company, directors and owners against sanctions lists, and checks the website.
        Returns decision (APPROVE | REJECT | ESCALATE), risk_level, score, flags (each citing evidence
        ids), check statuses, AML hits and auto-cleared false positives, and an analyst note.
        Decision support only, not legal advice.
        """
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
        result = await pipeline.review(app)
        return result.model_dump(mode="json")

    @server.tool(annotations=READ_ONLY)
    async def registry_lookup(
        country: str = "GB", registration_number: str | None = None, name: str | None = None
    ) -> dict[str, Any]:
        """Look up a company in the configured registries (Companies House, GLEIF).

        Returns raw canonical records: status, incorporation date, registered address, SIC codes,
        officers and persons with significant control (owners), each with an evidence id.
        Provide a registration number (preferred) or an exact company name. Decision support only.
        """
        if not registration_number and not name:
            return {"error": "provide registration_number or name"}
        return await pipeline.registry_lookup(country, registration_number, name)

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
        subject = ScreeningSubject(
            name=name, type=type, birth_year=birth_year, birth_month=birth_month, nationality=nationality
        )
        return await pipeline.screen(subject)

    @server.tool(annotations=READ_ONLY)
    async def check_website(
        url: str, declared_industry: str | None = None, company_name: str | None = None
    ) -> dict[str, Any]:
        """Fetch a business website and check legitimacy signals.

        Returns reachability, page title and text excerpt (so you can judge what the business sells),
        domain registration date (RDAP), and flags: WEBSITE_UNREACHABLE, PARKED_DOMAIN, NEW_DOMAIN,
        PROHIBITED_GOODS, INDUSTRY_MISMATCH, DOMAIN_NAME_MISMATCH. Decision support only.
        """
        return await pipeline.check_website(url, declared_industry, company_name)

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

    return server


def run_stdio() -> None:
    # Keep stderr readable in MCP client logs: no per-request HTTP lines.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    build_server().run("stdio")
