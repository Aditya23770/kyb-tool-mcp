"""Policy engine. The ONLY place a decision is made. Pure, deterministic Python."""

from __future__ import annotations

from dataclasses import dataclass

from kyb_tool_mcp.config import Policy
from kyb_tool_mcp.schemas.decision import Decision, Flag, RiskLevel

DECISION_RANK: dict[str, int] = {"APPROVE": 0, "ESCALATE": 1, "REJECT": 2}
SEVERITY_ORDER = ["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    risk_level: RiskLevel
    score: int
    reasons: tuple[str, ...]


def apply_severity_overrides(flags: list[Flag], policy: Policy) -> list[Flag]:
    """Operator-configured severities (policy YAML `flag_severity`) replace check defaults."""
    out: list[Flag] = []
    for f in flags:
        sev = policy.flag_severity.get(f.code)
        out.append(f.model_copy(update={"severity": sev}) if sev and sev != f.severity else f)
    return out


def score_flags(flags: list[Flag], policy: Policy) -> int:
    return sum(policy.severity_points.get(f.severity.value, 0) for f in flags)


def decide(flags: list[Flag], policy: Policy) -> Outcome:
    """Apply the decision rules.

    any `reject_if_any` severity -> REJECT; any `escalate_if_any` severity or
    score >= `escalate_if_score_gte` -> ESCALATE; otherwise APPROVE.
    """
    severities = {f.severity.value for f in flags}
    score = score_flags(flags, policy)
    rules = policy.decision
    reasons: list[str] = []

    decision: Decision
    if hit := sorted(severities & set(rules.reject_if_any)):
        decision = "REJECT"
        reasons.append(f"severity {', '.join(hit)} present")
    elif hit := sorted(severities & set(rules.escalate_if_any)):
        decision = "ESCALATE"
        reasons.append(f"severity {', '.join(hit)} present")
    elif score >= rules.escalate_if_score_gte:
        decision = "ESCALATE"
        reasons.append(f"score {score} >= {rules.escalate_if_score_gte}")
    else:
        decision = "APPROVE"
        reasons.append(f"no blocking flags; score {score}")

    risk: RiskLevel
    if decision == "REJECT" or score > policy.risk_levels.medium_max:
        risk = "HIGH"
    elif score > policy.risk_levels.low_max:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    return Outcome(decision=decision, risk_level=risk, score=score, reasons=tuple(reasons))


def sort_flags(flags: list[Flag]) -> list[Flag]:
    return sorted(flags, key=lambda f: (SEVERITY_ORDER.index(f.severity.value), f.code, f.detail))


def explain_policy(policy: Policy) -> str:
    """Human/LLM-readable description of how decisions are made."""
    p = policy
    s = p.screening
    return (
        f"kyb-tool-mcp policy {p.version}\n"
        f"Severity points: {p.severity_points}.\n"
        f"Decision: any {p.decision.reject_if_any} flag => REJECT; any {p.decision.escalate_if_any} flag "
        f"or total score >= {p.decision.escalate_if_score_gte} => ESCALATE; otherwise APPROVE.\n"
        f"Risk level: score <= {p.risk_levels.low_max} LOW, <= {p.risk_levels.medium_max} MEDIUM, else HIGH "
        "(REJECT is always HIGH).\n"
        "Step 2 registry flags: REGISTRY_NOT_FOUND high, COMPANY_INACTIVE critical, NAME_MISMATCH "
        f"medium/high (ok >= {p.registry.name_ok}), ADDRESS_MISMATCH medium, NEW_ENTITY medium "
        f"(< {p.registry.new_entity_months} months), UNDECLARED_DIRECTOR high, UNKNOWN_DIRECTOR high, "
        "OPAQUE_OWNERSHIP high (corporate PSC), NO_UBO_DATA high, OWNERSHIP_MISMATCH medium, "
        "SIC_INDUSTRY_MISMATCH medium.\n"
        "Step 4 screening (company, directors, UBOs/PSCs): SANCTIONS_MATCH critical, PEP_MATCH high, "
        "CRIME_MATCH high, POSSIBLE_MATCH high. A candidate needs name score >= "
        f"{s.candidate_min}; TRUE_MATCH needs name >= {s.true_match.name_min} and a DOB match for people "
        f"(companies: name >= {s.true_match.company_name_min}); auto-cleared if DOB years differ by > "
        f"{s.false_positive.dob_year_diff_gt}, nationality conflicts with name < "
        f"{s.false_positive.nationality_conflict_name_below}, or person/company schema mismatch. "
        "Anything else is NEEDS_REVIEW (POSSIBLE_MATCH). The LLM may explain hits but never clears them.\n"
        "Step 6 website flags: WEBSITE_UNREACHABLE, PARKED_DOMAIN, NEW_DOMAIN, INDUSTRY_MISMATCH medium; "
        "PROHIBITED_GOODS high; DOMAIN_NAME_MISMATCH, WEBSITE_BLOCKS_AUTOMATION, NO_WEBSITE_DECLARED low.\n"
        "System: PROVIDER_ERROR high (fail closed), CHECK_NOT_CONFIGURED low.\n"
        "Every flag cites evidence ids. Results are decision support, not legal advice."
    )
