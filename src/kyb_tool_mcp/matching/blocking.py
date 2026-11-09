"""Candidate generation: FTS tokens ∪ phonetic keys ∪ trigrams, then name scoring."""

from __future__ import annotations

from collections.abc import Callable

from rapidfuzz.distance import JaroWinkler

from kyb_tool_mcp.config import ScreeningPolicy
from kyb_tool_mcp.matching.features import _directional, _tok_sim, compute_features, name_similarity
from kyb_tool_mcp.matching.index import SanctionsIndex, entity_name_tokens
from kyb_tool_mcp.matching.normalize import distinctive_company_tokens, fold, tokens_for
from kyb_tool_mcp.matching.scorer import rule_score
from kyb_tool_mcp.schemas.screening import Candidate, ListEntity, ScreeningSubject


def block(index: SanctionsIndex, tokens: list[str], limit: int) -> list[str]:
    """Return up to `limit` entity ids, ranked by number of blocking signals then FTS rank."""
    fts = index.fts_candidates(tokens, limit)
    pho = index.phonetic_candidates(tokens, limit)
    tri = index.trigram_candidates(tokens, limit // 2)
    ids = set(fts) | set(pho) | set(tri)

    def rank(i: str) -> tuple[int, float]:
        signals = (i in fts) + (i in pho) + (i in tri)
        return (-signals, -fts.get(i, 0.0))

    return sorted(ids, key=rank)[:limit]


def _company_aware_similarity(
    subject: ScreeningSubject,
    entity: ListEntity,
    s_tokens: list[str],
    e_tokens: list[str],
    weight: Callable[[str], float],
) -> dict[str, float]:
    """Company vs organisation: match on DISTINCTIVE words only (not "trading", "group", ...)."""
    sim = name_similarity(s_tokens, e_tokens, weight)
    if subject.type != "company" or entity.is_person:
        return sim
    ds, de = distinctive_company_tokens(s_tokens), distinctive_company_tokens(e_tokens)
    if ds and de:
        # Company names are registered spellings: a word only counts if it is (nearly) the same word.
        # Measured: different firms <= 0.88 (Northgate/Northstar), true variants >= 0.92 (Gazprom/Gasprom).
        dsim = name_similarity(ds, de, weight, min_token_sim=COMPANY_TOKEN_MIN)
        dsim["generic_only"] = 0.0
        # Share of each side's distinctive words found in the other; both must be high to confirm.
        dsim["subject_coverage"] = round(_directional(ds, de, weight, COMPANY_TOKEN_MIN), 3)
        dsim["entity_coverage"] = round(_directional(de, ds, weight, COMPANY_TOKEN_MIN), 3)
        matched = [t for t in de if any(_tok_sim(t, u) >= COMPANY_TOKEN_MIN for u in ds)]
        dsim["short_match"] = 1.0 if matched and all(len(t) <= 3 for t in matched) else 0.0
        dsim["full_name_score"] = sim["name_score"]
        dsim["full_name_coverage"] = sim.get("coverage_both", 0.0)
        dsim["full_name_strong"] = sim.get("all_strong", 0.0)
        if len(ds) == 1 and len(de) == 1:
            # Plain spelling closeness for one-word names, no sound-alike boost (NALOW vs NALU = 0.85).
            dsim["one_word_spelling"] = round(float(JaroWinkler.normalized_similarity(ds[0], de[0])), 3)
        # "al", "el", "de" ... are particles, not distinctive words
        dsim["distinctive_words"] = float(max(sum(len(t) > 2 for t in ds), sum(len(t) > 2 for t in de)))
        return dsim
    # One side is made only of generic words: only an (almost) exact full-name match counts.
    if sim["name_score"] < GENERIC_ONLY_EXACT_MIN:
        sim = {**sim, "name_score": 0.0}
    sim["generic_only"] = 1.0
    return sim


GENERIC_ONLY_EXACT_MIN = 97.0

# Pure legal-form words. Descriptive words such as "holdings", "group", "trading" are NOT here.
CORE_LEGAL = frozenset(
    "ltd limited inc incorporated co company corp corporation llc llp lp plc gmbh kg ag sa sarl srl "
    "bv nv oy ab as jsc ojsc pjsc cjsc ooo tov fze fzco fzc dmcc pte pvt the and of "
    "joint stock open closed public private liability partnership".split()
)


def core_name(name: str) -> frozenset[str]:
    """Name words with only pure legal forms removed (used to confirm one-word company names)."""
    return frozenset(t for t in fold(name).replace("&", " ").split() if t not in CORE_LEGAL)


COMPANY_TOKEN_MIN = 0.90


def score_entity(
    index: SanctionsIndex | None,
    subject: ScreeningSubject,
    s_tokens: list[str],
    entity: ListEntity,
) -> Candidate | None:
    """Best-scoring name of `entity` against the subject, with full features."""
    if index is not None:
        max_idf = index.max_idf() or 1.0

        def weight(t: str) -> float:
            return 0.5 + index.idf(t) / max_idf
    else:

        def weight(t: str) -> float:
            return 1.0

    best: Candidate | None = None
    for name, weak in [(n, False) for n in entity.names] + [(n, True) for n in entity.weak_names]:
        e_tokens = entity_name_tokens(name, entity.schema_)
        if not e_tokens:
            continue
        sim = _company_aware_similarity(subject, entity, s_tokens, e_tokens, weight)
        if best is not None and sim["name_score"] <= best.name_score:
            continue
        if index is not None:
            common = [t for t in s_tokens if t in set(e_tokens)] or s_tokens
            rarity = sum(index.idf(t) for t in common) / (len(common) * max_idf)
        else:
            rarity = 1.0
        feats = compute_features(subject, s_tokens, entity, e_tokens, sim, rarity, weak)
        if subject.type == "company" and not entity.is_person:
            feats["core_exact"] = core_name(subject.name) == core_name(name)
        best = Candidate(
            entity=entity,
            matched_name=name,
            name_score=sim["name_score"],
            features=feats,
            score=rule_score(feats),
        )
    return best


def generate_candidates(
    index: SanctionsIndex,
    subject: ScreeningSubject,
    policy: ScreeningPolicy,
    sources: set[str] | None = None,
) -> list[Candidate]:
    s_tokens = tokens_for(subject.name, subject.type)
    if not s_tokens:
        return []
    ids = block(index, s_tokens, policy.max_candidates)
    entities = index.get_entities(ids)
    out: list[Candidate] = []
    for ent in entities:
        if sources is not None and ent.source not in sources:
            continue
        cand = score_entity(index, subject, s_tokens, ent)
        if cand and cand.name_score >= policy.candidate_min:
            out.append(cand)
    out.sort(key=lambda c: (-c.score, -c.name_score, c.entity.id))
    return out
