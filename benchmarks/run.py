"""Benchmark runner + improvement-loop gate.

    uv run python -m benchmarks.run                       # C (dev) + B quick; compare with previous run
    uv run python -m benchmarks.run --split holdout       # report on holdout (do not tune on it)
    uv run python -m benchmarks.run --full                # all splits, larger B, A if data present
    uv run python -m benchmarks.run --gate                # exit 1 on target breach or regression

Writes benchmarks/results/latest-<label>.json, a timestamped copy in results/history/, and
benchmarks/RESULTS.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from benchmarks.baselines.naive import NaiveBaseline
from benchmarks.common import (
    DATA,
    ROOT,
    UK_FIRST,
    UK_LAST,
    check_targets,
    diff,
    mean,
    pct,
    previous_results,
    rng,
    save_results,
)
from kyb_tool_mcp.config import Settings, load_config
from kyb_tool_mcp.evidence import EvidenceStore  # noqa: F401  (documented dependency of the metric)
from kyb_tool_mcp.matching.adjudicate import adjudicate
from kyb_tool_mcp.matching.blocking import generate_candidates
from kyb_tool_mcp.matching.index import IndexBuilder, SanctionsIndex, entity_name_tokens
from kyb_tool_mcp.pipeline import ReviewPipeline
from kyb_tool_mcp.schemas.screening import HitOutcome, ListEntity, ScreeningSubject
from kyb_tool_mcp.testing import FakeWorld

TODAY = date(2026, 11, 1)
CACHE = ROOT / ".cache"
RANK = {"APPROVE": 0, "ESCALATE": 1, "REJECT": 2}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---- index ----------------------------------------------------------------------------------


def sample_index() -> Path:
    """Deterministic index from benchmarks/data/list_sample.json (rebuilt when the sample changes)."""
    src = DATA / "list_sample.json"
    target = CACHE / "sample_index.sqlite"
    if target.exists() and target.stat().st_mtime >= src.stat().st_mtime:
        return target
    data = json.loads(src.read_text(encoding="utf-8"))
    CACHE.mkdir(exist_ok=True)
    b = IndexBuilder(target)
    b.add(ListEntity.model_validate(e) for e in data["entities"])
    b.finish({"sample_of": data.get("index_built_at")})
    return target


def resolve_index(choice: str) -> Path:
    if choice == "sample":
        return sample_index()
    if choice == "full":
        p = Settings().index_path
        if not p.exists():
            raise SystemExit("full index missing: run `kyb-tool-mcp update-lists`")
        return p
    return Path(choice)


# ---- benchmark C ------------------------------------------------------------------------------


def load_cases(splits: list[str]) -> list[dict[str, Any]]:
    cases = []
    for s in splits:
        for p in sorted((ROOT / "c_cases" / s).glob("*.json")):
            c = json.loads(p.read_text(encoding="utf-8"))
            c["split"] = s
            cases.append(c)
    return cases


def planted_hit(planted: list[str], codes: set[str]) -> tuple[int, int]:
    hit = sum(any(alt in codes for alt in p.split("|")) for p in planted)
    return hit, len(planted)


async def run_c(cases: list[dict[str, Any]], index_path: Path, repeats: int) -> dict[str, Any]:
    settings = Settings(
        _env_file=None,
        companies_house_api_key="bench",
        kyb_audit=False,  # type: ignore[call-arg]
        kyb_http_cache=False,
        kyb_data_dir=CACHE,
    )
    config = load_config(settings)
    naive = NaiveBaseline(SanctionsIndex(index_path))
    per_case: list[dict[str, Any]] = []
    t_start = time.perf_counter()
    for n_done, c in enumerate(cases):
        if n_done and n_done % 10 == 0:
            el = time.perf_counter() - t_start
            log(
                f"  C {n_done * repeats}/{len(cases) * repeats} reviews  "
                f"({el:.0f}s, ~{el / n_done * (len(cases) - n_done):.0f}s left)"
            )
        world = FakeWorld.from_dict(c["world"])
        decisions: list[str] = []
        times: list[float] = []
        codes: set[str] = set()
        unsupported = 0
        for _ in range(repeats):
            pipe = ReviewPipeline(config, transport=world.transport(), index_path=index_path, today=TODAY)
            t0 = time.perf_counter()
            res = await pipe.review(c["application"])
            times.append((time.perf_counter() - t0) * 1000)
            decisions.append(res.decision)
            codes = {f.code for f in res.flags}
            unsupported = sum(1 for f in res.flags for e in f.evidence if e not in res.evidence)
        n_dec, n_flags = naive.decide(c["application"], world)
        got, total = planted_hit(c["planted"], codes)
        per_case.append(
            {
                "case_id": c["case_id"],
                "type": c["type"],
                "split": c["split"],
                "expected": c["expected"],
                "decision": decisions[0],
                "consistent": len(set(decisions)) == 1,
                "flags": sorted(codes),
                "planted_hit": got,
                "planted_total": total,
                "unsupported": unsupported,
                "ms": mean(times),
                "naive": n_dec,
                "naive_flags": n_flags,
            }
        )
    return summarise_c(per_case)


def _sys_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    correct = sum(r[key] == r["expected"] for r in rows)
    return {
        "cases": n,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "false_approvals": sum(r[key] == "APPROVE" and r["expected"] != "APPROVE" for r in rows),
        "false_rejects": sum(r[key] == "REJECT" and r["expected"] == "APPROVE" for r in rows),
        "under_decisions": sum(RANK[r[key]] < RANK[r["expected"]] for r in rows),
        "over_decisions": sum(RANK[r[key]] > RANK[r["expected"]] for r in rows),
    }


def summarise_c(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tool = _sys_metrics(rows, "decision")
    planted_total = sum(r["planted_total"] for r in rows)
    tool.update(
        {
            "consistency": round(sum(r["consistent"] for r in rows) / len(rows), 4) if rows else 0.0,
            "flag_recall": round(sum(r["planted_hit"] for r in rows) / planted_total, 4)
            if planted_total
            else 1.0,
            "unsupported_flags": sum(r["unsupported"] for r in rows),
            "p50_ms": pct([r["ms"] for r in rows], 50),
            "p95_ms": pct([r["ms"] for r in rows], 95),
        }
    )
    by_type: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = by_type.setdefault(r["type"], {"n": 0, "tool_ok": 0, "naive_ok": 0, "expected": r["expected"]})
        t["n"] += 1
        t["tool_ok"] += r["decision"] == r["expected"]
        t["naive_ok"] += r["naive"] == r["expected"]
    errors = [
        {k: r[k] for k in ("case_id", "split", "expected", "decision", "flags")}
        for r in rows
        if r["decision"] != r["expected"]
    ]
    return {
        "systems": {"kyb-tool-mcp": tool, "naive-fuzzy": _sys_metrics(rows, "naive")},
        "by_type": by_type,
        "errors": errors,
    }


# ---- benchmark B ------------------------------------------------------------------------------


def noisy_variants(ent: ListEntity, r: Any) -> list[tuple[str, str]]:
    toks = [t for t in ent.caption.split() if t]
    out = [("exact", ent.caption)]
    last = toks[-1]
    if len(last) > 4:
        i = r.randint(1, len(last) - 2)
        out.append(("typo", " ".join([*toks[:-1], last[:i] + last[i + 1] + last[i] + last[i + 2 :]])))
    out.append(("swapped", " ".join([toks[-1], *toks[:-1]])))
    out.append(("ch_format", f"{toks[-1].upper()}, {' '.join(toks[:-1])}"))
    if len(toks) >= 3:
        out.append(("dropped_middle", f"{toks[0]} {toks[-1]}"))
    out.append(("initial", f"{toks[0][0]}. {' '.join(toks[1:])}"))
    return out


def run_b(index_path: Path, n_pos: int, n_neg: int, seed: int = 7) -> dict[str, Any]:
    policy = load_config(Settings(_env_file=None)).policy.screening  # type: ignore[call-arg]
    idx = SanctionsIndex(index_path)
    r = rng(seed)
    persons = [
        e
        for e in idx.iter_entities()
        if e.is_person
        and e.birth_dates
        and all(ord(c) < 128 for c in e.caption)
        and 2 <= len(entity_name_tokens(e.caption, "Person")) <= 4
    ]
    r.shuffle(persons)
    persons = persons[:n_pos]
    lat: list[float] = []
    pos_total = cand_found = final_found = 0
    by_variant: dict[str, list[int]] = {}
    misses: list[dict[str, Any]] = []

    total_screens = sum(len(noisy_variants(e, rng(0))) for e in persons) + n_neg
    b_start = time.perf_counter()

    def screen(s: ScreeningSubject) -> list[tuple[str, HitOutcome]]:
        if lat and len(lat) % 200 == 0:
            el = time.perf_counter() - b_start
            log(
                f"  B {len(lat)}/~{total_screens} screens  ({el:.0f}s, ~{el / len(lat) * (total_screens - len(lat)):.0f}s left)"
            )
        t0 = time.perf_counter()
        cands = generate_candidates(idx, s, policy)
        out = [(c.entity.id, adjudicate(s, c, policy)[0]) for c in cands]
        lat.append((time.perf_counter() - t0) * 1000)
        return out

    for ent in persons:
        d = ent.birth_dates[0]
        for variant, name in noisy_variants(ent, r):
            dob_mode = r.choice(["year_month", "year", "none"])
            s = ScreeningSubject(
                name=name,
                birth_year=d.year if dob_mode != "none" else None,
                birth_month=d.month if dob_mode == "year_month" else None,
            )
            res = dict(screen(s))
            pos_total += 1
            found = ent.id in res
            final = found and res[ent.id] != HitOutcome.FALSE_POSITIVE
            cand_found += found
            final_found += final
            by_variant.setdefault(variant, [0, 0])
            by_variant[variant][0] += final
            by_variant[variant][1] += 1
            if not final and len(misses) < 25:
                misses.append(
                    {
                        "entity": ent.id,
                        "caption": ent.caption,
                        "query": name,
                        "variant": variant,
                        "dob": dob_mode,
                        "outcome": str(res.get(ent.id, "not a candidate")),
                    }
                )
    alerts = cleared = 0
    alert_examples: list[dict[str, Any]] = []
    for _ in range(n_neg):
        first = r.choice(UK_FIRST)
        middle = f" {r.choice(UK_FIRST)}" if r.random() < 0.3 else ""
        s = ScreeningSubject(
            name=f"{first}{middle} {r.choice(UK_LAST)}",
            birth_year=r.randint(1945, 2000),
            birth_month=r.randint(1, 12),
            nationality="GB",
        )
        res = screen(s)
        raised = [(e, o) for e, o in res if o != HitOutcome.FALSE_POSITIVE]
        alerts += bool(raised)
        cleared += sum(1 for _, o in res if o == HitOutcome.FALSE_POSITIVE)
        if raised and len(alert_examples) < 15:
            ent = idx.get_entity(raised[0][0])
            alert_examples.append(
                {
                    "query": s.name,
                    "dob": s.birth_year,
                    "hit": ent.caption if ent else raised[0][0],
                    "outcome": str(raised[0][1]),
                }
            )
    tool = {
        "positives": pos_total,
        "negatives": n_neg,
        "candidate_recall": round(cand_found / pos_total, 4) if pos_total else 0.0,
        "final_recall": round(final_found / pos_total, 4) if pos_total else 0.0,
        "alerts_per_1k": round(1000 * alerts / n_neg, 1) if n_neg else 0.0,
        "auto_clear_rate": round(cleared / (cleared + alerts), 4) if (cleared + alerts) else 1.0,
        "p50_ms": pct(lat, 50),
        "p95_ms": pct(lat, 95),
    }
    return {
        "index_entities": idx.count(),
        "systems": {"kyb-tool-mcp": tool},
        "recall_by_variant": {k: round(v[0] / v[1], 4) for k, v in sorted(by_variant.items())},
        "misses": misses,
        "alert_examples": alert_examples,
    }


# ---- benchmark A (optional data) ---------------------------------------------------------------


def run_a(n: int) -> dict[str, Any] | None:
    from benchmarks.a_matching.run import run as run_pairs

    return run_pairs(n)


# ---- report -----------------------------------------------------------------------------------


def write_report(
    results: dict[str, Any], changes: list[tuple[str, Any, Any, str]], breaches: list[str]
) -> None:
    L: list[str] = [
        "# Benchmark results",
        "",
        f"Generated by `python -m benchmarks.run` ({results['label']}, {results['created_at']}). "
        "Synthetic UK companies with real OFAC/UN sanctions subjects; see docs/testing.md.",
        "",
    ]
    L.append("**Targets:** " + ("all met ✅" if not breaches else "; ".join(f"❌ {b}" for b in breaches)))
    L.append("")
    c = results.get("c_cases")
    if c:
        L += [
            "## C. End-to-end KYB cases",
            "",
            f"Splits: {', '.join(results['splits'])}; {c['systems']['kyb-tool-mcp']['cases']} cases; "
            f"each run {results['repeats']}x.",
            "",
            "| System | Accuracy | False approvals | False rejects | Consistency | Planted-flag recall | Unsupported flags | p95 ms |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        t = c["systems"]["kyb-tool-mcp"]
        n = c["systems"]["naive-fuzzy"]
        L.append(
            f"| **kyb-tool-mcp** | {t['accuracy']:.1%} | **{t['false_approvals']}** | {t['false_rejects']} | "
            f"{t['consistency']:.0%} | {t['flag_recall']:.1%} | {t['unsupported_flags']} | {t['p95_ms']} |"
        )
        L.append(
            f"| naive fuzzy rules | {n['accuracy']:.1%} | {n['false_approvals']} | {n['false_rejects']} | "
            "100% | n/a | n/a | n/a |"
        )
        L += ["", "| Case type | Expected | n | kyb-tool-mcp | naive |", "| --- | --- | --- | --- | --- |"]
        for k, v in c["by_type"].items():
            L.append(
                f"| {k} | {v['expected']} | {v['n']} | {v['tool_ok']}/{v['n']} | {v['naive_ok']}/{v['n']} |"
            )
        if c["errors"]:
            L += ["", "Wrong decisions (kyb-tool-mcp):", ""]
            L += [
                f"- `{e['case_id']}` ({e['split']}): expected {e['expected']}, got {e['decision']}; flags "
                f"{', '.join(e['flags']) or 'none'}"
                for e in c["errors"][:40]
            ]
        L.append("")
    b = results.get("b_screening")
    if b:
        t = b["systems"]["kyb-tool-mcp"]
        L += [
            "## B. Screening at scale",
            "",
            f"Index: {b['index_entities']} list entities. {t['positives']} noisy queries for listed people "
            f"(typos, swapped order, Companies House format, dropped middle names, initials; DOB full/year/missing) "
            f"and {t['negatives']} clean UK decoys.",
            "",
            "| Candidate recall | Final recall | Alerts / 1,000 clean | Auto-clear rate | p50 ms | p95 ms |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| {t['candidate_recall']:.1%} | {t['final_recall']:.1%} | {t['alerts_per_1k']} | "
            f"{t['auto_clear_rate']:.1%} | {t['p50_ms']} | {t['p95_ms']} |",
            "",
            "Recall by query variant: "
            + ", ".join(f"{k} {v:.0%}" for k, v in b["recall_by_variant"].items()),
            "",
        ]
    a = results.get("a_matching")
    if a:
        L += [
            "## A. Name matching (OpenSanctions pairs)",
            "",
            "| System | Precision | Recall | F1 |",
            "| --- | --- | --- | --- |",
        ]
        for name, m in a["systems"].items():
            L.append(f"| {name} | {m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.1%} |")
        L.append("")
    else:
        L += [
            "## A. Name matching",
            "",
            "Not run: download the OpenSanctions pairs file (non-commercial) with "
            "`python -m benchmarks.a_matching.run --download`.",
            "",
        ]
    if changes:
        L += ["## Change vs previous run", "", "| Metric | Before | After | |", "| --- | --- | --- | --- |"]
        L += [f"| {m} | {b_} | {a_} | {v} |" for m, b_, a_, v in changes]
        L.append("")
    (ROOT / "RESULTS.md").write_text("\n".join(L), encoding="utf-8")


# ---- main ----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    ap.add_argument(
        "--full", action="store_true", help="all splits, 500 B positives, 1000 decoys, A if available"
    )
    ap.add_argument(
        "--index", default="sample", help="sample | full | <path> (benchmark C always uses sample)"
    )
    ap.add_argument(
        "--b-index", default="auto", help="auto (full if built, else sample) | sample | full | <path>"
    )
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--skip", default="", help="comma list of benchmarks to skip: a,b,c")
    ap.add_argument("--gate", action="store_true", help="exit 1 on target breach or metric regression")
    ap.add_argument("--label", default=None)
    a = ap.parse_args(argv)

    splits = ["dev", "holdout"] if (a.full or a.split == "all") else [a.split]
    label = a.label or ("full" if a.full else a.split)
    skip = set(a.skip.split(",")) - {""}
    results: dict[str, Any] = {"splits": splits, "repeats": a.repeats}

    if "c" not in skip:
        idx = resolve_index(a.index)
        cases = load_cases(splits)
        log(f"C: {len(cases)} cases x {a.repeats} runs ...")
        results["c_cases"] = asyncio.run(run_c(cases, idx, a.repeats))
    if "b" not in skip:
        b_choice = a.b_index
        if b_choice == "auto":
            b_choice = "full" if Settings().index_path.exists() else "sample"
        n_pos, n_neg = (500, 1000) if a.full else (150, 1000)
        log(f"B: {n_pos} listed people (noisy variants) + {n_neg} decoys on {b_choice} index ...")
        results["b_screening"] = run_b(resolve_index(b_choice), n_pos, n_neg)
        results["b_screening"]["index"] = b_choice
    if "a" not in skip and a.full:
        a_res = run_a(3000)
        if a_res:
            results["a_matching"] = a_res

    prev = previous_results(label)
    changes = diff(prev, results)
    breaches = check_targets(results)
    path = save_results(results, label)
    results = json.loads(path.read_text(encoding="utf-8"))
    write_report(results, changes, breaches)

    t = results.get("c_cases", {}).get("systems", {}).get("kyb-tool-mcp")
    if t:
        log(
            f"C  accuracy {t['accuracy']:.1%}  false approvals {t['false_approvals']}  "
            f"consistency {t['consistency']:.0%}  flag recall {t['flag_recall']:.1%}"
        )
        for e in results["c_cases"]["errors"][:20]:
            log(f"   x {e['case_id']}: expected {e['expected']} got {e['decision']} {e['flags']}")
    b = results.get("b_screening", {}).get("systems", {}).get("kyb-tool-mcp")
    if b:
        log(
            f"B  recall {b['final_recall']:.1%} (candidates {b['candidate_recall']:.1%})  "
            f"alerts/1k {b['alerts_per_1k']}  p95 {b['p95_ms']}ms"
        )
    for m, before, after, verdict in changes:
        log(f"   {verdict:7} {m}: {before} -> {after}")
    log("targets: " + ("ALL MET" if not breaches else " | ".join(breaches)))
    regressions = [c for c in changes if c[3] == "WORSE"]
    if a.gate and (breaches or regressions):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
