"""Country name / demonym -> ISO-3166 alpha-2 (compact table, enough for list data)."""

from __future__ import annotations

from kyb_tool_mcp.matching.normalize import fold

_TABLE = """
AF afghanistan afghan|AL albania albanian|DZ algeria algerian|AO angola angolan|AR argentina argentine argentinian
AM armenia armenian|AU australia australian|AT austria austrian|AZ azerbaijan azerbaijani|BH bahrain bahraini
BD bangladesh bangladeshi|BY belarus belarusian|BE belgium belgian|BJ benin|BO bolivia bolivian
BA bosnia and herzegovina bosnian|BW botswana|BR brazil brazilian|BG bulgaria bulgarian|BF burkina faso
BI burundi|KH cambodia cambodian|CM cameroon cameroonian|CA canada canadian|CF central african republic
TD chad chadian|CL chile chilean|CN china chinese|CO colombia colombian|KM comoros|CG congo republic of the congo
CD democratic republic of the congo congo kinshasa drc|CR costa rica|CI cote d ivoire ivory coast ivorian
HR croatia croatian|CU cuba cuban|CY cyprus cypriot|CZ czech republic czechia czech|DK denmark danish
DJ djibouti|DO dominican republic|EC ecuador|EG egypt egyptian|SV el salvador|GQ equatorial guinea
ER eritrea eritrean|EE estonia estonian|ET ethiopia ethiopian|FI finland finnish|FR france french|GA gabon
GM gambia|GE georgia georgian|DE germany german|GH ghana ghanaian|GR greece greek|GT guatemala|GN guinea
GW guinea bissau|HT haiti haitian|HN honduras|HK hong kong|HU hungary hungarian|IS iceland|IN india indian
ID indonesia indonesian|IR iran iranian islamic republic of iran|IQ iraq iraqi|IE ireland irish|IL israel israeli
IT italy italian|JM jamaica jamaican|JP japan japanese|JO jordan jordanian|KZ kazakhstan kazakh|KE kenya kenyan
KP north korea democratic people s republic of korea korea north dprk
KR south korea republic of korea korea south korean|KW kuwait kuwaiti|KG kyrgyzstan kyrgyz|LA laos lao
LV latvia latvian|LB lebanon lebanese|LS lesotho|LR liberia liberian|LY libya libyan|LI liechtenstein
LT lithuania lithuanian|LU luxembourg|MO macau macao|MK north macedonia macedonia|MG madagascar|MW malawi
MY malaysia malaysian|MV maldives|ML mali malian|MT malta maltese|MR mauritania mauritanian|MU mauritius
MX mexico mexican|MD moldova moldovan|MC monaco|MN mongolia mongolian|ME montenegro|MA morocco moroccan
MZ mozambique|MM myanmar burma burmese|NA namibia|NP nepal nepalese|NL netherlands dutch|NZ new zealand
NI nicaragua|NE niger|NG nigeria nigerian|NO norway norwegian|OM oman omani|PK pakistan pakistani|PS palestine palestinian
PA panama panamanian|PG papua new guinea|PY paraguay|PE peru peruvian|PH philippines filipino|PL poland polish
PT portugal portuguese|QA qatar qatari|RO romania romanian|RU russia russian federation russian|RW rwanda rwandan
SA saudi arabia saudi|SN senegal senegalese|RS serbia serbian|SC seychelles|SL sierra leone|SG singapore singaporean
SK slovakia slovak|SI slovenia slovenian|SO somalia somali|ZA south africa south african|SS south sudan
ES spain spanish|LK sri lanka sri lankan|SD sudan sudanese|SR suriname|SE sweden swedish|CH switzerland swiss
SY syria syrian syrian arab republic|TW taiwan taiwanese|TJ tajikistan tajik|TZ tanzania tanzanian|TH thailand thai
TG togo|TT trinidad and tobago|TN tunisia tunisian|TR turkey turkiye turkish|TM turkmenistan turkmen
UG uganda ugandan|UA ukraine ukrainian|AE united arab emirates uae emirati|GB united kingdom uk great britain british
US united states united states of america usa american|UY uruguay|UZ uzbekistan uzbek|VE venezuela venezuelan
VN vietnam viet nam vietnamese|YE yemen yemeni|ZM zambia zambian|ZW zimbabwe zimbabwean|BS bahamas|BB barbados
BZ belize|BM bermuda|VG british virgin islands|KY cayman islands|GI gibraltar|GG guernsey|IM isle of man|JE jersey
PR puerto rico|SM san marino|VA vatican holy see|AD andorra|NC new caledonia|FJ fiji|KN saint kitts and nevis
LC saint lucia|VC saint vincent and the grenadines|AG antigua and barbuda|GD grenada|DM dominica|SZ eswatini swaziland
CV cabo verde cape verde|ST sao tome and principe|BN brunei|BT bhutan|TL timor leste east timor|XK kosovo
"""


def _build() -> dict[str, str]:
    table: dict[str, str] = {}
    for chunk in _TABLE.replace("\n", "|").split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        code, _, rest = chunk.partition(" ")
        table[code.lower()] = code
        # Each line lists the name then demonyms; register every 1-4 word run that appears.
        words = rest.split()
        for i in range(len(words)):
            for j in range(i + 1, min(i + 6, len(words) + 1)):
                table.setdefault(" ".join(words[i:j]), code)
    # full-line registration overrides partial-run collisions
    for chunk in _TABLE.replace("\n", "|").split("|"):
        chunk = chunk.strip()
        if chunk:
            code, _, rest = chunk.partition(" ")
            table[rest.strip()] = code
    return table


_OVERRIDES = {
    "british": "GB",
    "english": "GB",
    "scottish": "GB",
    "welsh": "GB",
    "northern irish": "GB",
    "democratic peoples republic of korea": "KP",
    "korea democratic peoples republic of": "KP",
    "korea republic of": "KR",
    "iran islamic republic of": "IR",
    "russian federation": "RU",
    "united states": "US",
    "congo democratic republic of the": "CD",
    "syrian arab republic": "SY",
    "lao peoples democratic republic": "LA",
    "venezuela bolivarian republic of": "VE",
    "moldova republic of": "MD",
    "tanzania united republic of": "TZ",
}

_COUNTRIES = {**_build(), **_OVERRIDES}
_AMBIGUOUS = {
    "guinea",
    "congo",
    "korea",
    "republic",
    "of",
    "the",
    "and",
    "islands",
    "saint",
    "new",
    "south",
    "north",
    "united",
    "states",
    "central",
    "african",
    "arab",
    "democratic",
    "people",
    "s",
    "east",
    "papua",
    "virgin",
    "isle",
    "man",
    "sri",
    "san",
    "el",
    "costa",
    "sierra",
    "kingdom",
}


def to_iso2(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    f = fold(v)
    if f in _AMBIGUOUS:
        return None
    return _COUNTRIES.get(f)
