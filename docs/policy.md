# Policy: how decisions are made

Decisions come only from `policy.decide()`, driven by a versioned YAML file. The default is
`src/kyb_tool_mcp/data/policy.default.yaml`. To customise it:

```bash
kyb-tool-mcp init --dir ./kyb-config          # writes policy.yaml, providers.yaml, .env.example
# edit kyb-config/policy.yaml, bump `version`
export KYB_POLICY_FILE=./kyb-config/policy.yaml
```

The MCP client can read the active policy as the `kyb://policy` resource or through the `explain_policy` tool.

## Decision rule

| Condition | Decision |
| --- | --- |
| any `critical` flag | **REJECT** |
| any `high` flag, or total score ≥ 30 | **ESCALATE** |
| otherwise | **APPROVE** |

Score = sum of severity points (critical 100, high 40, medium 15, low 5).
Risk level: ≤ 14 LOW, ≤ 39 MEDIUM, else HIGH (REJECT is always HIGH).

## Flags

| Step | Flag | Severity |
| --- | --- | --- |
| Registry | `REGISTRY_NOT_FOUND` | high |
| | `COMPANY_INACTIVE` (dissolved, liquidation, administration...) | critical |
| | `NAME_MISMATCH` (≥ `name_medium` → medium, else high; former name → medium) | medium/high |
| | `ADDRESS_MISMATCH`, `NEW_ENTITY`, `SIC_INDUSTRY_MISMATCH` | medium |
| Ownership | `UNDECLARED_DIRECTOR`, `UNKNOWN_DIRECTOR`, `OPAQUE_OWNERSHIP`, `NO_UBO_DATA` | high |
| | `NO_UBO_DATA` for a PLC (listed companies disclose holders to the market, not a PSC register) | medium |
| | `OWNERSHIP_MISMATCH` | medium |
| Screening | `SANCTIONS_MATCH` | critical |
| | `PEP_MATCH`, `CRIME_MATCH`, `POSSIBLE_MATCH` (needs review) | high |
| Website | `WEBSITE_UNREACHABLE`, `PARKED_DOMAIN`, `NEW_DOMAIN`, `INDUSTRY_MISMATCH` | medium |
| | `PROHIBITED_GOODS` | high |
| | `DOMAIN_NAME_MISMATCH`, `WEBSITE_BLOCKS_AUTOMATION` (site online but refuses bots), `NO_WEBSITE_DECLARED` | low |
| System | `PROVIDER_ERROR` | high |
| | `CHECK_NOT_CONFIGURED` | low |

## Screening adjudication

Each candidate from the local index is labelled:

- **FALSE_POSITIVE** (auto-cleared, listed in `aml_info.cleared` with reasons): any one of
  - person vs company/vessel mismatch;
  - birth years differ by more than `dob_year_diff_gt`;
  - nationality conflict and name similarity < `nationality_conflict_name_below`;
  - weak-alias match below `weak_alias_name_min`.
- **TRUE_MATCH**:
  - people: name ≥ `true_match.name_min` **and** birth year (or year + month) matches;
  - companies: name ≥ `company_name_min`;
  - a weak alias alone never confirms a match.
- **NEEDS_REVIEW** for everything else, raised as `POSSIBLE_MATCH`, which escalates.

## Changing thresholds safely

Run `python -m benchmarks.run --gate` before and after the change. The change is accepted only if it
breaks no target and makes no metric worse. Tune on the dev split; check the holdout split before
merging.
