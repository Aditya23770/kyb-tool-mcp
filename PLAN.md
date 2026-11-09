# kyb-tool-mcp: End-to-End Implementation Plan

Open-source KYB (Know Your Business) screening and case-review engine, shipped as a **Python library, a CLI and an MCP server**. Built as proof-of-work for AiPrise (KYB/KYC/AML platform).

Use this file in Claude Code: put it in the repo root as `PLAN.md`, create `CLAUDE.md` from section 14, then run the phases in order. Each phase has a goal, tasks, acceptance criteria and a ready-to-paste prompt.

---

## 0. One-paragraph summary

`kyb-tool-mcp` takes a business onboarding application, checks it against public registries, sanctions lists and the business's website, reconciles what the business **claimed** with what the **sources show**, and returns `APPROVE / REJECT / ESCALATE` with flags that each cite evidence. Data sources are pluggable providers: free sources by default (non-commercial), and companies plug in their own licensed keys. Decisions come from deterministic, versioned rules. The LLM only summarises websites, explains ambiguous name matches and writes the analyst note. A benchmark suite proves the design beats both plain fuzzy rules and "just give an LLM the tools".

---

## 1. Goals, non-goals, success criteria

### Goals
1. Automate KYB workflow steps: **registry verification (2)**, **sanctions/PEP screening + hit adjudication (4)**, **website legitimacy (6)**, **decision (7)**.
2. Pluggable data providers with two modes: **free default** and **bring-your-own-key**.
3. Three interfaces from one core: library, CLI, MCP server.
4. Every flag traceable to stored evidence; full audit log; versioned policy.
5. Reproducible benchmarks with published numbers.

### Non-goals (v1)
- ID document / selfie / liveness KYC.
- Document tamper detection.
- Ongoing monitoring / scheduled re-screening.
- Non-UK registries in the free tier (provider interface supports them later).
- Hosted SaaS. Users self-host.

### Success criteria (v1 release)
- `pip install kyb-tool-mcp` works; `kyb-tool review case.json` returns valid decision JSON.
- MCP server works in Claude Desktop / Claude Code / Cursor.
- **0 false approvals** on benchmark C.
- Benchmark tables A, B, C in the README.
- CI green: lint, types, unit + recorded integration tests.

---

## 2. Domain background (short)

| Term | Meaning |
| --- | --- |
| KYB | Verifying a business: exists, who owns/runs it, not sanctioned, does what it claims |
| UBO / PSC | Ultimate Beneficial Owner; UK calls it Person with Significant Control (usually 25%+) |
| Sanctions list | Government blacklist (OFAC SDN, UN, UK, EU); dealing with listed parties is illegal |
| PEP | Politically Exposed Person; higher risk, needs review, not banned |
| Hit | A list entry that resembles the subject; most are false positives |
| Adjudication | Deciding whether a hit is a true match |
| False approval | Approving a business that should have been rejected or escalated; the metric that must be 0 |

---

## 3. Data sources

**Verify every URL and licence before shipping.** Licences change.

### 3.1 Free default (non-commercial / open source)

| Step | Source | Access | Licence note |
| --- | --- | --- | --- |
| 2 Registry | UK Companies House REST API: `https://api.company-information.service.gov.uk` | Free key, HTTP Basic (key as username). ~600 req / 5 min | Open government data |
| 2 Registry | GLEIF LEI API: `https://api.gleif.org/api/v1/` | No key | Open data |
| 2b Owners | Companies House PSC endpoint + free daily PSC bulk snapshot | Free | Open government data |
| 4a Sanctions | OFAC SDN list (US) | Free download (find current URL via OFAC Sanctions List Service) | US government, public |
| 4a Sanctions | UN Security Council consolidated list (XML) | Free download | Public |
| 4a Sanctions | UK sanctions list, EU consolidated list | Free download (phase 9) | Check reuse terms |
| 4a/4b Sanctions + PEP | OpenSanctions bulk `default` dataset (`entities.ftm.json`) | Free download | **CC BY-NC: non-commercial only** |
| 6 Website | Direct fetch with httpx | n/a | n/a |
| 6 Domain age | RDAP: `https://rdap.org/domain/{domain}` | No key | Public |
| 6 Industry | Companies House SIC code list (CSV, vendored in repo) | Free | Open government data |
| LLM | Anthropic / OpenAI / local via Ollama | User's key | User's account |

