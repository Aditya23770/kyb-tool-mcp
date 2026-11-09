"""Pairwise features between a screening subject and a list entity."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from kyb_tool_mcp.matching.countries import to_iso2
from kyb_tool_mcp.matching.normalize import merge_particles, phonetic
from kyb_tool_mcp.schemas.screening import ListEntity, ScreeningSubject

PERSON_SCHEMAS = {"person"}
COMPANY_SCHEMAS = {"organization", "company", "legalentity", "publicbody", "entity"}


def _tok_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) == 1 or len(b) == 1:  # initials
        return 0.9 if a[0] == b[0] else 0.0
    sim = float(JaroWinkler.normalized_similarity(a, b))
    if phonetic(a) == phonetic(b) and len(a) >= 3 and len(b) >= 3:
        sim = max(sim, 0.92)
    return sim


def _directional(src: list[str], dst: list[str], weight: Callable[[str], float]) -> float:
    if not src or not dst:
        return 0.0
    total = 0.0
    wsum = 0.0
    used: set[int] = set()
    for a in src:
        best, best_j = 0.0, -1
        for j, b in enumerate(dst):
            if j in used:
                continue
            s = _tok_sim(a, b)
            if s > best:
                best, best_j = s, j
        if best_j >= 0 and best >= 0.85:
            used.add(best_j)
        w = weight(a)
        total += best * w
        wsum += w
    return total / wsum if wsum else 0.0


def name_similarity(
    a_tokens: list[str], b_tokens: list[str], weight: Callable[[str], float] | None = None
) -> dict[str, float]:
    """Token-alignment similarity, robust to order, dropped middle names and transliteration."""
    w = weight or (lambda _t: 1.0)
    best: dict[str, float] = {"name_score": 0.0}
    variants_a = [a_tokens, merge_particles(a_tokens)]
    variants_b = [b_tokens, merge_particles(b_tokens)]
    for va in variants_a:
        for vb in variants_b:
            if not va or not vb:
                continue
            short, long_ = (va, vb) if len(va) <= len(vb) else (vb, va)
            d_short = _directional(short, long_, w)
            d_long = _directional(long_, short, w)
            align = 100 * (0.75 * d_short + 0.25 * d_long)
            sa, sb = " ".join(va), " ".join(vb)
            ts = float(fuzz.token_sort_ratio(sa, sb))
            joined = float(fuzz.ratio(sa.replace(" ", ""), sb.replace(" ", "")))
            score = max(align, ts, joined if abs(len(va) - len(vb)) <= 1 else 0.0)
            if score > best["name_score"]:
                best = {
                    "name_score": round(score, 2),
                    "align": round(align, 2),
                    "token_sort": round(ts, 2),
                    "token_set": round(float(fuzz.token_set_ratio(sa, sb)), 2),
                    "joined": round(joined, 2),
                    "coverage_short": round(d_short, 3),
                    "coverage_long": round(d_long, 3),
                    "token_diff": float(abs(len(va) - len(vb))),
                }
    return best


def schema_relation(subject: ScreeningSubject, entity: ListEntity) -> str:
    sch = entity.schema_.lower()
    if subject.type == "person":
        if sch in PERSON_SCHEMAS:
            return "match"
        return "mismatch"
    # company subject
    if sch in COMPANY_SCHEMAS:
        return "match"
    return "mismatch"


def dob_relation(subject: ScreeningSubject, entity: ListEntity) -> tuple[str, int | None]:
    """Best relation across the entity's birth dates: year_month | year | month_conflict | near | conflict | unknown."""
    if subject.type != "person" or not subject.birth_year or not entity.birth_dates:
        return "unknown", None
    rank = {"year_month": 5, "year": 4, "month_conflict": 3, "near": 2, "conflict": 1}
    best, best_diff = "conflict", None
    for d in entity.birth_dates:
        diff = abs(d.year - subject.birth_year)
        if diff == 0:
            if subject.birth_month and d.month:
                rel = "year_month" if subject.birth_month == d.month else "month_conflict"
            else:
                rel = "year"
        elif diff == 1:
            rel = "near"
        else:
            rel = "conflict"
        if best_diff is None or rank[rel] > rank[best] or (rel == best and diff < best_diff):
            best, best_diff = rel, diff
    return best, best_diff


def nationality_relation(subject: ScreeningSubject, entity: ListEntity) -> str:
    s = to_iso2(subject.nationality)
    if not s or not entity.nationalities:
        return "unknown"
    ent = {c for c in (to_iso2(n) for n in entity.nationalities) if c}
    if not ent:
        return "unknown"
    return "match" if s in ent else "conflict"


def compute_features(
    subject: ScreeningSubject,
    subject_tokens: list[str],
    entity: ListEntity,
    matched_tokens: list[str],
    sim: dict[str, float],
    rarity: float,
    weak: bool,
) -> dict[str, Any]:
    dob, dob_diff = dob_relation(subject, entity)
    return {
        **sim,
        "schema": schema_relation(subject, entity),
        "dob": dob,
        "dob_year_diff": dob_diff,
        "nationality": nationality_relation(subject, entity),
        "rarity": round(rarity, 3),
        "weak_alias": weak,
        "subject_tokens": len(subject_tokens),
        "matched_tokens": len(matched_tokens),
    }
