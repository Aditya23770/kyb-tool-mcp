# CLAUDE.md

Project: **kyb-tool-mcp**, an open-source KYB screening and case-review engine that runs **locally as an MCP server** (plus a small CLI and a Python library). Read `PLAN.md` before large tasks.

## Current scope
Local MCP only. Do not add LLM SDKs, web servers, hosted APIs or other non-local-MCP dependencies; those are future work (see the README roadmap).

## Rules
- Python 3.11+, uv, pydantic v2, async httpx. Type everything; `mypy --strict` must pass.
- Providers (`src/kyb_tool_mcp/providers/`) only fetch and map to schemas. No business logic.
- Decisions come only from `policy.py` (`decide()`). Nothing else sets or changes a decision.
- Every Flag must cite evidence ids that exist in the `EvidenceStore` (`validate_flags`).
- Missing provider for a check => status `not_configured` + `CHECK_NOT_CONFIGURED`, never a silent pass.
- Provider errors => `PROVIDER_ERROR` (high), never swallowed (fail closed).
- Thresholds live in `src/kyb_tool_mcp/data/policy.default.yaml`, never in code. Bump `version` when changing them.
- Never print to stdout from server code (stdout is the MCP stdio transport). Log to stderr.
- No network in tests: use `kyb_tool_mcp.testing.FakeWorld` (HTTP) and `tests/fixtures/sanctions_mini` (lists).
- Never commit API keys, `.env`, raw third-party datasets, or `*.sqlite`.

## Commands
- `uv sync`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
- `uv run pytest -q`
- `uv run kyb-tool-mcp update-lists` (builds the real OFAC + UN index in `~/.kyb-tool-mcp/`)
- `uv run kyb-tool-mcp review benchmarks/c_cases/dev/<case>.json`
- `uv run python -m benchmarks.run --split dev` (improvement loop; see docs/testing.md)

## Improvement loop
Run the benchmark, fix the highest-priority failure (false approvals, then missed sanctioned
subjects, then wrong decisions, then alert volume), add a regression test first, re-run, keep the
change only if no metric regresses, log it in `benchmarks/IMPROVEMENT_LOG.md`.

## Done means
Tests added and passing; ruff, format and mypy clean; benchmark targets in `benchmarks/targets.yaml` still met.