### 3.2 Bring-your-own-key (companies)

| Step | Provider options (examples) |
| --- | --- |
| 2 Registry (global) | OpenCorporates API, Middesk (US), India MCA data vendors, AiPrise KYB API |
| 4 Sanctions/PEP/adverse media | OpenSanctions licensed API, ComplyAdvantage, Dow Jones Risk & Compliance, LSEG World-Check |
| 6 Web risk | Google Web Risk API |
| LLM | Company's own Anthropic / OpenAI / Azure / Bedrock key, or local model |

v1 ships these free providers: `companies_house`, `gleif`, `ofac_sdn`, `un_consolidated`, `opensanctions_bulk` (flagged non-commercial), `website_http`, `rdap`. v1 ships one licensed adapter as a template: `opensanctions_api`.

**Rule:** if a check has no configured provider, output it as `"not_configured"`. A missing check must never pass silently.

---

## 4. Architecture

```
               Library  ·  CLI  ·  MCP server  ·  (later) REST
                               │
                        ReviewPipeline (orchestrator)
   ┌──────────────┬────────────┼──────────────┬───────────────┐
   │              │            │              │               │
Registry       Screening     Website        Compare        LLM service
providers      engine        providers      (claims vs     (optional:
(CH, GLEIF,    (index →      (HTTP, RDAP)    registry)      website summary,
 licensed)     block → score                               hit explanation,
               → adjudicate)                               analyst note)
   └──────────────┴────────────┴──────────────┴───────────────┘
                               │
                         Evidence store  ──►  Evidence validator
                               │
                        Policy engine (policy.yaml, pure Python)
                               │
                  Decision JSON  +  Audit log (SQLite)
```

### Design rules
1. **Deterministic core.** Same input + same provider data + same policy version = same decision.
2. **LLM never decides.** It may summarise, explain, recommend. Policy decides.
3. **Evidence or it didn't happen.** Every flag cites `evidence_id`s that exist in the store; LLM quotes must appear verbatim in fetched content.
4. **Providers are dumb adapters.** They fetch and map to canonical schemas. No business logic.
5. **Fail closed.** Provider errors become flags (`PROVIDER_ERROR`, high), never silent passes.

### Two operating modes
- **Pipeline mode (`review_case`)**: fixed orchestration of all configured checks. Default for library, CLI, and the MCP tool `review_kyb_case`.
- **Tool mode (MCP raw tools)**: the user's own LLM (e.g. Claude) calls `registry_lookup`, `screen_entity`, `check_website` itself. No LLM key needed by kyb-tool-mcp.

---

## 5. Tech stack

- Python 3.11+, `uv` for env, build and publish
- `pydantic` v2, `pydantic-settings`
- `httpx` (async) + `tenacity` (retries) + `hishel` or simple SQLite cache
- `rapidfuzz`, `jellyfish` (phonetics), `unidecode`, `anyascii` (transliteration)
- `sqlite3` with FTS5 for the local sanctions index
- `pyyaml` for policy
- `typer` + `rich` for CLI
- `mcp` (official Python SDK, `FastMCP`)
- LLM: `anthropic`, `openai` SDKs behind one small interface; `ollama` via OpenAI-compatible endpoint
- Tests: `pytest`, `pytest-asyncio`, `respx`, `hypothesis`
- Quality: `ruff`, `mypy`
- Benchmarks: `pandas`, `scikit-learn` (metrics, calibration)

Licence: Apache-2.0.

---

## 6. Repository structure

