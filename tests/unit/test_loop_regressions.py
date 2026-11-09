"""Regression tests from improvement-loop iteration 1 (see benchmarks/IMPROVEMENT_LOG.md).

Each test reproduces a benchmark C failure observed on the baseline run.
"""

from __future__ import annotations

import pytest

from kyb_tool_mcp.config import load_policy
from kyb_tool_mcp.matching.adjudicate import adjudicate
from kyb_tool_mcp.matching.blocking import score_entity
from kyb_tool_mcp.matching.normalize import tokens_for
from kyb_tool_mcp.schemas.screening import HitOutcome, ListEntity, ScreeningSubject

POLICY = load_policy().screening


def company_outcome(subject: str, *entity_names: str) -> tuple[HitOutcome | None, float]:
    s = ScreeningSubject(name=subject, type="company")
    e = ListEntity(
        id="x", source="ofac_sdn", schema="Organization", caption=entity_names[0], names=list(entity_names)
    )
    cand = score_entity(None, s, tokens_for(s.name, "company"), e)
    if cand is None or cand.name_score < POLICY.candidate_min:
        return None, cand.name_score if cand else 0.0
    return adjudicate(s, cand, POLICY)[0], cand.name_score


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        # clean_01: shared generic words "north" / "works" ~ "workers"
        ("Oakfield Northgate Works Limited", "KUMBYOL TRADING COMPANY OF NORTH KOREAN WORKERS' PARTY"),
        # clean_08: only "group" in common
        ("Bluebell Tamar Group Ltd", "AMAN GROUP"),
        # name_collision_05: only "trading" in common + loose first-token similarity
        ("Linden Pennine Trading Limited", "LIBAN TRADING"),
        ("Global Trading Services Ltd", "SEPEHR ENERGY GLOBAL TRADING"),
    ],
)
def test_generic_company_words_do_not_alert(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome in (None, HitOutcome.FALSE_POSITIVE), (outcome, score)


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("Rosneft Oil Company", "OPEN JOINT-STOCK COMPANY ROSNEFT OIL COMPANY"),
        ("Nordstar Shipping Ltd", "NORDSTAR SHIPPING LLC"),
        ("Golden Crescent Trading Company", "GOLDEN CRESCENT TRADING CO"),
        ("Aman Group Limited", "AMAN GROUP"),
        ("Liban Trading Ltd", "LIBAN TRADING"),
    ],
)
def test_distinctive_company_names_still_match(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome == HitOutcome.TRUE_MATCH, (outcome, score)


# ---- iteration 2: one fuzzy distinctive word must not carry a company match ------------------


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("Oakfield Northgate Works Limited", "NORTHSTAR TRADING CORPORATION"),  # clean_01
        ("Oakfield Northgate Works Limited", "NORATEC HOLDINGS LTD"),  # clean_01
        ("Marlow Meridian Partners Limited", "MARLENA TRADING LIMITED"),  # name_collision_02
    ],
)
def test_fuzzy_single_company_word_does_not_alert(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome in (None, HitOutcome.FALSE_POSITIVE), (outcome, score)


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("Rossneft Ltd", "ROSNEFT"),  # typo
        ("Gasprom Limited", "GAZPROM"),  # transliteration
        ("Sberbnk Ltd", "SBERBANK"),
        ("Rosneft Ltd", "ROSNEFT OIL COMPANY"),  # shortened name of a listed entity -> at least review
    ],
)
def test_company_spelling_variants_still_caught(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome in (HitOutcome.TRUE_MATCH, HitOutcome.NEEDS_REVIEW), (outcome, score)


# ---- iteration 3 (from out-of-distribution benchmark D on real Companies House names) ---------


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("ADF Services Limited", "ADF"),
        ("A7 Investments Limited", "A7 LIMITED LIABILITY COMPANY"),
        ("Agora Solutions Limited", "AGOR DMCC"),
        ("Aktivo Ltd", "JOINT STOCK COMPANY AKTIV"),
        ("Aero Media Ltd", "ERA-MEDIA TOV"),
        ("Advanced UK Solutions Limited", "ADVANCED INDUSTRIAL TECHNOLOGIES GMBH"),
        ("Age Partners Ltd", "GLOBAL AGE LIMITED"),
        ("AK Consultants Ltd", "AK SYSTEMS"),
        ("Aladia Ltd", "ALIADA GROUP, INC."),
    ],
)
def test_partial_company_overlap_never_auto_confirms(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome != HitOutcome.TRUE_MATCH, (outcome, score)


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("Ocean Grill Ltd", "BEYOND CENTER LIMITED"),
        ("Above and Beyond Holdings Ltd", "BEYOND CENTER LIMITED"),
        ("1st Class Auto Recovery Ltd", "JOINT STOCK COMPANY CLASS"),
        ("ACS Global Education Ltd", "ACS SYSTEMS LIMITED"),
        ("Akelind Properties Ltd", "ALDAR PROPERTIES"),
        ("Advanced Coated Products Limited", "ADVANCED INDUSTRIAL TECHNOLOGIES GMBH"),
    ],
)
def test_real_uk_company_noise_is_cleared(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome in (None, HitOutcome.FALSE_POSITIVE), (outcome, score)


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("Rosneft Oil Company", "OPEN JOINT-STOCK COMPANY ROSNEFT OIL COMPANY"),
        ("Nordstar Shipping Ltd", "NORDSTAR SHIPPING LLC"),
        ("Aktiv JSC", "JOINT STOCK COMPANY AKTIV"),
        ("Golden Crescent Trading Company", "GOLDEN CRESCENT TRADING CO"),
    ],
)
def test_full_company_name_still_confirms(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome == HitOutcome.TRUE_MATCH, (outcome, score)


def test_exact_name_with_conflicting_dob_goes_to_review() -> None:
    # UK and UN lists record different DOBs for the same person: an exact name must not be auto-cleared.
    from kyb_tool_mcp.schemas.screening import PartialDate

    s = ScreeningSubject(name="Amor Mohamed Ghedeir", birth_year=1965, birth_month=12)
    e = ListEntity(
        id="un-1",
        source="un_consolidated",
        schema="Person",
        caption="AMOR MOHAMED GHEDEIR",
        names=["AMOR MOHAMED GHEDEIR"],
        birth_dates=[PartialDate(year=1958)],
    )
    cand = score_entity(None, s, tokens_for(s.name, "person"), e)
    assert cand is not None
    assert adjudicate(s, cand, POLICY)[0] == HitOutcome.NEEDS_REVIEW


def test_near_name_with_conflicting_dob_still_cleared() -> None:
    from kyb_tool_mcp.schemas.screening import PartialDate

    s = ScreeningSubject(name="David Hargreave", birth_year=1987)
    e = ListEntity(
        id="o-1",
        source="ofac_sdn",
        schema="Person",
        caption="David HARGREAVES",
        names=["David HARGREAVES"],
        birth_dates=[PartialDate(year=1958, month=6)],
    )
    cand = score_entity(None, s, tokens_for(s.name, "person"), e)
    assert cand is not None and adjudicate(s, cand, POLICY)[0] == HitOutcome.FALSE_POSITIVE


def test_exact_full_company_name_confirms_even_if_distinctive_part_is_short() -> None:
    # benchmark C sanctioned_company_02: "B-Finance Ltd" vs listed "B-FINANCE LTD" (only "b" is distinctive).
    outcome, score = company_outcome("B-Finance Ltd", "B-FINANCE LTD")
    assert outcome == HitOutcome.TRUE_MATCH, (outcome, score)
    outcome, _ = company_outcome("B-Finance Ltd", "BASEEJ")
    assert outcome != HitOutcome.TRUE_MATCH


# ---- iteration 4: wrong auto-confirms found on FRESH real UK data (benchmark D, psc2/basic2) ----


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("City Property Development Ltd", "LIMITED LIABILITY COMPANY NEW CITY"),
        ("Capital Energy Group Ltd", "ENERGY GLOBAL INTERNATIONAL FZE"),
        ("Care Resourcing Limited", "INTERNATIONAL GENERAL RESOURCING FZE"),
        ("Clifton Care Services Limited", "CLIFTON TRADING LIMITED"),
        ("Capital Transport Ltd", "TRADING AND TRANSPORT SERVICES COMPANY, LTD"),
        ("Centre Start International Trade Limited", "JOINT STOCK COMPANY START"),
        ("Alco Capital Limited", "ALCO"),
        ("City Property Development Ltd", "CITY DEVELOPMENT LIMITED LIABILITY COMPANY"),
        ("Club International Limited", "JOINT STOCK COMMERCIAL BANK INTERNATIONAL FINANCIAL CLUB"),
        ("Al Hadi Holdings Limited", "AL-HADID"),
        ("Advance Social Care Holding Limited", "SOCIAL DESIGN AGENCY"),
    ],
)
def test_match_on_leftover_words_only_never_auto_confirms(subject: str, entity: str) -> None:
    # Generic words were stripped from both names, leaving one shared word that looked like "100% coverage".
    outcome, score = company_outcome(subject, entity)
    assert outcome != HitOutcome.TRUE_MATCH, (outcome, score)


