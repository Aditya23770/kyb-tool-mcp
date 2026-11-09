# Contributing

Thanks for helping. kyb-tool-mcp is a **local MCP server**; please keep changes within that scope
(no hosted services or LLM SDK dependencies; those are on the roadmap).

## Setup

```bash
git clone https://github.com/Aditya23770/kyb-tool-mcp && cd kyb-tool-mcp
uv sync                                  # Python 3.11+
uv run pytest -q                         # offline: FakeWorld + mini sanctions fixtures
uv run kyb-tool-mcp update-lists         # optional: real OFAC + UN index for manual testing
```

## Before you open a PR

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
uv run pytest -q
uv run python -m benchmarks.run --gate   # must not breach targets or regress any metric
```

## Design rules (enforced in review)

1. Providers only fetch and map to schemas, with no business logic.
2. Only `policy.decide()` makes decisions.
3. Every flag cites evidence ids that exist (`validate_flags`).
4. A missing provider is `not_configured`; a failing one is `PROVIDER_ERROR`. Never pass silently.
5. Thresholds live in `src/kyb_tool_mcp/data/policy.default.yaml`. Bump its `version` when they change.
6. No network in tests.

## Improving matching quality (the loop)

1. Run `uv run python -m benchmarks.run` and open `benchmarks/RESULTS.md`.
2. Pick the highest-priority failure: false approvals, then missed listed subjects (B recall), then
   wrong decisions, then alert volume.
3. **Write a regression test first** (`tests/unit/test_matching_golden.py` or an integration case).
4. Fix it (usually in `matching/normalize.py`, `matching/features.py` or the policy YAML).
5. Re-run. Keep the change only if no metric gets worse; `--gate` enforces this.
6. Add an entry to `benchmarks/IMPROVEMENT_LOG.md`.
7. Tune on the `dev` split only. Check `--split holdout` occasionally; if dev improves while holdout
   drops, you are overfitting.

## Adding a provider

See [docs/providers.md](docs/providers.md).

## Licence

By contributing you agree your work is licensed under Apache-2.0. Do not commit third-party datasets;
commit the script that downloads them instead.