```
kyb-tool-mcp/
├─ PLAN.md                       # this file
├─ CLAUDE.md                     # instructions for Claude Code (section 14)
├─ README.md
├─ LICENSE                       # Apache-2.0
├─ pyproject.toml                # entry points: kyb-tool-mcp, kyb-tool-mcp
├─ .env.example
├─ policy.default.yaml
├─ providers.default.yaml
├─ src/kyb_tool_mcp/
│  ├─ __init__.py                # review_case, screen_entity, __version__
│  ├─ config.py                  # Settings (env), loads providers + policy
│  ├─ schemas/
│  │  ├─ application.py          # Application, Person, DeclaredUBO
│  │  ├─ registry.py             # CompanyRecord, Officer, PSC
│  │  ├─ screening.py            # ScreeningSubject, Candidate, Hit, HitOutcome
│  │  ├─ website.py              # WebsiteSnapshot, WebsiteAssessment
│  │  ├─ evidence.py             # Evidence, EvidenceRef
│  │  └─ decision.py             # Flag, Severity, Decision, ReviewResult
│  ├─ providers/
│  │  ├─ base.py                 # Protocols + registry of providers
│  │  ├─ companies_house.py
│  │  ├─ gleif.py
│  │  ├─ sanctions/
│  │  │  ├─ loaders/ofac_sdn.py
│  │  │  ├─ loaders/un_consolidated.py
│  │  │  ├─ loaders/opensanctions_bulk.py
│  │  │  └─ opensanctions_api.py # licensed adapter template
│  │  ├─ website_http.py
│  │  └─ rdap.py
│  ├─ matching/
│  │  ├─ normalize.py            # names, legal suffixes, scripts, dates
│  │  ├─ index.py                # SQLite FTS5 build + query
│  │  ├─ blocking.py             # candidate generation
│  │  ├─ features.py             # pairwise features
│  │  ├─ scorer.py               # rule score (+ optional calibrated model)
│  │  └─ adjudicate.py           # TRUE_MATCH / FALSE_POSITIVE / NEEDS_REVIEW
│  ├─ checks/
│  │  ├─ registry_checks.py      # step 2 rules → flags
│  │  ├─ screening_checks.py     # step 4 → flags
│  │  └─ website_checks.py       # step 6 → flags
│  ├─ llm/
│  │  ├─ client.py               # provider-agnostic interface
│  │  ├─ website_assessor.py     # structured output
│  │  ├─ hit_explainer.py
│  │  └─ note_writer.py
│  ├─ evidence.py                # EvidenceStore + validator
│  ├─ policy.py                  # load policy.yaml, decide()
│  ├─ pipeline.py                # ReviewPipeline / review_case()
│  ├─ audit.py                   # SQLite audit log
│  ├─ cli.py
│  └─ mcp_server.py
├─ data/
│  ├─ sic_codes.csv
│  ├─ legal_suffixes.csv
│  └─ prohibited_keywords.yaml
├─ scripts/
│  ├─ update_lists.py            # download + build sanctions index
│  └─ record_fixtures.py         # record real API responses for tests
├─ tests/
│  ├─ unit/
│  ├─ integration/               # replay recorded responses, fake LLM
│  ├─ fixtures/
│  └─ test_mcp.py
├─ benchmarks/
│  ├─ a_matching/                # OpenSanctions Pairs sample
│  ├─ b_screening/               # noisy queries + UK PSC decoys
│  ├─ c_cases/                   # KYB cases with planted problems
│  ├─ baselines/raw_llm_agent.py # "just give the LLM the tools"
│  └─ report.py                  # writes benchmarks/RESULTS.md
├─ docs/
│  ├─ providers.md               # how to write a provider
│  └─ policy.md
├─ Dockerfile
└─ .github/workflows/ci.yml
```

---

## 7. Core schemas (sketch)

```python
# schemas/application.py
class Person(BaseModel):
    name: str
    birth_year: int | None = None
    birth_month: int | None = None
    nationality: str | None = None          # ISO-3166 alpha-2

class DeclaredUBO(Person):
    ownership_pct: float | None = None

class Application(BaseModel):
    case_id: str
    legal_name: str
    trading_name: str | None = None
    country: str                             # "GB" in v1 free tier
    registration_number: str | None = None
    registered_address: str | None = None
    postcode: str | None = None
    declared_industry: str | None = None
    website: HttpUrl | None = None
    directors: list[Person] = []
    ubos: list[DeclaredUBO] = []

# schemas/decision.py
class Severity(str, Enum): critical="critical"; high="high"; medium="medium"; low="low"

class Flag(BaseModel):
    code: str                                # e.g. UNDECLARED_DIRECTOR
    severity: Severity
    detail: str
    evidence: list[str]                      # evidence ids, must exist

class CheckStatus(BaseModel):
    check: str                               # registry | sanctions | pep | website
    status: Literal["done", "not_configured", "error"]
    providers: list[str]

class ReviewResult(BaseModel):
    case_id: str
    decision: Literal["APPROVE", "REJECT", "ESCALATE"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    score: int
    flags: list[Flag]
    checks: list[CheckStatus]
    aml_info: AmlInfo                        # result, num_hits, hits[], cleared[]
    analyst_note: str | None
    policy_version: str
    kyb_tool_mcp_version: str
    trace: list[TraceStep]
```

