"""Shared benchmark helpers: deterministic generators, metrics, results history and targets."""

from __future__ import annotations

import json
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"
DATA = ROOT / "data"

UK_FIRST = (
    "Oliver George Harry Jack Jacob Noah Charlie Thomas Oscar William James Henry Leo Alfie Joshua Freddie "
    "Archie Ethan Isaac Alexander Joseph Edward Samuel Max Daniel Arthur Lucas Mohammed Logan Theo Harrison "
    "Olivia Amelia Isla Ava Emily Sophia Grace Mia Poppy Ella Lily Evie Isabella Sophie Ivy Freya Harper "
    "Willow Charlotte Jessica Rosie Daisy Alice Florence Phoebe Sienna Matilda Elsie Ruby Evelyn Priya Aisha "
    "Chloe Hannah Rebecca Sarah Laura Emma Rachel Claire Helen Fiona Gareth Rhys Owen Callum Fraser Ewan "
    "Siobhan Niamh Aoife Ciaran Declan Tariq Imran Anjali Ravi Sanjay Wei Chen Ade Chidi Kwame Tomasz Piotr"
).split()
UK_LAST = (
    "Smith Jones Williams Taylor Brown Davies Evans Wilson Thomas Johnson Roberts Robinson Thompson Wright "
    "Walker White Edwards Hughes Green Hall Lewis Harris Clarke Patel Jackson Wood Turner Martin Cooper Hill "
    "Ward Morris Moore Clark Lee King Baker Harrison Morgan Allen James Scott Phillips Watson Davis Parker "
    "Price Bennett Young Griffiths Mitchell Kelly Cook Carter Richardson Bailey Collins Bell Shaw Murphy Miller "
    "Cox Richards Khan Marshall Anderson Simpson Ellis Adams Singh Begum Wilkinson Foster Chapman Powell Webb "
    "Rogers Gray Mason Ali Hunt Hussain Campbell Matthews Owen Palmer Holmes Mills Barnes Knight Lloyd Butler "
    "Russell Barker Fisher Stevens Jenkins Murray Dixon Harvey Okafor Nowak Kowalski Mensah Osei Chowdhury"
).split()
CO_WORDS = (
    "Brightwater Northgate Silverline Oakfield Redbridge Kestrel Meridian Harbour Ashford Lakeside Summit "
    "Blackthorn Riverside Granite Clearview Highland Westbrook Evergreen Copperleaf Ironbridge Bluebell "
    "Stonegate Fairway Larkspur Thornbury Wexford Pennine Cobalt Driftwood Marlow Beacon Juniper Halcyon "
    "Saxon Orchard Falcon Cedar Willowmere Foxglove Linden Arden Calder Tamar Severn Wren Hawthorn"
).split()
INDUSTRIES: list[tuple[str, str, str, str]] = [
    # (SIC code, declared industry, website description, domain word)
    (
        "62012",
        "business software development",
        "We design and develop business software and cloud applications for clients.",
        "software",
    ),
    (
        "46510",
        "computer and electronics wholesale",
        "Wholesale supplier of computers, peripheral equipment and software to resellers.",
        "electronics",
    ),
    (
        "56101",
        "licensed restaurant",
        "Our restaurant serves seasonal food, wine and craft beer. Book a table for lunch or dinner.",
        "kitchen",
    ),
    (
        "47710",
        "retail sale of clothing",
        "Shop our clothing collection: dresses, coats, knitwear and accessories delivered UK-wide.",
        "clothing",
    ),
    (
        "41201",
        "construction of commercial buildings",
        "Commercial building contractor delivering offices, warehouses and construction projects.",
        "build",
    ),
    (
        "69201",
        "accounting and bookkeeping",
        "Chartered accountants offering bookkeeping, payroll, tax returns and audit services.",
        "accounts",
    ),
    (
        "49410",
        "freight transport by road",
        "Road haulage and freight transport across the UK with a modern fleet of lorries.",
        "freight",
    ),
    (
        "86230",
        "dental practice",
        "Friendly dental practice offering check-ups, hygienist appointments and dental implants.",
        "dental",
    ),
    (
        "73110",
        "advertising agency",
        "Creative advertising agency: campaigns, branding, media buying and social advertising.",
        "media",
    ),
    (
        "47910",
        "online retail of homeware",
        "Online store for homeware, kitchenware, cushions and furniture with free delivery.",
        "home",
    ),
]
MISMATCH_SITE = (
    "Fresh flowers, bouquets and wedding floristry delivered same day. Order roses and lilies online."
)


