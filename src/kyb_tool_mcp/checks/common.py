"""Small helpers shared by the check modules."""

from __future__ import annotations

import csv
import io
from functools import cache

from rapidfuzz import fuzz

from kyb_tool_mcp.config import read_package_text
from kyb_tool_mcp.matching.features import name_similarity
from kyb_tool_mcp.matching.normalize import fold, person_tokens

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "or",
        "of",
        "the",
        "for",
        "to",
        "in",
        "on",
        "with",
        "by",
        "from",
        "other",
        "n",
        "e",
        "c",
        "not",
        "elsewhere",
        "classified",
        "activities",
        "activity",
        "services",
        "service",
        "products",
        "product",
        "goods",
        "general",
        "ltd",
        "limited",
        "company",
        "business",
        "trade",
        "trading",
        "related",
        "including",
        "except",
        "via",
    ]
)


@cache
def sic_descriptions() -> dict[str, str]:
    rows = csv.reader(io.StringIO(read_package_text("sic_codes.csv")))
    next(rows, None)
    return {r[0].strip(): r[1].strip() for r in rows if len(r) >= 2}


# Words found on almost every website regardless of industry ("my account", "order support"...).
WEB_GENERIC = frozenset(
    """account accounts support contact order orders delivery customer customers office hours information
    details team terms privacy policy cookies quote quotes help login log sign home page about news blog
    email phone call today free best quality professional""".split()
)


def keywords(text: str) -> list[str]:
    return [t for t in fold(text).split() if t not in STOPWORDS and len(t) > 2]


def industry_keywords(text: str) -> list[str]:
    """Keywords for industry comparisons: generic website vocabulary removed."""
    return [t for t in keywords(text) if t not in WEB_GENERIC]


def stem(token: str) -> str:
    return token[:5]


# Everyday industry words -> words used in SIC descriptions.
INDUSTRY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "supermarket": ("retail", "food", "stores"),
    "grocery": ("retail", "food"),
    "groceries": ("retail", "food"),
    "shop": ("retail",),
    "ecommerce": ("retail",),
    "restaurant": ("restaurants", "food"),
    "cafe": ("restaurants", "food"),
    "software": ("computer", "programming"),
    "saas": ("computer", "programming"),
    "bank": ("banking", "monetary", "financial"),
    "fintech": ("financial",),
    "oil": ("petroleum", "crude"),
    "telecom": ("telecommunications",),
    "builder": ("construction",),
}


def industry_similarity(declared: str, description: str) -> float:
    """0-100 similarity between a declared industry and a SIC / website description."""
    a, b = industry_keywords(declared), industry_keywords(description)
    a = a + [s for t in a for s in INDUSTRY_SYNONYMS.get(t, ())]
    if not a or not b:
        return 0.0
    sa, sb = {stem(t) for t in a}, {stem(t) for t in b}
    overlap = 100 * len(sa & sb) / len(sa)
    return max(overlap, float(fuzz.token_set_ratio(" ".join(a), " ".join(b))) * 0.8)


def person_match(a: str, b: str) -> float:
    return name_similarity(person_tokens(a), person_tokens(b))["name_score"]


def same_person(
    name_a: str,
    year_a: int | None,
    month_a: int | None,
    name_b: str,
    year_b: int | None,
    month_b: int | None,
    threshold: float,
) -> bool:
    if person_match(name_a, name_b) < threshold:
        return False
    if year_a and year_b and year_a != year_b:
        return False
    return not (month_a and month_b and year_a == year_b and month_a != month_b)
