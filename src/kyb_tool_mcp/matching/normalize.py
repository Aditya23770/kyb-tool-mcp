"""Name, date and address normalisation.

Everything here is pure and deterministic; it is the foundation of matching quality, so
most improvement-loop fixes land in this file (new suffixes, honorifics, transliterations).
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from functools import cache

import jellyfish
from anyascii import anyascii

from kyb_tool_mcp.config import read_package_text
from kyb_tool_mcp.schemas.screening import PartialDate

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACES = re.compile(r"\s+")

HONORIFICS = frozenset(
    [
        "mr",
        "mrs",
        "ms",
        "miss",
        "mx",
        "dr",
        "prof",
        "professor",
        "sir",
        "dame",
        "lord",
        "lady",
        "rev",
        "reverend",
        "hon",
        "honourable",
        "sheikh",
        "shaikh",
        "shaykh",
        "sheik",
        "haji",
        "hajji",
        "hajj",
        "alhaj",
        "alhaji",
        "mullah",
        "maulana",
        "maulvi",
        "general",
        "gen",
        "col",
        "colonel",
        "major",
        "maj",
        "capt",
        "captain",
        "lt",
        "lieutenant",
        "admiral",
        "brig",
        "brigadier",
        "judge",
        "justice",
        "his",
        "her",
        "excellency",
        "president",
        "minister",
        "senator",
        "governor",
        "emir",
        "prince",
        "princess",
        "king",
        "queen",
    ]
)

# Name particles that are frequently merged / split / dropped across sources.
PARTICLES = frozenset(
    [
        "al",
        "el",
        "bin",
        "ben",
        "ibn",
        "bint",
        "abu",
        "abd",
        "abdul",
        "van",
        "von",
        "der",
        "de",
        "da",
        "di",
        "la",
        "le",
        "du",
        "del",
        "dos",
        "das",
    ]
)

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def fold(text: str) -> str:
    """NFKC -> transliterate to ASCII -> lowercase -> alnum tokens separated by single spaces."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = anyascii(t).lower()
    t = t.replace("&", " and ")
    t = t.replace("'", "").replace("`", "")
    # "L.L.C." -> "llc", "S.A." -> "sa"
    t = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", t)
    t = t.replace(".", " ")
    t = _NON_ALNUM.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


@cache
def _legal_suffixes() -> tuple[tuple[str, ...], ...]:
    rows = csv.DictReader(io.StringIO(read_package_text("legal_suffixes.csv")))
    suffixes = {tuple(fold(r["suffix"]).split()) for r in rows if r.get("suffix")}
    # Longest first so "public limited company" wins over "company".
    return tuple(sorted(suffixes, key=len, reverse=True))


def strip_legal_suffixes(tokens: list[str]) -> list[str]:
    """Remove trailing legal-form tokens (repeatedly), e.g. 'acme holdings uk ltd' -> 'acme holdings uk'."""
    toks = list(tokens)
    changed = True
    while changed and len(toks) > 1:
        changed = False
        for suf in _legal_suffixes():
            n = len(suf)
            if n < len(toks) and tuple(toks[-n:]) == suf:
                toks = toks[:-n]
                changed = True
                break
    if len(toks) > 1 and toks[0] == "the":
        toks = toks[1:]
    return toks


# Legal forms that PRECEDE the name (common in CIS / list data): "OOO Romashka", "Open Joint-Stock Company X".
LEADING_LEGAL_FORMS: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        (
            tuple(x.split())
            for x in (
                "open joint stock company",
                "closed joint stock company",
                "public joint stock company",
                "joint stock company",
                "limited liability company",
                "private joint stock company",
                "joint stock commercial bank",
                "ooo",
                "oao",
                "zao",
                "pao",
                "ao",
                "jsc",
                "ojsc",
                "cjsc",
                "pjsc",
                "tov",
                "pt",
                "cv",
            )
        ),
        key=len,
        reverse=True,
    )
)

# Words shared by huge numbers of unrelated companies. They must not carry a company-name match.
GENERIC_COMPANY_WORDS = frozenset(
    """trading trade traders group groups holding holdings company companies co corporation corp inc
    limited ltd llc international intl global general services service solutions enterprises enterprise
    industries industry industrial works partners partnership management investments investment ventures
    consulting consultants technologies technology tech systems logistics import export imports exports
    impex uk of the and for establishment est firm agency center centre office plc
    advanced advance construction central house media it properties property consultancy education products
    home care digital design studio capital finance financial engineering electrical building developments
    development estates recovery class business first 1st new premier auto motors cars foods""".split()
)


def strip_leading_legal_forms(tokens: list[str]) -> list[str]:
    for form in LEADING_LEGAL_FORMS:
        n = len(form)
        if len(tokens) > n and tuple(tokens[:n]) == form:
            return tokens[n:]
    return tokens


# Legal forms that also appear INSIDE names in list data ("Era-Media TOV Group", "X JSC Holding").
INNER_LEGAL_FORMS: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        {
            *LEADING_LEGAL_FORMS,
            *(
                tuple(x.split())
                for x in (
                    "limited liability company",
                    "llc",
                    "tov",
                    "dmcc",
                    "fzco",
                    "fze",
                    "fzc",
                    "pte",
                    "gmbh",
                    "ltda",
                    "srl",
                    "spa",
                    "sarl",
                    "kft",
                    "sdn bhd",
                    "bhd",
                )
            ),
        },
        key=len,
        reverse=True,
    )
)


