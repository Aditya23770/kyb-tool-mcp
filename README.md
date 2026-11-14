# TrustLedger

**Trust, backed by evidence.** Know-your-business (KYB) checks on UK companies, from inside your AI assistant.

[![PyPI](https://img.shields.io/pypi/v/kyb-tool-mcp)](https://pypi.org/project/kyb-tool-mcp/)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](LICENSE)
[![Website](https://img.shields.io/badge/website-TrustLedger-0f8a55)](https://trustledger.vercel.app/)

Give it a company. It checks Companies House, screens the company, its directors and its owners
against the OFAC and UN sanctions lists, and checks the website. You get **APPROVE**, **ESCALATE**
or **REJECT**, and every flag points to the evidence behind it.

- **Add it to Claude by URL.** No install: paste the connector URL into claude.ai or Claude Desktop.
- **Or run it on your machine.** The pip package keeps everything local; names never leave your computer.
- **No LLM key needed.** Your assistant is the LLM. The decision comes from fixed rules, so it is repeatable and auditable.
- **Free data.** Companies House, OFAC and the UN list are free. You only need a free Companies House key.

> Decision support, not legal advice. You stay responsible for your compliance decisions.

## Quick start

**Get a free Companies House key** (5 minutes): register at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/),
create an application, then create a **REST** key. Registry checks need it; sanctions and website checks
work without it.

### 1. Claude.ai and Claude Desktop (no install)

1. In Claude, open **Customize → Connectors → Add custom connector**.
2. Name it `TrustLedger` and paste the connector URL:

   ```text
   https://CONNECTOR-URL-PENDING.run.app/mcp
   ```

3. Under **Request headers**, add `X-Companies-House-Key` with your Companies House key as the value.
4. Click **Add**, then **Connect**.

Your key is sent with each request, used for that request only, and never stored or logged. Never paste
it into the chat. On the Free plan you can add one custom connector; on Team and Enterprise an Owner adds
it in Organization settings and members click **Connect**.

### 2. pip / Python

```bash
pip install kyb-tool-mcp
kyb-tool-mcp update-lists         # builds the local sanctions index; re-run daily
```

Then use the [command line](#command-line), the [Python library](#python), or
[run your own connector](#run-your-own-connector). Details in [Use it with pip](#use-it-with-pip-no-assistant-needed).

### 3. Other AI tools

Hosted, in Claude Code:

```bash
claude mcp add --transport http trustledger https://CONNECTOR-URL-PENDING.run.app/mcp \
  --header "X-Companies-House-Key: your-key"
```

Hosted, in Cursor (`.cursor/mcp.json`) or any client that takes a URL and headers:

```json
{
  "mcpServers": {
    "trustledger": {
      "url": "https://CONNECTOR-URL-PENDING.run.app/mcp",
      "headers": { "X-Companies-House-Key": "your-key" }
    }
  }
}
```

Local (everything stays on your machine). Build the sanctions index once, then add the server:

```bash
uvx kyb-tool-mcp update-lists
claude mcp add kyb-tool-mcp -e COMPANIES_HOUSE_API_KEY=your-key -- uvx kyb-tool-mcp
```

For Claude Desktop or Cursor, add this to `claude_desktop_config.json` or `.cursor/mcp.json`, then restart the app:

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

`uvx` comes with [uv](https://docs.astral.sh/uv/).

### Ask

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

Without a Companies House key, registry checks report `not_configured` and fall back to GLEIF.
Sanctions and website checks still run.

## Your data

- **Hosted connector:** what you check is processed on the TrustLedger server to run the checks, then
  discarded. No accounts, no case history (the audit log and HTTP cache are off), and your key and inputs
  are never logged.
- **Local install:** everything runs on your computer. Sanctions screening uses a local copy of the lists,
  so names never leave your machine.

## Use it with pip (no assistant needed)

The same checks work from the terminal and from Python, which is how you automate them.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install kyb-tool-mcp
kyb-tool-mcp update-lists         # once, then daily
```

Put your key in a `.env` file in the folder you run from (or set it as an environment variable):

```bash
COMPANIES_HOUSE_API_KEY=your-key
```

### Command line

| Command | What it does |
| --- | --- |
| `kyb-tool-mcp review case.json --exit-code` | Full review. Exit code 0 approve, 3 escalate, 4 reject, 1 error |
| `kyb-tool-mcp screen "Viktor Bout" --dob 1967-01` | Screen one person (`--type company` for a company, `--nationality RU`) |
| `kyb-tool-mcp registry 00445790` | Companies House record: status, address, SIC, directors, owners |
| `kyb-tool-mcp website https://example.com --industry "retail"` | Website checks |
| `kyb-tool-mcp status` | Shows whether the key and the sanctions index are set up |
| `kyb-tool-mcp init` | Writes an editable `policy.yaml` so you can change thresholds |

`case.json` is the application you want to check. Declare the directors and owners the applicant gave
you: the tool compares them with the registry, and anyone missing is flagged `UNDECLARED_DIRECTOR`.

```json
{
  "case_id": "case-001",
  "legal_name": "Tesco PLC",
  "registration_number": "00445790",
  "website": "https://www.tesco.com",
  "declared_industry": "retail supermarket",
  "directors": [{ "name": "Jane Example", "birth_year": 1975, "birth_month": 6, "nationality": "GB" }],
  "ubos": []
}
```

### Run your own connector

The same tools over Streamable HTTP, for your team or your own cloud:

```bash
kyb-tool-mcp serve --http --port 8080      # MCP at /mcp, health at /healthz (localhost only;
                                           # add --host 0.0.0.0 to accept outside connections)
```

Each caller sends their own key in the `X-Companies-House-Key` header (or set `COMPANIES_HOUSE_API_KEY`
on the server to share one key). `GET /healthz` returns 503 when the sanctions index is missing or older
than 48 hours. Set `KYB_ALLOWED_HOSTS` to your public hostname(s) to reject other `Host` headers. The repo
includes a `Dockerfile` that builds the index into the image.

### Python

```python
import asyncio
from kyb_tool_mcp.pipeline import ReviewPipeline


async def main():
    pipe = ReviewPipeline()  # reads COMPANIES_HOUSE_API_KEY from the environment or .env; reuse it

    result = await pipe.review(
        {"case_id": "c1", "legal_name": "Tesco PLC", "registration_number": "00445790"}
    )
    print(result.decision, result.risk_level)  # e.g. APPROVE LOW
    for flag in result.flags:
        print(flag.severity, flag.code, flag.detail)  # each flag also lists its evidence ids

    record = await pipe.registry_lookup("GB", "00445790", None)
    site = await pipe.check_website("https://example.com", "retail", "Example Ltd")


asyncio.run(main())
```

`result.model_dump_json()` gives the whole result as JSON for storage: it is your audit trail.

## Build your own API

Wrap the library in your own web service, for example to call it from another app or a form.
TrustLedger does not ship a web server, so install one yourself (`pip install fastapi uvicorn`):

```python
# api.py  ->  run with: uvicorn api:app
from fastapi import FastAPI, Header, HTTPException
from kyb_tool_mcp.pipeline import ReviewPipeline

app = FastAPI(title="My KYB API")
pipe = ReviewPipeline()  # one pipeline for the whole app (shares the sanctions index and cache)
API_TOKEN = "change-me"  # your own token, so only your apps can call this


@app.post("/review")
async def review(application: dict, authorization: str = Header("")):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(401, "missing or wrong token")
    result = await pipe.review(application)
    return result.model_dump(mode="json")


@app.get("/screen")
async def screen(name: str, type: str = "person", birth_year: int | None = None):
    from kyb_tool_mcp.schemas.screening import ScreeningSubject

    return await pipe.screen(ScreeningSubject(name=name, type=type, birth_year=birth_year))
```

Before you expose it:

- Keep the Companies House key on the server only, never in a browser or app.
- Protect the endpoint (a token as above, or your usual auth) and rate-limit it: Companies House
  allows 600 requests per 5 minutes per key.
- Schedule `kyb-tool-mcp update-lists` daily so sanctions data stays current.
- Treat **ESCALATE** as "send to a person": the tool is decision support, not a sign-off.

### Add your own data sources

You can plug in another registry, sanctions list or screening service (for example a paid PEP
provider) without touching the decision rules. See [docs/providers.md](docs/providers.md).

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
- **Registry checks say `not_configured`:** the key is missing. In the Claude connector, add the
  `X-Companies-House-Key` request header. Locally, pass it as `COMPANIES_HOUSE_API_KEY` in the MCP config
  or a `.env` file.
- **Sanctions results look out of date:** run `uvx kyb-tool-mcp update-lists` again.

## Coming next

- One-click install for Claude Desktop (local version)
- A web form: check a company in the browser, no install
- The UK government's sanctions list alongside OFAC and the UN

## More

[Policy and flags](docs/policy.md) · [Data sources and licences](docs/providers.md) ·
[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE)

Contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
