## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] Tests added (regression test first for bug fixes / matching changes)
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy src` clean
- [ ] `uv run pytest -q` green
- [ ] `uv run python -m benchmarks.run --gate` passes; paste the "Change vs previous run" table below if any metric moved
- [ ] Thresholds changed only in `policy.default.yaml`, with a `version` bump
- [ ] No API keys, `.env`, raw third-party datasets or `*.sqlite` committed

## Benchmark diff

<!-- paste from benchmarks/RESULTS.md -->