def strip_inner_legal_forms(tokens: list[str]) -> list[str]:
    toks = list(tokens)
    for form in INNER_LEGAL_FORMS:
        n = len(form)
        i = 0
        while i <= len(toks) - n and len(toks) > n:
            if tuple(toks[i : i + n]) == form:
                toks = toks[:i] + toks[i + n :]
            else:
                i += 1
    return toks


def company_tokens(name: str) -> list[str]:
    return strip_inner_legal_forms(strip_leading_legal_forms(strip_legal_suffixes(fold(name).split())))


def distinctive_company_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in GENERIC_COMPANY_WORDS]


def normalize_company(name: str) -> str:
    return " ".join(company_tokens(name))


def person_tokens(name: str) -> list[str]:
    """Tokens of a person name: handles 'SURNAME, Firstname' and drops honorifics."""
    raw = name or ""
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first} {last}"
    toks = [t for t in fold(raw).split() if t not in HONORIFICS]
    return toks


def normalize_person(name: str) -> str:
    return " ".join(person_tokens(name))


def merge_particles(tokens: list[str]) -> list[str]:
    """'al', 'bin', 'van' ... glued to the following token: ['al','qaeda'] -> ['alqaeda']."""
    out: list[str] = []
    skip = False
    for i, t in enumerate(tokens):
        if skip:
            skip = False
            continue
        if t in PARTICLES and i + 1 < len(tokens):
            out.append(t + tokens[i + 1])
            skip = True
        else:
            out.append(t)
    return out


def tokens_for(name: str, kind: str) -> list[str]:
    return company_tokens(name) if kind == "company" else person_tokens(name)


def subject_key(name: str, kind: str) -> str:
    """De-duplication key: sorted normalised tokens."""
    return " ".join(sorted(tokens_for(name, kind))) + f"|{kind}"


@cache
def phonetic(token: str) -> str:
    """Phonetic key for a token (Metaphone; falls back to the token for digits / short tokens)."""
    if not token or token.isdigit() or len(token) < 2:
        return token
    key: str = jellyfish.metaphone(token)
    return key or token


def phonetic_keys(tokens: list[str]) -> list[str]:
    return [phonetic(t) for t in tokens]


# ---- dates --------------------------------------------------------------------

_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def parse_partial_dates(text: str | None) -> list[PartialDate]:
    """Parse '1960', '1960-03', '1960-03-04', '04 Mar 1960', 'Mar 1960', '1960 to 1962', 'circa 1960'."""
    if not text:
        return []
    s = text.strip().lower()
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        return [PartialDate(year=y, month=mo or None, day=int(d) if d and int(d) else None)]
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]{3})[a-z]*\s+(\d{4})", s)
    if m and m.group(2) in MONTHS:
        return [PartialDate(year=int(m.group(3)), month=MONTHS[m.group(2)], day=int(m.group(1)))]
    m = re.fullmatch(r"([a-z]{3})[a-z]*\s+(\d{4})", s)
    if m and m.group(1) in MONTHS:
        return [PartialDate(year=int(m.group(2)), month=MONTHS[m.group(1)])]
    m = re.fullmatch(r"(\d{4})\s*(?:to|-)\s*(\d{4})", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= b - a <= 10:
            return [PartialDate(year=y) for y in range(a, b + 1)]
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)  # dd/mm/yyyy
    if m:
        return [PartialDate(year=int(m.group(3)), month=int(m.group(2)), day=int(m.group(1)))]
    return [PartialDate(year=int(y)) for y in _YEAR.findall(s)][:5]


# ---- addresses ------------------------------------------------------------------

_POSTCODE = re.compile(r"\b([a-z]{1,2}\d[a-z\d]?)\s*(\d[a-z]{2})\b", re.I)

ADDRESS_ABBREVIATIONS = {
    "st": "street",
    "rd": "road",
    "ave": "avenue",
    "ln": "lane",
    "sq": "square",
    "ct": "court",
    "pl": "place",
    "blvd": "boulevard",
    "hse": "house",
    "bldg": "building",
    "fl": "floor",
    "flr": "floor",
    "ste": "suite",
    "uk": "united kingdom",
    "gb": "united kingdom",
    "england": "united kingdom",
}


def normalize_postcode(text: str | None) -> str | None:
    if not text:
        return None
    m = _POSTCODE.search(text)
    if not m:
        return fold(text).replace(" ", "") or None
    return (m.group(1) + m.group(2)).lower()


def normalize_address(text: str | None) -> str:
    toks = fold(text or "").split()
    out: list[str] = []
    for t in toks:
        out.extend(ADDRESS_ABBREVIATIONS.get(t, t).split())
    return " ".join(out)


def domain_of(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/")[0].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def domain_label(url: str) -> str:
    """'https://www.acme-widgets.co.uk/x' -> 'acme widgets'."""
    host = domain_of(url)
    parts = host.split(".")
    # drop TLD and common second-level parts
    while len(parts) > 1 and parts[-1] in {
        "com",
        "uk",
        "co",
        "org",
        "net",
        "io",
        "ltd",
        "plc",
        "biz",
        "info",
        "eu",
        "de",
        "fr",
        "us",
        "app",
        "dev",
        "shop",
        "store",
        "ai",
        "gov",
    }:
        parts = parts[:-1]
    return fold(" ".join(parts).replace("-", " "))
