# TrustLedger

**Trust, backed by evidence.**

TrustLedger checks whether a UK company is safe to do business with, from inside your AI assistant.
It looks the company up in Companies House, screens the company, its directors and owners against
the OFAC and UN sanctions lists, and checks its website. You get **APPROVE**, **ESCALATE** or
**REJECT**, with the evidence behind every flag.

It runs on your machine: the names you screen never leave it.

[![CI](https://github.com/Aditya23770/kyb-tool-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Aditya23770/kyb-tool-mcp/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](LICENSE)
[![Website](https://img.shields.io/badge/website-TrustLedger-14213d)](https://aditya23770.github.io/kyb-tool-mcp/)

> Decision support, not legal advice. You stay responsible for your compliance decisions.

## Install

Needs Python 3.11+ and a free [Companies House API key](https://developer.company-information.service.gov.uk/).
No Claude or other LLM API key is needed: your assistant is the LLM.

```bash
uvx kyb-tool-mcp update-lists      # one-off: build the local sanctions index
```

**Claude Code**

```bash
claude mcp add kyb-tool-mcp -e COMPANIES_HOUSE_API_KEY=your-key -- uvx kyb-tool-mcp
```

**Claude Desktop / Cursor**: add to `claude_desktop_config.json` or `.cursor/mcp.json`, then restart.

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

**pip**

```bash
pip install kyb-tool-mcp
```

Without the Companies House key, registry checks report `not_configured`. Sanctions and website checks still run.

## Use it

Ask your assistant in plain English:

- "Run a KYB check on company 09876543, director Sarah Holloway, born June 1979." → decision, flags with evidence, analyst note
- "Is Viktor Bout, born 1967, on a sanctions list?" → match / needs review / cleared, with reasons
- "Does harbourline-logistics.co.uk look real?" → reachable, parked, domain age, industry fit

Also works from the terminal (`kyb-tool-mcp review case.json`) and from Python (`from kyb_tool_mcp import review_case`).

## Benchmark

| | Result |
| --- | --- |
| Sanctioned names caught | **99.4%** |
| Real UK companies with no false alert | **92.9%** |
| Wrongly confirmed as sanctioned | **0.04%** |
| Test cases decided correctly | **100%** |

Measured on real public UK data the tool was never tuned on (992 sanctioned designations, 5,000
companies) plus 168 test cases.

## Coming next (phase 2)

- One-click install for Claude Desktop (desktop extension)
- Add it as a connector by URL (hosted version)
- A web form: check a company in the browser, no install

## More

[Policy and flags](docs/policy.md) · [Data sources](docs/providers.md) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE) ([data licences](NOTICE))