Provider protocols:

```python
class RegistryProvider(Protocol):
    name: str
    async def lookup(self, country: str, reg_no: str | None, name: str) -> Evidence[CompanyRecord]: ...

class ScreeningProvider(Protocol):
    name: str
    topics: set[str]                         # {"sanction", "role.pep", "crime"}
    async def screen(self, subject: ScreeningSubject) -> Evidence[list[Candidate]]: ...

class WebsiteProvider(Protocol):
    name: str
    async def fetch(self, url: str) -> Evidence[WebsiteSnapshot]: ...
```

`Evidence[T]` = `{id, provider, fetched_at, request, payload: T, raw_hash}`.

---

## 8. Validation rules (policy.default.yaml)

All thresholds live in YAML and are starting values to tune on benchmarks.

```yaml
version: "2026.11-1"
severity_points: {critical: 100, high: 40, medium: 15, low: 5}
decision:
  reject_if_any: [critical]
  escalate_if_any: [high]
  escalate_if_score_gte: 30
risk_levels: {low_max: 14, medium_max: 39}

registry:
  name_ok: 90
  name_medium: 75
  address_min: 80
  new_entity_months: 6
  person_name_match: 88
  inactive_statuses: [dissolved, liquidation, administration, receivership]

screening:
  candidate_min: 80
  true_match: {name_min: 92, require_dob_match: true}
  false_positive: {dob_year_diff_gt: 1, nationality_conflict_name_below: 95}
  needs_review_escalates: true
  llm_explain_needs_review: true        # explains only, never clears

website:
  parked_min_words: 200
  new_domain_days: 90
  industry_mismatch_confidence: 0.7
```

### Flags by step

**Step 2 (registry)**: `REGISTRY_NOT_FOUND` high · `COMPANY_INACTIVE` critical · `NAME_MISMATCH` medium/high · `ADDRESS_MISMATCH` medium · `NEW_ENTITY` medium · `UNDECLARED_DIRECTOR` high · `UNKNOWN_DIRECTOR` high · `OPAQUE_OWNERSHIP` (corporate PSC) high · `NO_UBO_DATA` high · `OWNERSHIP_MISMATCH` medium.

**Step 4 (screening)**: subjects = company + every director + every UBO/PSC (from application and registry, de-duplicated). `SANCTIONS_MATCH` critical · `PEP_MATCH` high · `CRIME_MATCH` high · `POSSIBLE_MATCH` high · false positives logged in `aml_info.cleared` with reasons.

**Step 6 (website)**: `WEBSITE_UNREACHABLE` medium · `PARKED_DOMAIN` medium · `NEW_DOMAIN` medium · `INDUSTRY_MISMATCH` medium · `PROHIBITED_GOODS` high · `DOMAIN_NAME_MISMATCH` low.

**System**: `PROVIDER_ERROR` high · `CHECK_NOT_CONFIGURED` low (listed in `checks`).

**Step 7**: any critical → REJECT; any high, or score ≥ 30 → ESCALATE; else APPROVE.

---

## 9. Matching engine (the technical core)

Pipeline per subject: **normalise → block → score → adjudicate → (optional) explain**.

1. **Normalise** (`normalize.py`)
   - Unicode NFKC, lowercase, strip punctuation.
   - Transliterate non-Latin to Latin (`anyascii`), keep original too.
   - Company names: strip legal suffixes from `data/legal_suffixes.csv` (Ltd, Limited, LLC, GmbH, OOO, etc.).
   - Person names: tokens sorted; drop honorifics (Mr, Dr, Sheikh); keep initials.
   - Dates: partial dates supported (year only, year+month).
