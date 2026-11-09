"""Benchmark D: out-of-distribution (OOD) evaluation on real data the tool was never tuned on.

REPORT-ONLY: never tune thresholds on this data, or it stops being out-of-distribution.

D1  Cross-list recall. The UK Sanctions List (FCDO) spells names the UK way. Rows that carry a
    "UN Reference Number" are linked, by the UK government, to a UN consolidated-list entry we index.
    Query our index with the UK spelling (+ UK DOB) and check the linked UN entry is caught.
    Ground truth = the official UN reference link, independent of our matcher.
D2  False alerts on REAL UK people: individual owners (PSCs) from the Companies House PSC snapshot.
D3  False alerts on REAL UK company names: Companies House "Basic Company Data" (live companies).

Raw files (not committed, see .gitignore) in benchmarks/d_ood/raw/:
  uk_sanctions.csv  https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv
  basic1.zip        https://download.companieshouse.gov.uk/BasicCompanyData-<date>-part1_7.zip
  psc1.zip          https://download.companieshouse.gov.uk/psc-snapshot-<date>_1of33.zip

    uv run python -m benchmarks.d_ood.run
"""

from __future__ import annotations

import csv
import io
import json
import random
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from kyb_tool_mcp.config import Settings, load_policy
from kyb_tool_mcp.matching.adjudicate import adjudicate
from kyb_tool_mcp.matching.blocking import generate_candidates
from kyb_tool_mcp.matching.index import SanctionsIndex
from kyb_tool_mcp.matching.normalize import parse_partial_dates
from kyb_tool_mcp.schemas.screening import HitOutcome, ScreeningSubject

HERE = Path(__file__).parent
RAW = HERE / "raw"
SEED = 2026
N_PEOPLE = 5000
N_COMPANIES = 5000


def log(m: str) -> None:
    print(m, file=sys.stderr, flush=True)


class Screener:
    def __init__(self, index: SanctionsIndex) -> None:
        self.idx = index
        self.policy = load_policy().screening
        self.lat: list[float] = []

    def __call__(self, s: ScreeningSubject) -> list[tuple[Any, HitOutcome, list[str]]]:
        t0 = time.perf_counter()
        out = []
        for c in generate_candidates(self.idx, s, self.policy):
            o, reasons = adjudicate(s, c, self.policy)
            out.append((c, o, reasons))
        self.lat.append((time.perf_counter() - t0) * 1000)
        return out


# ---- D1: cross-list recall ------------------------------------------------------------------------


def uk_rows() -> list[dict[str, str]]:
    text = (RAW / "uk_sanctions.csv").read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Last Updated,"))
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def uk_name(r: dict[str, str]) -> str:
    # UKSL: "Name 1".."Name 5" = given names, "Name 6" = family name (entities: full name in Name 6).
    parts = [r.get(f"Name {i}", "").strip() for i in range(1, 6)]
    return " ".join(p for p in [*parts, r.get("Name 6", "").strip()] if p)


def d1(screen: Screener) -> dict[str, Any]:
    idx = screen.idx
    un_by_ref: dict[str, str] = {}
    for ent in idx.iter_entities(source="un_consolidated"):
        for p in ent.programs:
            if "." in p:
                un_by_ref[p.strip().upper()] = ent.id
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in uk_rows():
        ref = (r.get("UN Reference Number") or "").strip().upper()
        if ref and ref in un_by_ref:
            groups[r["Unique ID"]].append(r)
    results: list[dict[str, Any]] = []
    for uid, rows in sorted(groups.items()):
        ref = rows[0]["UN Reference Number"].strip().upper()
        target = un_by_ref[ref]
        kind = "person" if rows[0].get("Designation Type", "").lower().startswith("individual") else "company"
        dobs = parse_partial_dates((rows[0].get("D.O.B") or "").replace("dd/", "").replace("mm/", ""))
        dob = next((d for d in dobs if 1900 < d.year < 2015), None)
        primary = [r for r in rows if r.get("Name type", "").lower().startswith("primary")] or rows[:1]
        aliases = [r for r in rows if r not in primary and (r.get("Alias strength") or "").lower() != "weak"]
        for variant, r in [("primary", primary[0])] + [("alias", a) for a in aliases[:2]]:
            name = uk_name(r)
            if not name:
                continue
            s = ScreeningSubject(
                name=name,
                type=kind,
                birth_year=dob.year if dob else None,
                birth_month=dob.month if dob else None,
            )
            res = {c.entity.id: (o, c.name_score) for c, o, _ in screen(s)}
            got = res.get(target)
            results.append(
                {
                    "uk_id": uid,
                    "un_ref": ref,
                    "kind": kind,
                    "variant": variant,
                    "query": name,
                    "dob": str(dob) if dob else None,
                    "target": target,
                    "outcome": got[0].value if got else "not_a_candidate",
                    "name_score": got[1] if got else None,
                }
            )

    def recall(rows: list[dict[str, Any]]) -> float:
        caught = sum(r["outcome"] in ("TRUE_MATCH", "NEEDS_REVIEW") for r in rows)
        return round(caught / len(rows), 4) if rows else 0.0

    prim = [r for r in results if r["variant"] == "primary"]
    return {
        "linked_designations": len(groups),
        "queries": len(results),
        "recall_primary": recall(prim),
        "recall_aliases": recall([r for r in results if r["variant"] == "alias"]),
        "recall_people": recall([r for r in prim if r["kind"] == "person"]),
        "recall_entities": recall([r for r in prim if r["kind"] == "company"]),
        "auto_confirmed_primary": round(
            sum(r["outcome"] == "TRUE_MATCH" for r in prim) / max(len(prim), 1), 4
        ),
        "misses": [r for r in results if r["outcome"] not in ("TRUE_MATCH", "NEEDS_REVIEW")],
    }


