"""Step 4: sanctions / PEP / crime screening -> hits, cleared hits and flags."""

from __future__ import annotations

from kyb_tool_mcp.config import ScreeningPolicy
from kyb_tool_mcp.matching.adjudicate import adjudicate
from kyb_tool_mcp.matching.normalize import subject_key
from kyb_tool_mcp.schemas.application import Application
from kyb_tool_mcp.schemas.decision import Flag, Hit, Severity
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.registry import CompanyRecord
from kyb_tool_mcp.schemas.screening import Candidate, HitOutcome, ScreeningSubject


def build_subjects(app: Application, record: CompanyRecord | None) -> list[ScreeningSubject]:
    """Company + every director + every UBO/PSC, from application and registry, de-duplicated."""
    subjects: list[ScreeningSubject] = []
    seen: dict[str, ScreeningSubject] = {}

    def add(s: ScreeningSubject) -> None:
        key = subject_key(s.name, s.type)
        if not key.split("|")[0]:
            return
        if key in seen:
            prev = seen[key]  # enrich missing DOB / nationality from the other source
            prev.birth_year = prev.birth_year or s.birth_year
            prev.birth_month = prev.birth_month or s.birth_month
            prev.nationality = prev.nationality or s.nationality
            return
        seen[key] = s
        subjects.append(s)

    add(ScreeningSubject(name=app.legal_name, type="company", role="company"))
    if app.trading_name:
        add(ScreeningSubject(name=app.trading_name, type="company", role="company"))
    for d in app.directors:
        add(
            ScreeningSubject(
                name=d.name,
                birth_year=d.birth_year,
                birth_month=d.birth_month,
                nationality=d.nationality,
                role="director",
            )
        )
    for u in app.ubos:
        add(
            ScreeningSubject(
                name=u.name,
                birth_year=u.birth_year,
                birth_month=u.birth_month,
                nationality=u.nationality,
                role="ubo",
            )
        )
    if record:
        add(ScreeningSubject(name=record.name, type="company", role="company"))
        for o in record.officers:
            if o.active:
                add(
                    ScreeningSubject(
                        name=o.name,
                        type="company" if o.is_corporate else "person",
                        birth_year=o.birth_year,
                        birth_month=o.birth_month,
                        nationality=o.nationality,
                        role="director",
                    )
                )
        for p in record.pscs:
            if p.active:
                add(
                    ScreeningSubject(
                        name=p.name,
                        type="company" if p.is_corporate else "person",
                        birth_year=p.birth_year,
                        birth_month=p.birth_month,
                        nationality=p.nationality,
                        role="psc",
                    )
                )
    return subjects


def _flag_for(hit: Hit) -> tuple[str, Severity]:
    if hit.outcome == HitOutcome.NEEDS_REVIEW:
        return "POSSIBLE_MATCH", Severity.high
    topics = set(hit.topics)
    if "sanction" in topics:
        return "SANCTIONS_MATCH", Severity.critical
    if "role.pep" in topics or any(t.startswith("role.") for t in topics):
        return "PEP_MATCH", Severity.high
    if "crime" in topics or any(t.startswith("crime") for t in topics):
        return "CRIME_MATCH", Severity.high
    return "SANCTIONS_MATCH", Severity.critical


def adjudicate_candidates(
    subject: ScreeningSubject, ev: Evidence[list[Candidate]], policy: ScreeningPolicy
) -> list[Hit]:
    hits: list[Hit] = []
    for cand in ev.payload:
        outcome, reasons = adjudicate(subject, cand, policy)
        hits.append(
            Hit(
                subject=subject.name,
                subject_role=subject.role,
                subject_type=subject.type,
                entity_id=cand.entity.id,
                source=cand.entity.source,
                matched_name=cand.matched_name,
                caption=cand.entity.caption,
                topics=cand.entity.topics,
                programs=cand.entity.programs,
                name_score=cand.name_score,
                score=cand.score,
                outcome=outcome,
                reasons=reasons,
                evidence=[ev.id],
            )
        )
    return hits


def screening_flags(hits: list[Hit], policy: ScreeningPolicy) -> list[Flag]:
    flags: list[Flag] = []
    for h in hits:
        if h.outcome == HitOutcome.FALSE_POSITIVE:
            continue
        if h.outcome == HitOutcome.NEEDS_REVIEW and not policy.needs_review_escalates:
            continue
        code, sev = _flag_for(h)
        role = f" ({h.subject_role})" if h.subject_role else ""
        flags.append(
            Flag(
                code=code,
                severity=sev,
                detail=(
                    f"{h.subject}{role} ~ '{h.caption}' on {h.source} [{', '.join(h.programs[:3])}]: "
                    + "; ".join(h.reasons)
                ),
                evidence=h.evidence,
                check="pep" if code == "PEP_MATCH" else "sanctions",
            )
        )
    return flags