2. **Index** (`index.py`): SQLite with FTS5 over all names + aliases, plus a table of phonetic keys (Double Metaphone per token) and character 3-grams for short names. Built by `scripts/update_lists.py`.
3. **Block** (`blocking.py`): union of FTS token matches, phonetic key matches, and 3-gram overlap; cap 200 candidates; keep those with name score ≥ `candidate_min`.
4. **Features** (`features.py`): token_set_ratio, Jaro-Winkler, partial ratio, phonetic overlap, alias max score, initials compatibility, DOB relation (exact / year+month / year / conflict / unknown), nationality relation, schema match (person vs company), name rarity (token frequency in index).
5. **Score** (`scorer.py`): v1 = weighted rules. Optional v2 = logistic regression trained on benchmark A sample, calibrated (Platt). Same interface.
6. **Adjudicate** (`adjudicate.py`): thresholds from policy → `TRUE_MATCH | FALSE_POSITIVE | NEEDS_REVIEW` + human-readable reasons list.
7. **Explain** (`hit_explainer.py`, optional): for `NEEDS_REVIEW` only, LLM reads both records and returns `{recommendation, reasons}`. Stored as evidence; never changes the outcome.

---

## 10. LLM usage (bounded)

| Use | Input | Output (structured) | Guard |
| --- | --- | --- | --- |
| Website assessment | page text (truncated), declared industry, SIC descriptions | `sells`, `industry_consistency`, `confidence`, `prohibited_categories[]`, `evidence_quotes[]` | every quote must be a substring of fetched text, else discard result and flag `PROVIDER_ERROR` low |
| Hit explanation | subject + candidate record | `recommendation`, `reasons[]` | informational only |
| Analyst note | final flags + checks | 3 to 6 sentence note | may only mention flags that exist |

- Temperature 0. Provider + model in config. Every call logged in `trace` with tokens and cost.
- With no LLM configured: website industry check reports `not_configured`, no explanation, template-based note. Everything else still works.

---

## 11. Interfaces

### Library
```python
from kyb_tool_mcp import review_case, screen_entity
result = await review_case(application_dict)          # ReviewResult
hits = await screen_entity({"name": "...", "birth_year": 1971, "type": "person"})
```

### CLI
```
kyb-tool init                      # writes .env.example, providers.yaml, policy.yaml
kyb-tool update-lists              # download lists + build index
kyb-tool review case.json [--out decision.json] [--explain]
kyb-tool screen "Name" --dob 1971-03 --country RU
kyb-tool registry 12345678
kyb-tool providers                 # shows configured/not configured per check
```

### MCP server (`kyb-tool-mcp`, FastMCP, stdio)
Tools:
- `review_kyb_case(application)` → full pipeline result
- `registry_lookup(country, registration_number?, name?)`
- `screen_entity(name, type, birth_year?, birth_month?, nationality?)`
- `check_website(url, declared_industry?)`
- `explain_policy()` → current rules, so the user's LLM knows how decisions are made

Resource: `kyb://policy` (current policy YAML). Tool descriptions must state that results are decision support, not legal advice.

Example client config:
```json
{
  "mcpServers": {
    "kyb-tool-mcp": {
      "command": "uvx",
      "args": ["kyb-tool-mcp"],
      "env": {
        "COMPANIES_HOUSE_API_KEY": "...",
        "ANTHROPIC_API_KEY": "...",
        "KYB_PROVIDERS_FILE": "/path/providers.yaml"
      }
    }
  }
}
```

### providers.yaml
```yaml
registry: [companies_house, gleif]
screening:
  - ofac_sdn
  - un_consolidated
  # - opensanctions_bulk        # non-commercial use only
  # - opensanctions_api         # needs OPENSANCTIONS_API_KEY + licence
pep: []                          # not_configured in free commercial mode
website: [website_http, rdap]
llm: {provider: anthropic, model: <set in .env>}
```

---

## 12. Testing

| Layer | What | Runs |
| --- | --- | --- |
| Unit | normalise, features, adjudicate, every registry/website rule, policy decide(), evidence validator, schema validation | CI, no network |
| Property (hypothesis) | a `SANCTIONS_MATCH` can never produce APPROVE; adding a flag never lowers severity of decision; decide() is deterministic | CI |
| Integration (recorded) | real Companies House / RDAP / website responses recorded with `scripts/record_fixtures.py`, replayed via `respx`; fake LLM client returning canned structured output | CI |
| MCP | start server in-process, list tools, call each tool on fixtures, validate JSON | CI |
| CLI | `review` on fixture cases, exit codes, output schema | CI |
| Benchmarks | section 13 | manual, results committed |

Golden cases in `tests/fixtures/golden/`: about 15 hand-written tricky cases (transliteration, partial DOB, corporate PSC, dissolved company, parked domain).