# ---- D2 / D3: false alerts on real UK people and companies ----------------------------------------


def sample_pscs(n: int, file: str = "psc1.zip") -> list[ScreeningSubject]:
    r = random.Random(SEED)
    pool: list[ScreeningSubject] = []
    with zipfile.ZipFile(RAW / file) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            for i, line in enumerate(io.TextIOWrapper(fh, encoding="utf-8")):
                if i > 300_000:
                    break
                try:
                    d = json.loads(line).get("data", {})
                except json.JSONDecodeError:
                    continue
                if d.get("kind") != "individual-person-with-significant-control" or d.get("ceased_on"):
                    continue
                dob = d.get("date_of_birth") or {}
                ne = d.get("name_elements") or {}
                full = " ".join(
                    x for x in (ne.get("forename"), ne.get("middle_name"), ne.get("surname")) if x
                )
                pool.append(
                    ScreeningSubject(
                        name=full or d.get("name", ""),
                        birth_year=dob.get("year"),
                        birth_month=dob.get("month"),
                        nationality=d.get("nationality"),
                    )
                )
    r.shuffle(pool)
    return [p for p in pool if p.name.strip()][:n]


def sample_companies(n: int, file: str = "basic1.zip") -> list[ScreeningSubject]:
    r = random.Random(SEED)
    with zipfile.ZipFile(RAW / file) as z, z.open(z.namelist()[0]) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
        names = [
            row.get("CompanyName") or row.get(" CompanyName") or ""
            for _, row in zip(range(300_000), reader, strict=False)
        ]
    r.shuffle(names)
    return [ScreeningSubject(name=x, type="company") for x in names if x.strip()][:n]


def false_alerts(screen: Screener, subjects: list[ScreeningSubject]) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    for i, s in enumerate(subjects):
        if i and i % 1000 == 0:
            log(f"    {i}/{len(subjects)}")
        raised = [(c, o, rs) for c, o, rs in screen(s) if o != HitOutcome.FALSE_POSITIVE]
        if raised:
            c, o, _ = max(raised, key=lambda x: x[0].score)
            alerts.append(
                {
                    "query": s.name,
                    "dob": s.birth_year,
                    "nationality": s.nationality,
                    "hit": c.entity.caption,
                    "matched": c.matched_name,
                    "outcome": o.value,
                    "name_score": c.name_score,
                    "source": c.entity.source,
                    "confirmed": any(o2 == HitOutcome.TRUE_MATCH for _, o2, _ in raised),
                }
            )
    return {
        "screened": len(subjects),
        "alerts": len(alerts),
        "alerts_per_1k": round(1000 * len(alerts) / max(len(subjects), 1), 1),
        "auto_confirmed": sum(a["confirmed"] for a in alerts),
        "examples": alerts,
    }


def pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return round(s[min(len(s) - 1, int(q / 100 * len(s)))], 1) if s else 0.0


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--people", default="psc1.zip")
    ap.add_argument("--companies", default="basic1.zip")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--skip-d1", action="store_true")
    a = ap.parse_args()
    idx = SanctionsIndex(Settings().index_path)
    screen = Screener(idx)
    out: dict[str, Any] = {"index": idx.meta, "policy_version": load_policy().version}
    if (RAW / "uk_sanctions.csv").exists() and not a.skip_d1:
        log("D1: UK Sanctions List rows linked to UN entries ...")
        out["d1_cross_list"] = d1(screen)
    if (RAW / a.people).exists():
        log(f"D2: {N_PEOPLE} real UK PSCs ...")
        out["d2_uk_people"] = false_alerts(screen, sample_pscs(N_PEOPLE, a.people))
    if (RAW / a.companies).exists():
        log(f"D3: {N_COMPANIES} real UK company names ...")
        out["d3_uk_companies"] = false_alerts(screen, sample_companies(N_COMPANIES, a.companies))
    out["latency_p50_ms"] = pct(screen.lat, 50)
    out["latency_p95_ms"] = pct(screen.lat, 95)
    out["files"] = {"people": a.people, "companies": a.companies}
    (HERE / a.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    d1r = out.get("d1_cross_list", {})
    log(
        f"D1 linked={d1r.get('linked_designations')} recall primary={d1r.get('recall_primary')} "
        f"aliases={d1r.get('recall_aliases')} people={d1r.get('recall_people')} "
        f"entities={d1r.get('recall_entities')} misses={len(d1r.get('misses', []))}"
    )
    for k in ("d2_uk_people", "d3_uk_companies"):
        if k in out:
            log(
                f"{k}: {out[k]['alerts']}/{out[k]['screened']} alerts ({out[k]['alerts_per_1k']}/1k), "
                f"auto-confirmed {out[k]['auto_confirmed']}"
            )
    log(f"latency p50 {out['latency_p50_ms']}ms p95 {out['latency_p95_ms']}ms")


if __name__ == "__main__":
    main()
