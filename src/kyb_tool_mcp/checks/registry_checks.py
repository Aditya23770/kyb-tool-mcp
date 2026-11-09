"""Step 2: registry verification (claims vs registry) -> flags."""

from __future__ import annotations

from datetime import date

from rapidfuzz import fuzz

from kyb_tool_mcp.checks.common import industry_similarity, same_person, sic_descriptions
from kyb_tool_mcp.config import RegistryPolicy
from kyb_tool_mcp.matching.normalize import normalize_address, normalize_company, normalize_postcode
from kyb_tool_mcp.schemas.application import Application
from kyb_tool_mcp.schemas.decision import Flag, Severity
from kyb_tool_mcp.schemas.evidence import Evidence
from kyb_tool_mcp.schemas.registry import CompanyRecord


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def company_name_score(a: str, b: str) -> float:
    na, nb = normalize_company(a), normalize_company(b)
    if not na or not nb:
        return 0.0
    return float(max(fuzz.token_sort_ratio(na, nb), fuzz.ratio(na.replace(" ", ""), nb.replace(" ", ""))))


def registry_flags(
    app: Application, ev: Evidence[CompanyRecord] | None, policy: RegistryPolicy, today: date
) -> list[Flag]:
    """Company-level registry rules. `ev` is the primary registry record (None = not found)."""
    if ev is None:
        return []
    rec = ev.payload
    eid = [ev.id]
    flags: list[Flag] = []

    def add(code: str, sev: Severity, detail: str) -> None:
        flags.append(Flag(code=code, severity=sev, detail=detail, evidence=eid, check="registry"))

    if rec.status.lower() in policy.inactive_statuses:
        add("COMPANY_INACTIVE", Severity.critical, f"Registry status is '{rec.status}'.")

    score = company_name_score(app.legal_name, rec.name)
    if score < policy.name_ok:
        prev = max((company_name_score(app.legal_name, p) for p in rec.previous_names), default=0.0)
        if prev >= policy.name_ok:
            add(
                "NAME_MISMATCH",
                Severity.medium,
                f"Declared name '{app.legal_name}' matches a former name; current name is '{rec.name}'.",
            )
        else:
            sev = Severity.medium if score >= policy.name_medium else Severity.high
            add(
                "NAME_MISMATCH",
                sev,
                f"Declared name '{app.legal_name}' vs registry '{rec.name}' (similarity {score:.0f}).",
            )

    if (app.registered_address or app.postcode) and rec.address:
        reg_pc = normalize_postcode(rec.address.postal_code)
        app_pc = normalize_postcode(app.postcode or app.registered_address)
        addr_score = (
            float(
                fuzz.token_set_ratio(
                    normalize_address(app.registered_address), normalize_address(rec.address.one_line())
                )
            )
            if app.registered_address
            else 100.0
        )
        pc_conflict = bool(reg_pc and app_pc and reg_pc != app_pc)
        if pc_conflict or addr_score < policy.address_min:
            add(
                "ADDRESS_MISMATCH",
                Severity.medium,
                f"Declared address/postcode does not match registered office '{rec.address.one_line()}' "
                f"(similarity {addr_score:.0f}{', postcode differs' if pc_conflict else ''}).",
            )

    if rec.incorporated_on and _months_between(rec.incorporated_on, today) < policy.new_entity_months:
        add(
            "NEW_ENTITY",
            Severity.medium,
            f"Incorporated on {rec.incorporated_on.isoformat()} (< {policy.new_entity_months} months ago).",
        )

    if app.declared_industry and rec.sic_codes:
        descs = {c: sic_descriptions().get(c, "") for c in rec.sic_codes}
        best = max((industry_similarity(app.declared_industry, d) for d in descs.values() if d), default=None)
        if best is not None and best < policy.sic_match_min:
            listing = "; ".join(f"{c} {d}" for c, d in descs.items())
            add(
                "SIC_INDUSTRY_MISMATCH",
                Severity.medium,
                f"Declared industry '{app.declared_industry}' does not match registered SIC codes ({listing}).",
            )
    return flags


def ownership_flags(app: Application, ev: Evidence[CompanyRecord], policy: RegistryPolicy) -> list[Flag]:
    """Directors and beneficial owners: declared vs registry."""
    rec = ev.payload
    eid = [ev.id]
    flags: list[Flag] = []
    th = policy.person_name_match

    def add(code: str, sev: Severity, detail: str) -> None:
        flags.append(Flag(code=code, severity=sev, detail=detail, evidence=eid, check="ownership"))

    if rec.officers_available:
        reg_dirs = [o for o in rec.officers if o.active and "director" in o.role]
        for o in reg_dirs:
            if not any(
                same_person(o.name, o.birth_year, o.birth_month, d.name, d.birth_year, d.birth_month, th)
                for d in app.directors
            ):
                add(
                    "UNDECLARED_DIRECTOR",
                    Severity.high,
                    f"Registry lists active director '{o.name}' who is not declared in the application.",
                )
        for d in app.directors:
            if not any(
                same_person(o.name, o.birth_year, o.birth_month, d.name, d.birth_year, d.birth_month, th)
                for o in reg_dirs
            ):
                add(
                    "UNKNOWN_DIRECTOR",
                    Severity.high,
                    f"Declared director '{d.name}' is not an active director on the registry.",
                )

    if rec.pscs_available:
        active = [p for p in rec.pscs if p.active]
        for p in active:
            if p.is_corporate:
                add(
                    "OPAQUE_OWNERSHIP",
                    Severity.high,
                    f"Controlled by a corporate entity '{p.name}'; ultimate beneficial owners are not visible.",
                )
        if not active:
            stmt = f" Statement: {rec.psc_statements[0]}." if rec.psc_statements else ""
            add(
                "NO_UBO_DATA",
                Severity.high,
                f"Registry shows no active persons with significant control.{stmt}",
            )
        individuals = [p for p in active if not p.is_corporate]
        for p in individuals:
            match = next(
                (
                    u
                    for u in app.ubos
                    if same_person(
                        p.name, p.birth_year, p.birth_month, u.name, u.birth_year, u.birth_month, th
                    )
                ),
                None,
            )
            if match is None:
                add(
                    "OWNERSHIP_MISMATCH",
                    Severity.medium,
                    f"Registry PSC '{p.name}' is not declared as a beneficial owner.",
                )
            elif match.ownership_pct is not None and p.ownership_pct_min is not None:
                hi = p.ownership_pct_max or 100
                if not (p.ownership_pct_min <= match.ownership_pct <= hi):
                    add(
                        "OWNERSHIP_MISMATCH",
                        Severity.medium,
                        f"'{match.name}' declared {match.ownership_pct:g}% but registry band is "
                        f"{p.ownership_pct_min:g}-{hi:g}%.",
                    )
        for u in app.ubos:
            if not any(
                same_person(p.name, p.birth_year, p.birth_month, u.name, u.birth_year, u.birth_month, th)
                for p in individuals
            ):
                add(
                    "OWNERSHIP_MISMATCH",
                    Severity.medium,
                    f"Declared beneficial owner '{u.name}' is not a registered person with significant control.",
                )
    return flags
