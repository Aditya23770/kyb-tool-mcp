"""Hit adjudication: TRUE_MATCH / FALSE_POSITIVE / NEEDS_REVIEW with human-readable reasons.

Rules only. Thresholds come from policy. The LLM never changes an outcome.
"""

from __future__ import annotations

from kyb_tool_mcp.config import ScreeningPolicy
from kyb_tool_mcp.schemas.screening import Candidate, HitOutcome, ScreeningSubject


def adjudicate(
    subject: ScreeningSubject, cand: Candidate, policy: ScreeningPolicy
) -> tuple[HitOutcome, list[str]]:
    f = cand.features
    name = cand.name_score
    tm = policy.true_match
    fp = policy.false_positive
    reasons: list[str] = [f"name similarity {name:.0f} with '{cand.matched_name}'"]

    # ---- exculpatory evidence (auto-clear) --------------------------------
    if fp.schema_mismatch and f.get("schema") == "mismatch":
        reasons.append(f"subject is a {subject.type} but list entry is a {cand.entity.schema_}")
        return HitOutcome.FALSE_POSITIVE, reasons
    if f.get("weak_alias") and name < policy.weak_alias_name_min:
        reasons.append(f"matched only a weak/low-quality alias below {policy.weak_alias_name_min:.0f}")
        return HitOutcome.FALSE_POSITIVE, reasons
    diff = f.get("dob_year_diff")
    if f.get("dob") == "conflict" and diff is not None and diff > fp.dob_year_diff_gt:
        reasons.append(f"birth year differs by {diff} years (> {fp.dob_year_diff_gt})")
        return HitOutcome.FALSE_POSITIVE, reasons
    if f.get("nationality") == "conflict" and name < fp.nationality_conflict_name_below:
        reasons.append(
            f"nationality conflicts and name similarity < {fp.nationality_conflict_name_below:.0f}"
        )
        return HitOutcome.FALSE_POSITIVE, reasons

    # ---- confirmation -------------------------------------------------------
    if f.get("weak_alias"):
        # OFAC "weak" / UN "low quality" aliases are too broad to confirm a match on their own.
        reasons.append("matched a weak/low-quality alias; cannot be confirmed automatically")
        reasons.append("needs analyst review")
        return HitOutcome.NEEDS_REVIEW, reasons
    if subject.type == "company":
        if name >= tm.company_name_min:
            reasons.append(f"company name similarity >= {tm.company_name_min:.0f}")
            return HitOutcome.TRUE_MATCH, reasons
    else:
        dob = f.get("dob")
        dob_ok = dob in {"year_month", "year"}
        if name >= tm.name_min and (dob_ok or not tm.require_dob_match):
            reasons.append(f"birth date matches ({dob})" if dob_ok else "DOB match not required by policy")
            if f.get("nationality") == "match":
                reasons.append("nationality matches")
            return HitOutcome.TRUE_MATCH, reasons
        if dob == "unknown":
            reasons.append("birth date unknown on one side; cannot confirm or clear")
        elif dob in {"near", "month_conflict"}:
            reasons.append(f"birth date close but not equal ({dob})")

    reasons.append("needs analyst review")
    return HitOutcome.NEEDS_REVIEW, reasons
