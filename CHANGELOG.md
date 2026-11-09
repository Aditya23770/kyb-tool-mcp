# Changelog

## 0.1.0 (unreleased)

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
