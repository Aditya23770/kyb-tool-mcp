# TrustLedger

**Trust, backed by evidence.** Know-your-business (KYB) checks on UK companies, from inside your AI assistant.

[![PyPI](https://img.shields.io/pypi/v/kyb-tool-mcp)](https://pypi.org/project/kyb-tool-mcp/)
[![CI](https://github.com/Aditya23770/kyb-tool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Aditya23770/kyb-tool-mcp/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](LICENSE)
[![Website](https://img.shields.io/badge/website-TrustLedger-0f8a55)](https://aditya23770.github.io/kyb-tool-mcp/)

Give it a company. It checks Companies House, screens the company, its directors and its owners
against the OFAC and UN sanctions lists, and checks the website. You get **APPROVE**, **ESCALATE**
or **REJECT**, and every flag points to the evidence behind it.

- **Runs on your machine.** Sanctions screening uses a local copy of the lists; names never leave your computer.
- **No LLM key needed.** Your assistant is the LLM. The decision comes from fixed rules, so it is repeatable and auditable.
- **Free data.** Companies House, OFAC and the UN list are free. You only need a free Companies House key.

> Decision support, not legal advice. You stay responsible for your compliance decisions.

## Quick start

**1. Get a free Companies House key** (5 minutes): register at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/),
create an application, then create a **REST** key.

**2. Build the local sanctions index** (once; re-run daily to stay current):

```bash
uvx kyb-tool-mcp update-lists
```

**3. Add it to your assistant**

Claude Code:

```bash
claude mcp add kyb-tool-mcp -e COMPANIES_HOUSE_API_KEY=your-key -- uvx kyb-tool-mcp
```

Claude Desktop or Cursor: add this to `claude_desktop_config.json` or `.cursor/mcp.json`, then restart the app.

```json
{
  "mcpServers": {
    "kyb-tool-mcp": {
      "command": "uvx",
      "args": ["kyb-tool-mcp"],
      "env": { "COMPANIES_HOUSE_API_KEY": "your-key" }
    }
  }
}
```

`uvx` comes with [uv](https://docs.astral.sh/uv/). Prefer pip? `pip install kyb-tool-mcp` gives you the same
`kyb-tool-mcp` command.

**4. Ask**

| You ask | You get |
| --- | --- |
| "Run a KYB check on company 00445790, website tesco.com, industry retail supermarket." | Decision, risk level, flags with evidence, analyst note |
| "Is Viktor Bout, born January 1967, on a sanctions list?" | Match, needs review or cleared, with the reasons |
| "Who are the directors and owners of company 00102498?" | The Companies House record |
| "Why was this case escalated?" | The rules behind the decision |

Tip: give the company number when you can, and birth year and month for people. Dates of birth are
what let the tool clear namesakes.

## What it checks

| Check | Flags it can raise |
| --- | --- |
| **Registry** | dissolved or inactive, not found, name or address mismatch, newly incorporated, industry differs from SIC code |
| **Ownership** | undeclared or unknown director, owners hidden behind another company, no owner data |
| **Sanctions** | company, every director and every owner vs OFAC SDN and the UN list; namesakes cleared on DOB and nationality |
| **Website** | unreachable, parked, newly registered, prohibited goods, different industry, domain does not match the name |

**Decision:** any critical flag (sanctions match, dissolved company) → **REJECT**. Any high flag, or
a total score of 30 or more → **ESCALATE**. Otherwise → **APPROVE**. Full list: [docs/policy.md](docs/policy.md).

Not covered: ID documents, selfies or liveness, ongoing monitoring, or non-UK registries (GLEIF is used
as a fallback).

## Two ways to use it

- **Full review:** `review_kyb_case` runs every check and returns the decision.
- **Step by step:** `registry_lookup`, `screen_entity`, `check_website` and `explain_policy` let your
  assistant investigate one thing at a time.

**Without an assistant**, for scripts, CI or your own product:

```bash
kyb-tool-mcp review case.json --exit-code   # exit code 0 approve, 3 escalate, 4 reject
kyb-tool-mcp screen "Viktor Bout" --dob 1967-01
```

```python
from kyb_tool_mcp import review_case

result = await review_case({"case_id": "c1", "legal_name": "Tesco PLC", "registration_number": "00445790"})
print(result.decision)
```

Without a Companies House key, registry checks report `not_configured` and fall back to GLEIF.
Sanctions and website checks still run.

## Benchmark

| Test | Result |
| --- | --- |
| Live check on real UK companies (20 cases across 8 types) | **20/20** |
| Sanctioned designations caught (992, UK spellings vs the UN list) | **99.4%** |
| Real UK companies with no false alert (5,000) | **92.9%** |
| Real UK companies wrongly confirmed as sanctioned | **0.04%** |
| Synthetic end-to-end cases decided correctly (168 × 3 runs) | **100%** |

The live run covers clean companies, large PLCs, dissolved companies, wrong legal names, undeclared
directors, sanctioned owners, sanctioned entities and dead websites. The real-data rows use public
UK data the tool was never tuned on.

## Troubleshooting

- **`kyb-tool-mcp` is not recognised (Windows):** your Python `Scripts` folder is not on PATH. Use
  `uvx kyb-tool-mcp`, or `python -m kyb_tool_mcp.cli`.
- **Registry checks say `not_configured`:** the key is missing. Pass it as `COMPANIES_HOUSE_API_KEY`
  in the MCP config or a `.env` file.
- **Sanctions results look out of date:** run `uvx kyb-tool-mcp update-lists` again.

## Coming next

- One-click install for Claude Desktop
- Add it to Claude as a connector by URL
- A web form: check a company in the browser, no install

## More

[Policy and flags](docs/policy.md) · [Data sources and licences](docs/providers.md) ·
[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE)
