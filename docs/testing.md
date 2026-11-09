# Testing and the improvement loop

## Test layers

| Layer | Where | Network |
| --- | --- | --- |
| Unit: normalisation, countries, policy `decide()`, evidence validator, loaders, provider mapping | `tests/unit/` | none |
| Property (hypothesis): critical never approves; adding a flag never softens a decision; deterministic; `fold` idempotent | `tests/unit/test_policy.py`, `test_normalize.py` | none |
| Golden matching: transliteration, aliases, weak aliases, partial / range DOB, schema mismatch, namesakes | `tests/unit/test_matching_golden.py` | none (mini OFAC/UN XML fixtures) |
| End-to-end: every registry, ownership, screening and website flag; fail-closed errors; determinism; audit | `tests/integration/` | none (`FakeWorld`) |
| MCP: in-process client lists tools and resources and calls every tool | `tests/test_mcp.py` | none |
| CLI: every command and exit code | `tests/test_cli.py` | none |
| Packaging: build the wheel, install it clean, start the stdio server, list tools | CI `package` job | none |
| Benchmarks A/B/C | `benchmarks/` | none by default (committed list sample) |

```bash
uv run pytest -q                 # everything offline, ~15 s
uv run pytest -q --cov           # coverage gate in CI: 85%
```

### FakeWorld

`kyb_tool_mcp.testing.FakeWorld` serves Companies House, GLEIF, RDAP and website responses through
`httpx.MockTransport`, using the real API response shapes:

```python
world = FakeWorld()
world.add_company(
    FakeCompany(number="01234567", name="ACME LIMITED", officers=[FakePerson("DOE, Jane", 1980)])
)
world.add_website(FakeWebsite(url="https://acme.co.uk", html=business_html("Acme", "We sell widgets")))
result = await ReviewPipeline(
    config, transport=world.transport(), index_path=..., today=date(2026, 11, 1)
).review(app)
```

## Manual testing against the real MCP client

```bash
uv run kyb-tool-mcp update-lists                                   # real OFAC + UN index
npx @modelcontextprotocol/inspector uv run kyb-tool-mcp            # MCP Inspector UI
claude mcp add kyb-tool-mcp -e COMPANIES_HOUSE_API_KEY=... -- uv --directory /path/to/kyb-tool-mcp run kyb-tool-mcp
```

## Benchmarks

| | What | Data |
| --- | --- | --- |
| **A** | Pairwise name matching vs plain rapidfuzz | OpenSanctions pairs (non-commercial, optional download) |
| **B** | Screening at scale: noisy queries for real listed people + 1,000 clean UK decoys | Real OFAC/UN index |
| **C** | End-to-end KYB decisions on 168 cases (14 types) vs a naive fuzzy-rules baseline | Synthetic UK companies (`FakeWorld`) with **real** OFAC/UN subjects planted |

Benchmark C cases are built by `python -m benchmarks.c_cases.build`. Expected decisions are assigned when
each case is constructed, never by running the tool. The cases are split 70/30 into `dev` (tune on this)
and `holdout` (report only). `benchmarks/data/list_sample.json` holds every planted list entry plus
4,000 background entries, so C runs identically offline and in CI.

```bash
uv run python -m benchmarks.run                    # C dev + B quick, diff vs previous run
uv run python -m benchmarks.run --split holdout    # check for overfitting
uv run python -m benchmarks.run --full             # everything (+ A if the pairs file exists)
uv run python -m benchmarks.run --gate             # exit 1 on target breach or any regression
```

The targets are in `benchmarks/targets.yaml`. The headline is **0 false approvals** on C.

## The improvement loop

```
run benchmarks ─► rank failures ─► regression test first ─► fix ─► re-run ─┐
      ▲                                                                     │
      └──────── keep only if no metric is worse (else revert) ◄────────────┘
```

1. `python -m benchmarks.run`
2. Rank failures in this order: false approvals, missed listed subjects (B recall), wrong decisions,
   inconsistency, alert volume, latency.
3. Take the top cluster and find its root cause (a normaliser gap, a blocking miss, a rule, a threshold).
4. Write a failing test, then fix. Threshold changes go only in the policy YAML, with a version bump.
5. Re-run with `--gate`. Revert if any metric got worse.
6. Log it in `benchmarks/IMPROVEMENT_LOG.md`.
7. Every few iterations run `--split holdout`. If dev improves while holdout drops, roll back the
   threshold changes.

The nightly workflow (`.github/workflows/bench.yml`) rebuilds the index from **fresh** lists, runs
`--full --gate` and opens an issue when a target breaks. This catches list format changes and data
drift, and those issues seed the next loop session.