CI (`.github/workflows/ci.yml`): `uv sync` → `ruff check` → `mypy src` → `pytest` → `uv build`.

---

## 13. Benchmarks

Data licences: OpenSanctions data is CC BY-NC (non-commercial); benchmarking is fine. Do not commit raw third-party data; commit scripts that download it and the sampled IDs.

### A. Name matching: OpenSanctions Pairs
- Data: OpenSanctions matcher training pairs (older `pairs.json` is a free non-commercial download; current version needs a delivery token). Each line: `{judgement, left, right}` in FollowTheMoney format.
- Sample: 3,000 pairs stratified by person/company, same-script/cross-script, positive/negative. Fixed seed; save sampled IDs.
- Systems: (1) rapidfuzz threshold, (2) kyb-tool rule scorer, (3) kyb-tool calibrated model (optional), (4) LLM pairwise judge, (5) hybrid: scorer, LLM only in uncertain band.
- Metrics: precision, recall, F1 overall and per stratum; % of pairs sent to LLM; cost per 1,000 pairs.
- Reference point: the OpenSanctions Pairs paper (2026) reports 91.3% F1 for their rule matcher and up to 99.0% for GPT-4o; LLMs weaker on cross-script.

### B. Screening at scale: alert volume vs recall
- Positives: 500 list entities → generate noisy queries (transliteration variants, typos, swapped order, dropped middle name, initials, DOB present/partial/missing).
- Negatives: 5,000 real UK people from the Companies House PSC bulk snapshot (birth month/year available) treated as non-sanctioned decoys; remove any that the pipeline confirms as true matches after manual check.
- Run full blocking + adjudication against the full sanctions index.
- Metrics: recall of true targets (candidate generation recall and final recall), **alerts per 1,000 screenings** at recall ≥ 99%, % auto-cleared, p50/p95 latency.

### C. End-to-end KYB cases
- 150 to 200 cases from real UK companies (Companies House API + free company data snapshot).
- Case mix:

| Type | Construction | Expected |
| --- | --- | --- |
| Clean | application copied faithfully from registry | APPROVE |
| Dissolved | real dissolved company | REJECT |
| Wrong reg no / address | one field altered | ESCALATE |
| Undeclared / unknown director | remove or add a director | ESCALATE |
| Corporate PSC | real company owned by a company | ESCALATE |
| Sanctioned director | real sanctioned person (name + DOB) inserted | REJECT |
| PEP owner | real PEP inserted as UBO (non-commercial data) | ESCALATE |
| Name collision | common name near a listed person, different DOB/nationality | APPROVE |
| Industry mismatch | declared industry contradicts SIC + website | ESCALATE |
| Dead / parked website | unreachable or parked domain | ESCALATE |

- Systems compared: (1) rules only (no LLM), (2) **raw-LLM agent**: an LLM given only the raw tools and the instruction "review this business and decide", (3) kyb-tool hybrid pipeline.
- Each case run 3 times per system.
- Metrics: **false approvals** (target 0), decision accuracy, consistency (same decision across 3 runs), flag recall per planted problem, unsupported claims (flags/claims without evidence), cost and latency per case.

`benchmarks/report.py` writes `benchmarks/RESULTS.md` with all tables; the README links it.

---

## 14. CLAUDE.md (create this in the repo root)

```markdown
# CLAUDE.md

Project: kyb-tool-mcp, open-source KYB screening and case-review engine (library, CLI, MCP server). Read PLAN.md before any task.

## Rules
- Python 3.11, uv, pydantic v2, async httpx. Type everything; mypy must pass.
- Providers only fetch and map to schemas. No business logic in providers.
- Decisions come only from policy.py. The LLM never sets or changes a decision.
- Every Flag must cite evidence ids that exist in the EvidenceStore.
- Missing provider for a check => status "not_configured", never a silent pass.
- Provider errors => PROVIDER_ERROR flag (high), never swallowed.
- No network in unit tests. Integration tests replay recorded fixtures with respx.
- Never commit API keys, raw third-party datasets, or .env.
- Keep thresholds in policy YAML, not code.

## Commands
- uv sync
- uv run ruff check . && uv run mypy src
- uv run pytest -q
- uv run kyb-tool review tests/fixtures/cases/clean_01.json

## Done means
Tests added and passing, ruff and mypy clean, PLAN.md phase checklist ticked.
```

