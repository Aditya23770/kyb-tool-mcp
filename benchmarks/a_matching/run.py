"""Benchmark A: pairwise name matching on the OpenSanctions matcher training pairs.

Data: https://data.opensanctions.org/contrib/training/pairs.json (CC BY-NC, non-commercial; large).
Each line: {"judgement": "positive"|"negative", "left": <FtM entity>, "right": <FtM entity>}.
The raw file is never committed; only the sampled ids (fixed seed) are.

    uv run python -m benchmarks.a_matching.run --download   # one-off
    uv run python -m benchmarks.run --full                   # includes A when the file exists
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import httpx
from rapidfuzz import fuzz

from kyb_tool_mcp.config import load_policy
from kyb_tool_mcp.matching.adjudicate import adjudicate
from kyb_tool_mcp.matching.blocking import score_entity
from kyb_tool_mcp.matching.normalize import fold, parse_partial_dates, tokens_for
from kyb_tool_mcp.schemas.screening import HitOutcome, ListEntity, ScreeningSubject

HERE = Path(__file__).parent
RAW = HERE / "raw" / "pairs.json"
URL = "https://data.opensanctions.org/contrib/training/pairs.json"
SEED = 13


def _subject(e: dict[str, Any]) -> ScreeningSubject | None:
    props = e.get("properties", {})
    names = props.get("name") or []
    if not names:
        return None
    kind = "person" if e.get("schema") == "Person" else "company"
    dob = parse_partial_dates((props.get("birthDate") or [None])[0])
    return ScreeningSubject(
        name=names[0],
        type=kind,
        birth_year=dob[0].year if dob else None,
        birth_month=dob[0].month if dob else None,
        nationality=(props.get("nationality") or [None])[0],
    )


def _entity(e: dict[str, Any]) -> ListEntity | None:
    props = e.get("properties", {})
    names = (props.get("name") or []) + (props.get("alias") or [])
    if not names:
        return None
    dobs = [d for raw in props.get("birthDate", []) for d in parse_partial_dates(raw)]
    return ListEntity(
        id=str(e.get("id")),
        source="pairs",
        schema=e.get("schema", "LegalEntity"),
        caption=names[0],
        names=names,
        weak_names=props.get("weakAlias", []),
        birth_dates=dobs,
        nationalities=props.get("nationality", []),
    )


def sample(n: int) -> list[dict[str, Any]]:
    r = random.Random(SEED)
    pools: dict[tuple[str, bool, bool], list[dict[str, Any]]] = {}
    with open(RAW, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i > 400_000:
                break
            row = json.loads(line)
            left, right = row.get("left", {}), row.get("right", {})
            if left.get("schema") not in {"Person", "Company", "Organization", "LegalEntity"}:
                continue
            ln = (left.get("properties", {}).get("name") or [""])[0]
            rn = (right.get("properties", {}).get("name") or [""])[0]
            cross = all(ord(c) < 128 for c in ln) != all(ord(c) < 128 for c in rn)
            key = (left["schema"] == "Person", cross, row.get("judgement") == "positive")
            pools.setdefault(key, []).append(row)
    per = max(1, n // max(1, len(pools)))
    out: list[dict[str, Any]] = []
    for rows in pools.values():
        r.shuffle(rows)
        out.extend(rows[:per])
    return out


def metrics(pred: list[bool], gold: list[bool]) -> dict[str, float]:
    tp = sum(p and g for p, g in zip(pred, gold, strict=True))
    fp = sum(p and not g for p, g in zip(pred, gold, strict=True))
    fn = sum(g and not p for p, g in zip(pred, gold, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "n": len(gold)}


def run(n: int = 3000) -> dict[str, Any] | None:
    if not RAW.exists():
        return None
    policy = load_policy().screening
    rows = sample(n)
    (HERE / "sampled_ids.json").write_text(
        json.dumps([[r["left"].get("id"), r["right"].get("id")] for r in rows]), encoding="utf-8"
    )
    gold: list[bool] = []
    ours: list[bool] = []
    naive: list[bool] = []
    for row in rows:
        s, e = _subject(row["left"]), _entity(row["right"])
        if not s or not e:
            continue
        gold.append(row["judgement"] == "positive")
        cand = score_entity(None, s, tokens_for(s.name, s.type), e)
        ours.append(
            bool(cand)
            and cand is not None
            and cand.name_score >= policy.candidate_min
            and adjudicate(s, cand, policy)[0] != HitOutcome.FALSE_POSITIVE
        )
        naive.append(max(fuzz.token_sort_ratio(fold(s.name), fold(nm)) for nm in e.names) >= 85)
    return {"systems": {"kyb-tool-mcp": metrics(ours, gold), "rapidfuzz>=85": metrics(naive, gold)}}


def download() -> None:
    RAW.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", URL, follow_redirects=True, timeout=600) as r, open(RAW, "wb") as fh:
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            fh.write(chunk)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("-n", type=int, default=3000)
    args = ap.parse_args()
    if args.download:
        download()
    print(json.dumps(run(args.n), indent=2))
