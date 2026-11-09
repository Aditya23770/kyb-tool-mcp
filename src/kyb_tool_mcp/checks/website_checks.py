"""Step 6: website legitimacy -> flags. Deterministic heuristics only."""

from __future__ import annotations

import re
from datetime import date
from functools import cache
from typing import Any

import yaml
from rapidfuzz import fuzz

from kyb_tool_mcp.checks.common import industry_keywords, industry_similarity, sic_descriptions, stem
from kyb_tool_mcp.config import WebsitePolicy, read_package_text
from kyb_tool_mcp.matching.normalize import domain_label, fold, normalize_company
from kyb_tool_mcp.schemas.application import Application
from kyb_tool_mcp.schemas.decision import Flag, Severity
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.website import DomainInfo, WebsiteSnapshot

MAJORITY = 0.5  # more than half of the industry keywords must appear on the page

PARKED_PHRASES = [
    "this domain is for sale",
    "domain is for sale",
    "buy this domain",
    "this domain may be for sale",
    "domain parking",
    "parked free",
    "parked domain",
    "sedo domain parking",
    "hugedomains",
    "dan com",
    "related searches",
    "is available for purchase",
    "coming soon",
    "under construction",
    "website coming soon",
    "default web page",
    "welcome to nginx",
    "it works",
    "index of",
    "future home of",
    "this site can t be reached",
    "account suspended",
    "this account has been suspended",
]


@cache
def prohibited_config() -> dict[str, Any]:
    data = yaml.safe_load(read_package_text("prohibited_keywords.yaml"))
    return data if isinstance(data, dict) else {}


def find_prohibited(text: str) -> dict[str, list[str]]:
    """Category -> matched phrases, skipping phrases preceded by a negation ('we never sell ...')."""
    cfg = prohibited_config()
    window = int(cfg.get("negation_window", 4))
    negs = set(cfg.get("negations", []))
    words = fold(text).split()
    joined = " " + " ".join(words) + " "
    found: dict[str, list[str]] = {}
    for cat, phrases in (cfg.get("categories") or {}).items():
        for phrase in phrases:
            p = fold(phrase)
            for m in re.finditer(r"(?<= )" + re.escape(p) + r"(?= )", joined):
                before = joined[: m.start()].split()[-window:]
                if not negs.intersection(before):
                    found.setdefault(str(cat), []).append(phrase)
                    break
    return found


def is_parked(snap: WebsiteSnapshot, policy: WebsitePolicy) -> str | None:
    text = fold(f"{snap.title or ''} {snap.text}")
    for phrase in PARKED_PHRASES:
        if f" {phrase} " in f" {text} " and snap.word_count < policy.parked_min_words:
            return f"parking/placeholder phrase '{phrase}' on a {snap.word_count}-word page"
    if snap.word_count < policy.thin_content_words:
        return f"only {snap.word_count} words of content"
    return None


def website_flags(
    app: Application,
    snap_ev: Evidence[WebsiteSnapshot] | None,
    dom_ev: Evidence[DomainInfo] | None,
    policy: WebsitePolicy,
    today: date,
    sic_codes: list[str] | None = None,
) -> list[Flag]:
    flags: list[Flag] = []
    if snap_ev is not None:
        snap = snap_ev.payload
        eid = [snap_ev.id]
        if not snap.reachable:
            flags.append(
                Flag(
                    code="WEBSITE_UNREACHABLE",
                    severity=Severity.medium,
                    evidence=eid,
                    check="website",
                    detail=f"{snap.url} unreachable: {snap.error or snap.status_code}.",
                )
            )
        else:
            parked = is_parked(snap, policy)
            if parked:
                flags.append(
                    Flag(
                        code="PARKED_DOMAIN",
                        severity=Severity.medium,
                        evidence=eid,
                        check="website",
                        detail=f"{snap.final_url or snap.url} looks parked or empty ({parked}).",
                    )
                )
            prohibited = find_prohibited(f"{snap.title or ''} {snap.text}")
            if prohibited:
                cats = "; ".join(f"{c}: {', '.join(p)}" for c, p in prohibited.items())
                flags.append(
                    Flag(
                        code="PROHIBITED_GOODS",
                        severity=Severity.high,
                        evidence=eid,
                        check="website",
                        detail=f"Website text mentions prohibited categories ({cats}).",
                    )
                )
            if not parked and app.declared_industry and snap.word_count >= policy.thin_content_words:
                flag = _industry_flag(app.declared_industry, snap, sic_codes or [])
                if flag:
                    flags.append(
                        Flag(
                            code="INDUSTRY_MISMATCH",
                            severity=Severity.medium,
                            evidence=eid,
                            check="industry",
                            detail=flag,
                        )
                    )
        label = domain_label(snap.url)
        name = normalize_company(app.trading_name or app.legal_name)
        if label and name:
            sim = max(
                fuzz.partial_ratio(label.replace(" ", ""), name.replace(" ", "")),
                fuzz.token_set_ratio(label, name),
            )
            if app.trading_name:
                sim = max(
                    sim,
                    fuzz.partial_ratio(
                        label.replace(" ", ""), normalize_company(app.legal_name).replace(" ", "")
                    ),
                )
            if sim < policy.domain_name_min:
                flags.append(
                    Flag(
                        code="DOMAIN_NAME_MISMATCH",
                        severity=Severity.low,
                        evidence=eid,
                        check="website",
                        detail=f"Domain '{label}' does not resemble the company name (similarity {sim:.0f}).",
                    )
                )
    if dom_ev is not None and dom_ev.payload.registered_on:
        age = (today - dom_ev.payload.registered_on).days
        if age < policy.new_domain_days:
            flags.append(
                Flag(
                    code="NEW_DOMAIN",
                    severity=Severity.medium,
                    evidence=[dom_ev.id],
                    check="website",
                    detail=f"Domain {dom_ev.payload.domain} registered {age} days ago "
                    f"(< {policy.new_domain_days}).",
                )
            )
    return flags


def _industry_flag(declared: str, snap: WebsiteSnapshot, sic_codes: list[str]) -> str | None:
    """INDUSTRY_MISMATCH when neither the declared industry nor the SIC descriptions show up on the site."""
    page = f"{snap.title or ''} {snap.text}"
    page_stems = {stem(t) for t in industry_keywords(page)}
    declared_kw = industry_keywords(declared)
    if not declared_kw:
        return None
    declared_hit = sum(stem(t) in page_stems for t in declared_kw) / len(declared_kw)
    sic_hit = 0.0
    for code in sic_codes:
        desc = sic_descriptions().get(code, "")
        kw = industry_keywords(desc)
        if kw:
            sic_hit = max(sic_hit, sum(stem(t) in page_stems for t in kw) / len(kw))
    if declared_hit > MAJORITY or sic_hit > MAJORITY or industry_similarity(declared, page[:3000]) >= 60:
        return None
    return (
        f"Website content does not mention the declared industry '{declared}' "
        f"({declared_hit:.0%} of its keywords found on the page)."
    )