---

## 15. Phased build plan

Estimated total: about 2.5 to 3 working days for v1 with benchmarks. A 5-hour cut is listed in section 16.

### Phase 0: Scaffold (30 min)
Tasks: `uv init --package`, pyproject with entry points, ruff/mypy/pytest config, CI workflow, `.env.example`, LICENSE, CLAUDE.md, empty module tree from section 6.
Accept: `uv run pytest` passes (one smoke test), CI green, `kyb-tool --help` works.
> Prompt: "Read PLAN.md sections 5, 6 and 14. Scaffold the repository exactly as in section 6 with uv, entry points kyb-tool and kyb-tool-mcp, ruff, mypy, pytest, and the CI workflow. Add a smoke test. Do not implement logic yet."

### Phase 1: Schemas, config, evidence, policy (1.5 h)
Tasks: all schemas from section 7; `config.py` loading env + providers.yaml + policy.yaml; `EvidenceStore` and validator; `policy.decide()`; default YAML files.
Accept: unit tests for decide() covering every rule; property test "critical never approves"; evidence validator drops flags with unknown ids.
> Prompt: "Implement PLAN.md section 7 schemas, section 8 policy as policy.default.yaml and policy.py decide(), and evidence.py. Write unit and hypothesis tests from section 12. No providers yet."

### Phase 2: Registry provider + registry checks (2 h)
Tasks: Companies House client (profile, officers, PSC, search) with retries, rate limiting, cache; GLEIF client; `scripts/record_fixtures.py`; `registry_checks.py` producing step-2 flags; name/address normalisation.
Accept: recorded fixtures for 5 real companies (active, dissolved, corporate PSC, recently incorporated, many directors); every step-2 flag has a unit test.
> Prompt: "Implement the Companies House and GLEIF providers and registry_checks.py per PLAN.md sections 3, 7 and 8. Add record_fixtures.py and record 5 real companies covering the cases in Phase 2. Tests replay fixtures with respx."

### Phase 3: Sanctions data + matching engine (4 h)
Tasks: loaders for OFAC SDN, UN consolidated, OpenSanctions bulk (non-commercial flag); `update_lists.py` building SQLite FTS5 + phonetic + 3-gram index; normalise, block, features, scorer, adjudicate; `screening_checks.py`; `ScreeningProvider` local implementation; `opensanctions_api` adapter template.
Accept: index builds from downloaded lists; golden matching tests pass (transliteration, aliases, partial DOB, schema mismatch); screening one name < 200 ms p95 locally.
> Prompt: "Implement PLAN.md section 9 end to end: loaders, update_lists.py, SQLite FTS5 index, blocking, features, rule scorer, adjudication, and screening_checks.py. Write golden tests for the tricky name cases. Keep thresholds in policy YAML."

### Phase 4: Website checks + LLM service (2 h)
Tasks: website_http (timeouts, redirects, size cap, robots-respecting user agent `kyb-tool-mcp/<version>`), RDAP client, parked-page heuristics; provider-agnostic LLM client (Anthropic, OpenAI, OpenAI-compatible local); website assessor with structured output and quote validation; hit explainer; note writer; fake LLM for tests.
Accept: website rules tested on recorded pages; quote validator rejects invented quotes; everything works with LLM disabled.
> Prompt: "Implement website_http, rdap, website_checks.py and the llm/ package per PLAN.md sections 8 and 10. Add a FakeLLM for tests. Quotes must be verified as substrings of fetched text."

### Phase 5: Pipeline + audit (1.5 h)
Tasks: `ReviewPipeline` running checks concurrently, collecting evidence, applying policy, building `ReviewResult` with `checks` status and `trace`; SQLite audit log storing inputs, evidence hashes, flags, policy version, decision.
Accept: integration tests for 6 end-to-end fixture cases (clean, dissolved, sanctioned director, corporate PSC, name collision, parked website) give expected decisions; same input twice gives identical decisions.
> Prompt: "Implement pipeline.py and audit.py per PLAN.md sections 4 and 7. Add integration tests for the 6 cases listed in Phase 5 using recorded fixtures and FakeLLM."

