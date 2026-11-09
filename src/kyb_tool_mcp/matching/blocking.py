"""Candidate generation: FTS tokens ∪ phonetic keys ∪ trigrams, then name scoring."""

from __future__ import annotations

from kyb_tool_mcp.config import ScreeningPolicy
from kyb_tool_mcp.matching.features import compute_features, name_similarity
from kyb_tool_mcp.matching.index import SanctionsIndex, entity_name_tokens
from kyb_tool_mcp.matching.normalize import tokens_for
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
        sim = name_similarity(s_tokens, e_tokens, weight)
        if best is not None and sim["name_score"] <= best.name_score:
            continue
        if index is not None:
            common = [t for t in s_tokens if t in set(e_tokens)] or s_tokens
            rarity = sum(index.idf(t) for t in common) / (len(common) * max_idf)
        else:
            rarity = 1.0
        feats = compute_features(subject, s_tokens, entity, e_tokens, sim, rarity, weak)
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
