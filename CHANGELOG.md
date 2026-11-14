# Changelog

## 0.2.0

Hosted connector: add TrustLedger to Claude by URL.

- New `kyb-tool-mcp serve --http [--host] [--port]`: the same tools over Streamable HTTP at `/mcp`
  (stateless, JSON responses), plus `GET /healthz` (503 when the sanctions index is missing or older
  than `KYB_INDEX_MAX_AGE_HOURS`, default 48).
- Each caller sends their own Companies House key in the `X-Companies-House-Key` request header. It is
  used for that call only: never stored, logged or returned. Without it, registry checks report
  `not_configured` with setup instructions (fail closed, as before).
- HTTP mode turns off the audit log and HTTP cache (shared server, nothing stored about users).
  `KYB_ALLOWED_HOSTS` enables Host-header checking.
- Library: `ReviewPipeline.review()` and `registry_lookup()` accept `companies_house_api_key=` per call;
  `ReviewPipeline(missing_key_hint=...)` customises the `not_configured` note.
- Security: `COMPANIES_HOUSE_API_KEY` is held as a `SecretStr`; a rejected key reports
  "the Companies House API key was rejected" without the key.
- `FakeWorld.companies_house_key` lets tests require a specific key.
- Docker image for hosting; website moved to https://trustledger.vercel.app/.
- No decision-rule changes (policy version unchanged).

## 0.1.1

Fixes from the first live run against real Companies House data.

- Sanctions: a person is no longer flagged when only one part of the name matches
  ("Alia Fazal" vs "Fazal RAHIM", "Robert Berry" vs "Robert BARRERA").
- Sanctions: short words no longer match on sound alone ("TESCO" vs "DSK"); one-word company names
  must be spelled nearly the same ("NALOW" vs "NALU").
- Sanctions: long transliterated name parts count as the same word, so a sanctioned person with a
  matching date of birth is confirmed ("Anatolyevich" / "Anatolijevitch").
- Ownership: `NO_UBO_DATA` is medium, not high, for PLCs (listed companies have no PSC register).
- Website: a site that refuses automated requests (HTTP 401/403/429) is reported as
  `WEBSITE_BLOCKS_AUTOMATION` (low), not `WEBSITE_UNREACHABLE`.
- Registry: everyday industry words map to SIC wording ("retail supermarket" matches SIC 47110).
- Index build: release SQLite cursors before swapping the index file (Windows, Python 3.11).
- Policy version `2026.11-6`.

## 0.1.0

First release: a local KYB MCP server.

- MCP tools: `review_kyb_case` (pipeline mode), `registry_lookup`, `screen_entity`, `check_website`,
  `explain_policy` (tool mode) and the `kyb://policy` resource.
- Registry: Companies House (free key) and GLEIF.
- Sanctions: local SQLite index of OFAC SDN and the UN consolidated list; OpenSanctions bulk data
  opt-in (non-commercial).
- Website: reachability, parked-domain and prohibited-goods heuristics, industry consistency, RDAP
  domain age.
- Deterministic, versioned YAML policy; every flag cites evidence; fail-closed provider errors;
  SQLite audit log.
- Benchmarks A/B/C with an improvement-loop gate.
