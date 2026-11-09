"""Template-based analyst note. Deterministic; mentions only flags that exist."""

from __future__ import annotations

from kyb_tool_mcp.schemas.decision import AmlInfo, CheckStatus, Flag
from kyb_tool_mcp.schemas.screening import HitOutcome


def analyst_note(
    company: str,
    decision: str,
    risk: str,
    score: int,
    flags: list[Flag],
    checks: list[CheckStatus],
    aml: AmlInfo,
) -> str:
    parts = [f"{company}: {decision} (risk {risk}, score {score})."]
    blocking = [f for f in flags if f.severity.value in {"critical", "high"}]
    if blocking:
        parts.append("Key issues: " + "; ".join(f"{f.code} - {f.detail}" for f in blocking[:4]) + ".")
    minor = [f.code for f in flags if f.severity.value in {"medium", "low"}]
    if minor:
        parts.append("Other flags: " + ", ".join(dict.fromkeys(minor)) + ".")
    if aml.result != "not_screened":
        confirmed = sum(h.outcome == HitOutcome.TRUE_MATCH for h in aml.hits)
        review = sum(h.outcome == HitOutcome.NEEDS_REVIEW for h in aml.hits)
        parts.append(
            f"Screened {aml.num_subjects} subjects: {confirmed} confirmed match(es), {review} needing review, "
            f"{len(aml.cleared)} auto-cleared false positive(s)."
        )
    gaps = [c.check for c in checks if c.status in {"not_configured", "error"}]
    if gaps:
        parts.append("Not completed: " + ", ".join(gaps) + ".")
    if decision == "APPROVE" and not flags:
        parts.append("No issues found in the configured checks.")
    return " ".join(parts)