### Phase 6: CLI + MCP server (1.5 h)
Tasks: Typer CLI commands from section 11; FastMCP server with the 5 tools + policy resource; tool descriptions with decision-support disclaimer.
Accept: CLI tests; MCP in-process test lists and calls every tool; manual check in Claude Desktop or Claude Code.
> Prompt: "Implement cli.py and mcp_server.py per PLAN.md section 11. Add CLI and MCP tests. Then print the exact Claude Desktop config to use locally."

### Phase 7: Benchmarks (6 to 8 h)
Tasks: data download + sampling scripts for A, B, C (fixed seeds, sampled IDs committed); raw-LLM agent baseline; runners; `report.py`.
Accept: `benchmarks/RESULTS.md` with tables for A, B, C; false approvals on C reported; every wrong decision listed with a one-line cause.
> Prompt: "Implement PLAN.md section 13. Start with benchmark C (cases + three systems + 3 runs each), then B, then A. Write report.py to produce benchmarks/RESULTS.md."

### Phase 8: Packaging, docs, release (1.5 h)
Tasks: README (problem, architecture diagram, quick start, providers table, licence notes, results, limitations, roadmap); `docs/providers.md` (how to add a provider), `docs/policy.md`; Dockerfile; publish to TestPyPI then PyPI.
Accept: fresh venv `uvx kyb-tool-mcp` runs; README quick start works copy-paste.
> Prompt: "Write README.md, docs/providers.md and docs/policy.md per PLAN.md section 17, add the Dockerfile, and prepare the TestPyPI release with uv build and uv publish."

### Phase 9 (later): extensions
UK and EU sanctions loaders · OpenCorporates and AiPrise API adapters · REST server with webhooks · ongoing monitoring (re-screen on list updates) · GDELT adverse media · calibrated model scorer · Ollama-first privacy mode docs.

---

## 16. 5-hour cut (if you need something to send fast)

| Block | Scope | Time |
| --- | --- | --- |
| 1 | Phase 0 + schemas + policy + evidence (minimal) | 60 min |
| 2 | Companies House provider + registry checks (no GLEIF) | 60 min |
| 3 | OFAC + UN loaders, simple index, rule adjudication | 75 min |
| 4 | Pipeline + CLI + MCP (skip website LLM; website reachability only) | 45 min |
| 5 | 20 benchmark-C cases, rules vs raw-LLM agent vs hybrid, README results | 60 min |

---

## 17. README outline

1. One-line pitch and a GIF of the MCP tool in Claude
2. Why: analyst time, false positives, why "LLM + tools" alone is not enough (link results)
3. What it checks (steps 2, 4, 6, 7) and what it does not
4. Architecture diagram
5. Quick start: library, CLI, MCP
6. Providers: free vs bring-your-own-key table, licence notes
7. Policy: how decisions are made, how to tune
8. Benchmarks: A, B, C headline numbers
9. Limitations and disclaimer: decision support, not legal advice; customers remain responsible for compliance
10. Roadmap, contributing, licence

---

## 18. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Licence misuse of OpenSanctions data | Non-commercial provider is opt-in and labelled; defaults use government lists |
| List download URLs or formats change | Loaders isolated; `update-lists` fails loudly; fixture tests per loader |
| Companies House rate limits | Cache, backoff, request budgeting |
| LLM hallucination | Bounded roles, quote validation, evidence validator, policy-only decisions |
| Missed sanctioned party (false negative) | Benchmark B recall target ≥ 99%; NEEDS_REVIEW always escalates |
| Personal data handling | Self-hosted, local index, optional local LLM, audit log stays on user infra |
| Scope creep | Phases fixed; Phase 9 parked |

---

## 19. Demo script (2 minutes)

1. `kyb-tool review benchmarks/c_cases/sanctioned_director_01.json` → REJECT, `SANCTIONS_MATCH` with evidence ids, list name, DOB match reason.
2. Same tool on `name_collision_03.json` → APPROVE, 4 hits auto-cleared with reasons.
3. In Claude Desktop: "Review this company: <registration number>, declared industry: electronics wholesale" → ESCALATE with `INDUSTRY_MISMATCH`.
4. Show `benchmarks/RESULTS.md`: raw-LLM agent false approvals and inconsistency vs hybrid 0 false approvals.

Pitch line: "An open, pluggable version of the case-review agents AiPrise sells: free public data by default, licensed vendors by key, deterministic decisions with evidence, benchmarked against plain rules and a raw LLM agent."