def person_outcome(subject: str, birth_year: int, listed: str) -> HitOutcome | None:
    from kyb_tool_mcp.schemas.screening import PartialDate

    s = ScreeningSubject(name=subject, birth_year=birth_year)
    e = ListEntity(
        id="p",
        source="ofac_sdn",
        schema="Person",
        caption=listed,
        names=[listed],
        birth_dates=[PartialDate(year=birth_year)],
    )
    cand = score_entity(None, s, tokens_for(s.name, "person"), e)
    return adjudicate(s, cand, POLICY)[0] if cand else None


@pytest.mark.parametrize(
    ("subject", "listed"),
    [
        ("Mohammed Arif", "Said Mohamed ARIF"),  # listed given name missing from applicant
        ("Jeffrey Mohammed Ali", "Ali Mohammed"),  # applicant given name missing from listing
    ],
)
def test_person_with_unmatched_name_part_is_not_auto_confirmed(subject: str, listed: str) -> None:
    assert person_outcome(subject, 1970, listed) == HitOutcome.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("subject", "listed"),
    [("John Anthony Fallon", "John Anthony FALLON"), ("Viktor Bout", "Viktor Anatolyevich BOUT")],
)
def test_full_person_name_with_dob_still_confirms_or_reviews(subject: str, listed: str) -> None:
    assert person_outcome(subject, 1967, listed) in (HitOutcome.TRUE_MATCH, HitOutcome.NEEDS_REVIEW)
    assert person_outcome("John Anthony Fallon", 1962, "John Anthony FALLON") == HitOutcome.TRUE_MATCH


