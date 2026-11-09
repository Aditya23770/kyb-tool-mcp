# Changelog

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