def rng(seed: int) -> random.Random:
    return random.Random(seed)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = min(len(v) - 1, max(0, round(q / 100 * (len(v) - 1))))
    return round(v[k], 2)


def mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def load_targets() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / "targets.yaml").read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def check_targets(results: dict[str, Any]) -> list[str]:
    """Return human-readable target breaches (empty list = all targets met)."""
    t = load_targets()
    breaches: list[str] = []
    c = results.get("c_cases", {}).get("systems", {}).get("kyb-tool-mcp")
    if c:
        tc = t.get("c_cases", {})
        if c["false_approvals"] > tc.get("false_approvals", 0):
            breaches.append(f"C false approvals {c['false_approvals']} > {tc.get('false_approvals', 0)}")
        if c["accuracy"] < tc.get("accuracy_min", 0):
            breaches.append(f"C accuracy {c['accuracy']:.3f} < {tc['accuracy_min']}")
        if c["consistency"] < tc.get("consistency_min", 0):
            breaches.append(f"C consistency {c['consistency']:.3f} < {tc['consistency_min']}")
        if c["unsupported_flags"] > tc.get("unsupported_flags", 0):
            breaches.append(f"C unsupported flags {c['unsupported_flags']}")
    b = results.get("b_screening", {}).get("systems", {}).get("kyb-tool-mcp")
    if b:
        tb = t.get("b_screening", {})
        if b["final_recall"] < tb.get("recall_min", 0):
            breaches.append(f"B recall {b['final_recall']:.3f} < {tb['recall_min']}")
        if b["alerts_per_1k"] > tb.get("alerts_per_1k_max", 1e9):
            breaches.append(f"B alerts/1k {b['alerts_per_1k']} > {tb['alerts_per_1k_max']}")
        if b["p95_ms"] > tb.get("p95_ms_max", 1e9):
            breaches.append(f"B p95 {b['p95_ms']}ms > {tb['p95_ms_max']}ms")
    a = results.get("a_matching", {}).get("systems", {}).get("kyb-tool-mcp")
    if a and a.get("f1") is not None and a["f1"] < t.get("a_matching", {}).get("f1_min", 0):
        breaches.append(f"A F1 {a['f1']:.3f} < {t['a_matching']['f1_min']}")
    return breaches


def save_results(results: dict[str, Any], label: str) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "history").mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results = {"label": label, "created_at": stamp, **results}
    text = json.dumps(results, indent=2, sort_keys=True)
    (RESULTS / f"latest-{label}.json").write_text(text, encoding="utf-8")
    (RESULTS / "history" / f"{stamp}-{label}.json").write_text(text, encoding="utf-8")
    return RESULTS / f"latest-{label}.json"


def previous_results(label: str) -> dict[str, Any] | None:
    p = RESULTS / f"latest-{label}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


HIGHER_IS_BETTER = {
    "accuracy",
    "consistency",
    "final_recall",
    "candidate_recall",
    "auto_clear_rate",
    "f1",
    "precision",
    "recall",
    "flag_recall",
}
LOWER_IS_BETTER = {
    "false_approvals",
    "alerts_per_1k",
    "p95_ms",
    "p50_ms",
    "unsupported_flags",
    "false_rejects",
}


def diff(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[tuple[str, Any, Any, str]]:
    """(metric, before, after, verdict) for the kyb-tool-mcp system across benchmarks."""
    if not prev:
        return []
    rows: list[tuple[str, Any, Any, str]] = []
    for bench in ("a_matching", "b_screening", "c_cases"):
        before = prev.get(bench, {}).get("systems", {}).get("kyb-tool-mcp", {})
        after = cur.get(bench, {}).get("systems", {}).get("kyb-tool-mcp", {})
        for k, v in after.items():
            if not isinstance(v, (int, float)) or k not in before or before[k] == v:
                continue
            better = (
                (v > before[k])
                if k in HIGHER_IS_BETTER
                else (v < before[k])
                if k in LOWER_IS_BETTER
                else None
            )
            verdict = "better" if better else "WORSE" if better is False else "changed"
            if k in {"p50_ms", "p95_ms"} and abs(v - before[k]) < max(5, 0.25 * before[k]):
                continue  # latency noise
            rows.append((f"{bench}.{k}", before[k], v, verdict))
    return rows