# ---- iteration 5: wrong auto-confirms on the untouched sample (psc3/basic3) ----


@pytest.mark.parametrize(
    ("subject", "entity"),
    [
        ("Ocean Co Holdings Limited", "OCEAN INC."),  # "holdings" is descriptive, not a legal form
        ("New Horizons Trade Limited", "NEW HORIZONS TRADING LIMITED"),  # near-spelling, not the same word
    ],
)
def test_near_identical_company_goes_to_review(subject: str, entity: str) -> None:
    assert company_outcome(subject, entity)[0] == HitOutcome.NEEDS_REVIEW


def test_near_spelled_surname_goes_to_review() -> None:
    assert person_outcome("Mohammed Hamdani", 1967, "Mohammed HAMANI") == HitOutcome.NEEDS_REVIEW


def test_transliteration_variant_still_confirms() -> None:
    assert person_outcome("Mohamed Abdullah", 1970, "Mohammed ABDULLAH") == HitOutcome.TRUE_MATCH
    assert company_outcome("B-Finance Ltd", "B-FINANCE LTD")[0] == HitOutcome.TRUE_MATCH
    assert company_outcome("Ocean Inc", "OCEAN INC.")[0] == HitOutcome.TRUE_MATCH


# ---- iteration 6: live Companies House run (real directors of real companies) ----


def person_unknown_dob(subject: str, listed: str) -> HitOutcome | None:
    s = ScreeningSubject(name=subject)
    e = ListEntity(id="p", source="ofac_sdn", schema="Person", caption=listed, names=[listed])
    cand = score_entity(None, s, tokens_for(s.name, "person"), e)
    if cand is None or cand.name_score < POLICY.candidate_min:
        return None
    return adjudicate(s, cand, POLICY)[0]


@pytest.mark.parametrize(
    ("subject", "listed"),
    [
        ("Alia Fazal", "Fazal RAHIM"),  # only the surname-ish token is shared
        ("Alia Fazal", "Muhammad AL-GHAZALI"),
        ("Robert Berry", "Robert BARRERA"),  # only the given name is shared
        ("Alasdair George Murray", "George AL HASAWANI"),
        ("Allan Douglas Pelan", "Douglas MPANO"),
    ],
)
def test_one_shared_name_part_is_cleared(subject: str, listed: str) -> None:
    assert person_unknown_dob(subject, listed) in (None, HitOutcome.FALSE_POSITIVE)


@pytest.mark.parametrize(
    ("subject", "listed"),
    [
        ("Viktor Bout", "Viktor Anatolyevich BOUT"),
        ("Viktor Butt", "Viktor BOUT"),
        ("Mohamed Ali Hassan", "Mohammed Ali HASAN"),
    ],
)
def test_full_or_variant_names_still_alert(subject: str, listed: str) -> None:
    assert person_unknown_dob(subject, listed) in (HitOutcome.NEEDS_REVIEW, HitOutcome.TRUE_MATCH)


@pytest.mark.parametrize(
    ("subject", "entity"),
    [("Tesco PLC", "OOO 'DSK'"), ("Nalow Ltd", "NALU"), ("Nalow Ltd", "NLA")],
)
def test_short_sound_alike_company_is_cleared(subject: str, entity: str) -> None:
    outcome, score = company_outcome(subject, entity)
    assert outcome in (None, HitOutcome.FALSE_POSITIVE), (outcome, score)


def test_one_word_spelling_variant_still_alerts() -> None:
    assert company_outcome("Gasprom Ltd", "GAZPROM")[0] in (HitOutcome.NEEDS_REVIEW, HitOutcome.TRUE_MATCH)


def test_retail_supermarket_matches_retail_sic() -> None:
    from kyb_tool_mcp.checks.common import industry_similarity

    sic = "Retail sale in non-specialised stores with food, beverages or tobacco predominating"
    assert industry_similarity("retail supermarket", sic) >= load_policy().registry.sic_match_min
    assert (
        industry_similarity("florist", "Accounting and auditing activities")
        < load_policy().registry.sic_match_min
    )


def test_long_transliterated_patronymic_confirms_with_dob() -> None:
    # Live run: "Viktor Anatolyevich Bout" vs OFAC "Viktor Anatolijevitch BOUT", same DOB -> must reject.
    assert (
        person_outcome("Viktor Anatolyevich Bout", 1967, "Viktor Anatolijevitch BOUT")
        == HitOutcome.TRUE_MATCH
    )
    assert (
        person_outcome("Mohammed Hamdani", 1967, "Mohammed HAMANI") == HitOutcome.NEEDS_REVIEW
    )  # short: still review
