"""ReviewPipeline: orchestrates registry, screening and website checks, then applies policy.

Design rules (see PLAN.md §4):
- deterministic core; the policy alone decides;
- every flag cites evidence that exists in the EvidenceStore;
- a check with no provider => "not_configured" + CHECK_NOT_CONFIGURED, never a silent pass;
- provider errors => PROVIDER_ERROR (high), never swallowed (fail closed).
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from kyb_tool_mcp import __version__
from kyb_tool_mcp.audit import AuditLog
from kyb_tool_mcp.checks.registry_checks import ownership_flags, registry_flags
from kyb_tool_mcp.checks.screening_checks import adjudicate_candidates, build_subjects, screening_flags
from kyb_tool_mcp.checks.website_checks import website_flags
from kyb_tool_mcp.config import Config, load_config
from kyb_tool_mcp.evidence import EvidenceStore, validate_flags
from kyb_tool_mcp.matching.index import IndexMissingError, SanctionsIndex
from kyb_tool_mcp.note import analyst_note
from kyb_tool_mcp.policy import apply_severity_overrides, decide, sort_flags
from kyb_tool_mcp.providers.base import ProviderContext, ProviderError, RegistryProvider
from kyb_tool_mcp.providers.companies_house import CompaniesHouseProvider
from kyb_tool_mcp.providers.gleif import GleifProvider
from kyb_tool_mcp.providers.rdap import RdapProvider
from kyb_tool_mcp.providers.sanctions.local_index import LocalIndexScreeningProvider
from kyb_tool_mcp.providers.website_http import WebsiteHttpProvider
from kyb_tool_mcp.schemas.application import Application
from kyb_tool_mcp.schemas.decision import AmlInfo, CheckStatus, Flag, Hit, ReviewResult, Severity, TraceStep
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.registry import CompanyRecord
from kyb_tool_mcp.schemas.screening import HitOutcome, ScreeningSubject
from kyb_tool_mcp.schemas.website import DomainInfo, WebsiteSnapshot

LOCAL_LIST_PROVIDERS = {"ofac_sdn", "un_consolidated", "opensanctions_bulk"}


class _Run:
    """Mutable state of one review."""

    def __init__(self) -> None:
        self.store = EvidenceStore()
        self.flags: list[Flag] = []
        self.checks: dict[str, CheckStatus] = {}
        self.trace: list[TraceStep] = []

    def error_flag(self, check: str, err: Exception, provider: str) -> None:
        ev = Evidence[dict[str, Any]].build(
            provider=provider, kind="provider.error", request={"check": check}, payload={"error": str(err)}
        )
        self.store.add(ev)
        self.flags.append(
            Flag(
                code="PROVIDER_ERROR",
                severity=Severity.high,
                evidence=[ev.id],
                check=check,
                detail=f"{provider} failed during {check} check: {err}",
            )
        )

    def not_configured(self, check: str, note: str) -> None:
        self.checks[check] = CheckStatus(check=check, status="not_configured", note=note)
        self.flags.append(
            Flag(
                code="CHECK_NOT_CONFIGURED",
                severity=Severity.low,
                check=check,
                detail=f"{check} check not configured: {note}",
            )
        )


class ReviewPipeline:
    def __init__(
        self,
        config: Config | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        index_path: Path | None = None,
        today: date | None = None,
        audit: bool | None = None,
    ) -> None:
        self.config = config or load_config()
        self.transport = transport
        self.index_path = index_path or self.config.settings.index_path
        self.today = today
        self.audit_enabled = self.config.settings.kyb_audit if audit is None else audit
        self._index: SanctionsIndex | None = None

    # ---- providers -----------------------------------------------------------
    def _registry_providers(
        self, ctx: ProviderContext, country: str
    ) -> tuple[list[RegistryProvider], list[str]]:
        providers: list[RegistryProvider] = []
        missing: list[str] = []
        for name in self.config.providers.registry:
            if name == "companies_house":
                key = self.config.settings.companies_house_api_key
                if not key:
                    missing.append("companies_house (set COMPANIES_HOUSE_API_KEY)")
                    continue
                p: RegistryProvider = CompaniesHouseProvider(ctx, key)
            elif name == "gleif":
                p = GleifProvider(ctx)
            else:
                missing.append(f"{name} (unknown provider)")
                continue
            if p.supports(country):
                providers.append(p)
        return providers, missing

    def index(self) -> SanctionsIndex:
        if self._index is None:
            self._index = SanctionsIndex(self.index_path)
        return self._index

    def _screening_providers(self, names: list[str]) -> list[LocalIndexScreeningProvider]:
        unknown = [n for n in names if n not in LOCAL_LIST_PROVIDERS]
        if unknown:
            raise ProviderError("screening", f"unknown screening provider(s): {unknown}")
        idx = self.index()
        absent = [n for n in names if n not in idx.sources()]
        if absent:
            raise ProviderError(
                "screening",
                f"list(s) {absent} not in local index; run `kyb-tool-mcp update-lists --sources ...`",
            )
        return [LocalIndexScreeningProvider(n, idx, self.config.policy.screening) for n in names]

    def _today(self) -> date:
        return self.today or date.today()

    # ---- building blocks (also exposed as raw MCP tools) ----------------------
    async def lookup_registry(
        self, ctx: ProviderContext, run: _Run, app: Application
    ) -> Evidence[CompanyRecord] | None:
        t0 = time.perf_counter()
        providers, missing = self._registry_providers(ctx, app.country)
        if not providers:
            run.not_configured("registry", "; ".join(missing) or f"no registry provider for {app.country}")
            return None
        results = await asyncio.gather(
            *(p.lookup(app.country, app.registration_number, app.legal_name) for p in providers),
            return_exceptions=True,
        )
        records: list[Evidence[CompanyRecord]] = []
        errors = 0
        for p, res in zip(providers, results, strict=True):
            if isinstance(res, BaseException):
                if not isinstance(res, Exception):
                    raise res
                errors += 1
                run.error_flag("registry", res, p.name)
            elif res is not None:
                run.store.add(res)
                records.append(res)
        # Prefer a record with officer data (Companies House) over GLEIF.
        records.sort(key=lambda e: (not e.payload.officers_available, e.provider))
        primary = records[0] if records else None
        names = [p.name for p in providers]
        if primary is None and errors == 0:
            ev = Evidence[dict[str, Any]].build(
                provider="+".join(names),
                kind="registry.not_found",
                request={
                    "country": app.country,
                    "registration_number": app.registration_number,
                    "name": app.legal_name,
                },
                payload={"found": False},
            )
            run.store.add(ev)
            run.flags.append(
                Flag(
                    code="REGISTRY_NOT_FOUND",
                    severity=Severity.high,
                    evidence=[ev.id],
                    check="registry",
                    detail=f"No registry record for '{app.legal_name}' "
                    f"({app.registration_number or 'no number'}) in {', '.join(names)}.",
                )
            )
        status = "error" if errors and primary is None else "done"
        run.checks["registry"] = CheckStatus(
            check="registry",
            status=status,
            providers=names,
            note="; ".join(missing) or None,
        )
        if primary is not None:
            run.flags.extend(registry_flags(app, primary, self.config.policy.registry, self._today()))
            # LEI cross-reference is informational
            lei = next((r.payload.lei for r in records if r.payload.lei), None)
            if lei and not primary.payload.lei:
                primary.payload.lei = lei
        run.trace.append(TraceStep(step="registry", ms=_ms(t0), detail=f"{len(records)} record(s)"))
        return primary

    def ownership(self, run: _Run, app: Application, primary: Evidence[CompanyRecord] | None) -> None:
        if primary is None:
            if "registry" in run.checks and run.checks["registry"].status == "not_configured":
                run.not_configured("ownership", "needs a registry provider with officer/PSC data")
            else:
                run.checks["ownership"] = CheckStatus(
                    check="ownership", status="skipped", note="no registry record"
                )
            return
        if not primary.payload.officers_available and not primary.payload.pscs_available:
            run.not_configured("ownership", f"{primary.provider} has no officer/owner data")
            return
        run.flags.extend(ownership_flags(app, primary, self.config.policy.registry))
        run.checks["ownership"] = CheckStatus(check="ownership", status="done", providers=[primary.provider])

    async def screen_subjects(self, run: _Run, subjects: list[ScreeningSubject]) -> AmlInfo:
        t0 = time.perf_counter()
        cfg = self.config.providers
        sanction_names = list(dict.fromkeys(cfg.screening))
        pep_names = list(dict.fromkeys(cfg.pep))
        all_names = list(dict.fromkeys(sanction_names + pep_names))
        aml = AmlInfo(num_subjects=len(subjects))
        if not sanction_names:
            run.not_configured("sanctions", "no screening providers configured")
        if not pep_names:
            run.not_configured("pep", "no PEP provider configured (e.g. opensanctions_bulk, non-commercial)")
        if not all_names:
            return aml
        try:
            providers = self._screening_providers(all_names)
        except (ProviderError, IndexMissingError) as e:
            for check, names in (("sanctions", sanction_names), ("pep", pep_names)):
                if names:
                    run.error_flag(check, e, "local_index")
                    run.checks[check] = CheckStatus(check=check, status="error", providers=names)
            return aml
        tasks = [(s, p) for s in subjects for p in providers]
        results = await asyncio.gather(*(p.screen(s) for s, p in tasks), return_exceptions=True)
        best: dict[tuple[str, str], Hit] = {}
        failed: set[str] = set()
        for (subject, prov), res in zip(tasks, results, strict=True):
            if isinstance(res, BaseException):
                if not isinstance(res, Exception):
                    raise res
                if prov.name not in failed:  # one flag per failing list, not per subject
                    failed.add(prov.name)
                    run.error_flag("sanctions", res, prov.name)
                continue
            run.store.add(res)
            for hit in adjudicate_candidates(subject, res, self.config.policy.screening):
                key = (subject.name, hit.entity_id)
                if key not in best or hit.score > best[key].score:
                    best[key] = hit
        hits = sorted(best.values(), key=lambda h: (-h.score, h.subject, h.entity_id))
        aml.hits = [h for h in hits if h.outcome != HitOutcome.FALSE_POSITIVE]
        aml.cleared = [h for h in hits if h.outcome == HitOutcome.FALSE_POSITIVE]
        aml.num_hits = len(aml.hits)
        aml.result = "hits" if aml.hits else ("error" if failed else "clear")
        run.flags.extend(screening_flags(hits, self.config.policy.screening))
        if sanction_names:
            run.checks.setdefault(
                "sanctions", CheckStatus(check="sanctions", status="done", providers=sanction_names)
            )
        if pep_names:
            run.checks.setdefault("pep", CheckStatus(check="pep", status="done", providers=pep_names))
        run.trace.append(
            TraceStep(
                step="screening",
                ms=_ms(t0),
                detail=f"{len(subjects)} subjects x {len(providers)} lists, {len(hits)} candidates",
            )
        )
        return aml

    async def fetch_website(
        self, ctx: ProviderContext, run: _Run, url: str
    ) -> tuple[Evidence[WebsiteSnapshot] | None, Evidence[DomainInfo] | None]:
        t0 = time.perf_counter()
        names = self.config.providers.website
        wp = self.config.policy.website
        snap: Evidence[WebsiteSnapshot] | None = None
        dom: Evidence[DomainInfo] | None = None
        coros: list[Any] = []
        if "website_http" in names:
            coros.append(WebsiteHttpProvider(ctx, wp.max_bytes, wp.timeout_seconds).fetch(url))
        if "rdap" in names:
            coros.append(RdapProvider(ctx).domain(url))
        if not coros:
            run.not_configured("website", "no website providers configured")
            return None, None
        results = await asyncio.gather(*coros, return_exceptions=True)
        errors = 0
        for res in results:
            if isinstance(res, BaseException):
                if not isinstance(res, Exception):
                    raise res
                errors += 1
                provider = res.provider if isinstance(res, ProviderError) else "website"
                run.error_flag("website", res, provider)
                continue
            run.store.add(res)
            if res.kind == "website.snapshot":
                snap = res
            else:
                dom = res
        run.checks["website"] = CheckStatus(
            check="website", status="error" if errors == len(coros) else "done", providers=names
        )
        run.trace.append(TraceStep(step="website", ms=_ms(t0), detail=url))
        return snap, dom

    # ---- full review -----------------------------------------------------------
    async def review(self, application: Application | dict[str, Any]) -> ReviewResult:
        app = application if isinstance(application, Application) else Application.model_validate(application)
        t_start = time.perf_counter()
        run = _Run()
        ctx = ProviderContext.create(self.config, self.transport)
        try:
            reg_task = asyncio.create_task(self.lookup_registry(ctx, run, app))
            web_task = asyncio.create_task(self.fetch_website(ctx, run, app.website)) if app.website else None
            primary = await reg_task
            self.ownership(run, app, primary)
            subjects = build_subjects(app, primary.payload if primary else None)
            aml = await self.screen_subjects(run, subjects)
            if web_task is not None:
                snap, dom = await web_task
                sic = primary.payload.sic_codes if primary else []
                run.flags.extend(
                    website_flags(app, snap, dom, self.config.policy.website, self._today(), sic)
                )
            else:
                run.checks["website"] = CheckStatus(
                    check="website", status="skipped", note="no website declared"
                )
                run.flags.append(
                    Flag(
                        code="NO_WEBSITE_DECLARED",
                        severity=Severity.low,
                        check="website",
                        detail="Application does not declare a website.",
                    )
                )
        finally:
            await ctx.aclose()

        flags, rejected = validate_flags(
            apply_severity_overrides(_dedupe(run.flags), self.config.policy), run.store
        )
        if rejected:
            run.trace.append(
                TraceStep(
                    step="evidence_validator",
                    ms=0,
                    detail=f"dropped {len(rejected)} flag(s) without valid evidence: "
                    + ", ".join(f.code for f in rejected),
                )
            )
        flags = sort_flags(flags)
        outcome = decide(flags, self.config.policy)
        order = ["registry", "ownership", "sanctions", "pep", "website", "industry"]
        checks = [run.checks[c] for c in order if c in run.checks]
        run.trace.append(TraceStep(step="decide", ms=_ms(t_start), detail="; ".join(outcome.reasons)))
        result = ReviewResult(
            case_id=app.case_id,
            decision=outcome.decision,
            risk_level=outcome.risk_level,
            score=outcome.score,
            flags=flags,
            checks=checks,
            aml_info=aml,
            analyst_note=analyst_note(
                app.legal_name, outcome.decision, outcome.risk_level, outcome.score, flags, checks, aml
            ),
            policy_version=self.config.policy.version,
            kyb_tool_version=__version__,
            evidence=run.store.summaries(),
            trace=run.trace,
        )
        if self.audit_enabled:
            try:
                AuditLog(self.config.settings.audit_path).record(app, result, run.store)
            except Exception as e:  # audit must never change a decision, but must be visible
                result.trace.append(TraceStep(step="audit", ms=0, detail=f"audit write failed: {e}"))
        return result

    # ---- raw tools (tool mode) -------------------------------------------------
    async def registry_lookup(
        self, country: str, registration_number: str | None, name: str | None
    ) -> dict[str, Any]:
        app = Application(
            case_id="lookup",
            legal_name=name or registration_number or "?",
            country=country,
            registration_number=registration_number,
        )
        run = _Run()
        ctx = ProviderContext.create(self.config, self.transport)
        try:
            providers, missing = self._registry_providers(ctx, app.country)
            out: dict[str, Any] = {
                "country": app.country,
                "records": [],
                "errors": [],
                "not_configured": missing,
            }
            for p in providers:
                try:
                    ev = await p.lookup(app.country, app.registration_number, name)
                except ProviderError as e:
                    out["errors"].append(str(e))
                    continue
                if ev is not None:
                    run.store.add(ev)
                    out["records"].append(
                        {
                            "evidence_id": ev.id,
                            "provider": ev.provider,
                            "record": ev.payload.model_dump(mode="json"),
                        }
                    )
            return out
        finally:
            await ctx.aclose()

    async def screen(self, subject: ScreeningSubject) -> dict[str, Any]:
        run = _Run()
        aml = await self.screen_subjects(run, [subject])
        flags, _ = validate_flags(run.flags, run.store)
        return {
            "subject": subject.model_dump(mode="json", exclude_none=True),
            "result": aml.result,
            "hits": [h.model_dump(mode="json") for h in aml.hits],
            "cleared": [h.model_dump(mode="json") for h in aml.cleared],
            "flags": [f.model_dump(mode="json") for f in sort_flags(flags)],
            "checks": [c.model_dump(mode="json") for c in run.checks.values()],
            "index": {k: v for k, v in (self._index.meta.items() if self._index else []) if k != "trigram"},
        }

    async def check_website(
        self,
        url: str,
        declared_industry: str | None = None,
        company_name: str | None = None,
        include_text_chars: int = 4000,
    ) -> dict[str, Any]:
        app = Application(
            case_id="website",
            legal_name=company_name or url,
            website=url,
            declared_industry=declared_industry,
        )
        assert app.website is not None
        run = _Run()
        ctx = ProviderContext.create(self.config, self.transport)
        try:
            snap, dom = await self.fetch_website(ctx, run, app.website)
        finally:
            await ctx.aclose()
        flags = website_flags(app, snap, dom, self.config.policy.website, self._today())
        if not company_name:
            flags = [f for f in flags if f.code != "DOMAIN_NAME_MISMATCH"]
        flags, _ = validate_flags(run.flags + flags, run.store)
        snapshot = snap.payload.model_dump(mode="json") if snap else None
        if snapshot:
            snapshot["text"] = snapshot["text"][:include_text_chars]
        return {
            "url": app.website,
            "snapshot": snapshot,
            "domain": dom.payload.model_dump(mode="json") if dom else None,
            "flags": [f.model_dump(mode="json") for f in sort_flags(flags)],
            "evidence_ids": run.store.ids(),
        }


def _dedupe(flags: list[Flag]) -> list[Flag]:
    seen: set[tuple[str, str]] = set()
    out: list[Flag] = []
    for f in flags:
        k = (f.code, f.detail)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


async def review_case(application: dict[str, Any] | Application, **kwargs: Any) -> ReviewResult:
    return await ReviewPipeline(**kwargs).review(application)


async def screen_entity(subject: dict[str, Any] | ScreeningSubject, **kwargs: Any) -> dict[str, Any]:
    s = subject if isinstance(subject, ScreeningSubject) else ScreeningSubject.model_validate(subject)
    return await ReviewPipeline(**kwargs).screen(s)
