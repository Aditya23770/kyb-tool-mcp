"""Hit adjudication: TRUE_MATCH / FALSE_POSITIVE / NEEDS_REVIEW with human-readable reasons.

Rules only. Thresholds come from policy. The LLM never changes an outcome.
"""

from __future__ import annotations

from kyb_tool_mcp.config import ScreeningPolicy
from kyb_tool_mcp.schemas.screening import Candidate, HitOutcome, ScreeningSubject

EXACT = 99.5  # identical after normalisation (legal forms, case, punctuation)


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
        if name >= fp.dob_conflict_keep_if_name_gte and f.get("nationality") != "conflict":
            # Lists disagree on DOBs for the same person (UK vs UN data): never clear an exact name on DOB.
            reasons.append(
                f"birth year differs by {diff} years, but the name is an exact match and no other "
                "identifier rules it out (lists can disagree on birth dates)"
            )
            reasons.append("needs analyst review")
            return HitOutcome.NEEDS_REVIEW, reasons
        extra = " and nationality differs" if f.get("nationality") == "conflict" else ""
        reasons.append(f"birth year differs by {diff} years (> {fp.dob_year_diff_gt}){extra}")
        return HitOutcome.FALSE_POSITIVE, reasons
    if (
        subject.type == "person"
        and float(f.get("parts_matched", 1.0)) < fp.person_parts_matched_min
        and f.get("dob") not in {"year_month", "year"}
    ):
        # "Alia Fazal" vs "Fazal RAHIM": one shared name part is not a match.
        reasons.append("only part of the name matches (another name part has no close counterpart)")
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
        cr = policy.company_review
        s_cov = float(f.get("subject_coverage", 1.0))
        e_cov = float(f.get("entity_coverage", 1.0))
        applicant_contained = s_cov >= tm.company_coverage_min and e_cov >= cr.entity_coverage_min
        if name < cr.name_min and not applicant_contained:
            reasons.append(f"company name similarity below review threshold {cr.name_min:.0f}")
            return HitOutcome.FALSE_POSITIVE, reasons
        if f.get("distinctive_words", 2.0) <= 1 and (
            not f.get("all_strong", 1.0) or float(f.get("one_word_spelling", 1.0)) < cr.one_word_spelling_min
        ):
            # One distinctive word that is only similar (NALOW vs NALU): a different name.
            reasons.append("the only distinctive word is similar but not the same word")
            return HitOutcome.FALSE_POSITIVE, reasons
        if s_cov <= cr.subject_coverage_gt or e_cov < cr.entity_coverage_min:
            reasons.append(f"only part of the names overlap (applicant {s_cov:.0%}, listed {e_cov:.0%})")
            return HitOutcome.FALSE_POSITIVE, reasons
        if f.get("short_match") and float(f.get("full_name_score", 0)) < cr.short_match_full_name_min:
            reasons.append("overlap is only a short acronym; full names differ")
            return HitOutcome.FALSE_POSITIVE, reasons
        full = s_cov >= tm.company_coverage_min and e_cov >= tm.company_coverage_min
        full_name = float(f.get("full_name_score", 0))
        exact_full_name = full_name >= EXACT
        whole_name_close = (
            full_name >= tm.company_full_name_min
            and float(f.get("full_name_coverage", 1.0)) >= tm.company_full_coverage_min
        )
        if f.get("distinctive_words", 2.0) <= 1 and (name < EXACT or not f.get("core_exact", True)):
            # One distinctive word (AL HADI vs AL-HADID, OCEAN CO HOLDINGS vs OCEAN INC.):
            # confirm only when the names are identical apart from pure legal forms.
            whole_name_close = False
        if not f.get("full_name_strong", 1.0):
            whole_name_close = False  # a word only nearly matches (TRADE vs TRADING): review, not confirm
        if (
            name >= tm.company_name_min
            and full
            and whole_name_close
            and (not f.get("short_match") or exact_full_name)
        ):
            reasons.append(f"all distinctive words match (similarity {name:.0f})")
            return HitOutcome.TRUE_MATCH, reasons
        reasons.append("company names overlap but do not fully match; cannot confirm automatically")
    else:
        dob = f.get("dob")
        dob_ok = dob in {"year_month", "year"}
        # Every name part identical, an initial, or a spelling variant (HAMDANI vs HAMANI is not).
        all_parts = float(f.get("coverage_both", 1.0)) >= tm.person_coverage_min and bool(
            f.get("all_strong", 1.0)
        )
        if name >= tm.name_min and (dob_ok or not tm.require_dob_match) and not all_parts:
            reasons.append(
                "part of the name has no counterpart on the other side; cannot confirm automatically"
            )
        elif name >= tm.name_min and (dob_ok or not tm.require_dob_match):
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
