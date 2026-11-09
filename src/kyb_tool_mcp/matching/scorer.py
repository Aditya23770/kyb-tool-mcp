"""Candidate scoring. v1 = transparent weighted rules; optional calibrated logistic model.

The rule score is a 0-100 number on the same scale as the name score so policy thresholds
stay interpretable. The calibrated model (if weights are supplied) returns P(match).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

DOB_ADJ = {
    "year_month": 5.0,
    "year": 3.0,
    "month_conflict": -8.0,
    "near": -4.0,
    "conflict": -30.0,
    "unknown": 0.0,
}
NAT_ADJ = {"match": 2.0, "conflict": -8.0, "unknown": 0.0}


def rule_score(f: dict[str, Any]) -> float:
    s = float(f.get("name_score", 0.0))
    s += DOB_ADJ.get(str(f.get("dob", "unknown")), 0.0)
    s += NAT_ADJ.get(str(f.get("nationality", "unknown")), 0.0)
    if f.get("schema") == "mismatch":
        s -= 40
    if f.get("weak_alias"):
        s -= 5
    if float(f.get("rarity", 1.0)) < 0.35 and float(f.get("name_score", 0)) < 97:
        s -= 5
    return round(max(0.0, min(100.0, s)), 2)


FEATURE_ORDER = [
    "name_score",
    "align",
    "token_sort",
    "token_set",
    "coverage_short",
    "coverage_long",
    "token_diff",
    "rarity",
    "dob_ym",
    "dob_y",
    "dob_conflict",
    "nat_match",
    "nat_conflict",
    "schema_mismatch",
    "weak_alias",
]


def vectorize(f: dict[str, Any]) -> list[float]:
    dob = f.get("dob", "unknown")
    nat = f.get("nationality", "unknown")
    return [
        float(f.get("name_score", 0)) / 100,
        float(f.get("align", 0)) / 100,
        float(f.get("token_sort", 0)) / 100,
        float(f.get("token_set", 0)) / 100,
        float(f.get("coverage_short", 0)),
        float(f.get("coverage_long", 0)),
        min(float(f.get("token_diff", 0)), 3) / 3,
        float(f.get("rarity", 1)),
        1.0 if dob == "year_month" else 0.0,
        1.0 if dob == "year" else 0.0,
        1.0 if dob in {"conflict", "month_conflict"} else 0.0,
        1.0 if nat == "match" else 0.0,
        1.0 if nat == "conflict" else 0.0,
        1.0 if f.get("schema") == "mismatch" else 0.0,
        1.0 if f.get("weak_alias") else 0.0,
    ]


class CalibratedModel:
    """Logistic regression (pure Python) trained by `benchmarks/a_matching/train.py`."""

    def __init__(self, weights: list[float], bias: float) -> None:
        if len(weights) != len(FEATURE_ORDER):
            raise ValueError("weight vector does not match FEATURE_ORDER")
        self.weights = weights
        self.bias = bias

    @classmethod
    def load(cls, path: Path) -> CalibratedModel:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(list(map(float, data["weights"])), float(data["bias"]))

    def predict(self, f: dict[str, Any]) -> float:
        z = self.bias + sum(w * x for w, x in zip(self.weights, vectorize(f), strict=True))
        return 1 / (1 + math.exp(-z))

    @classmethod
    def fit(
        cls,
        rows: list[dict[str, Any]],
        labels: list[int],
        epochs: int = 400,
        lr: float = 0.5,
        l2: float = 1e-3,
    ) -> CalibratedModel:
        xs = [vectorize(r) for r in rows]
        w = [0.0] * len(FEATURE_ORDER)
        b = 0.0
        n = max(len(xs), 1)
        for _ in range(epochs):
            gw = [0.0] * len(w)
            gb = 0.0
            for x, y in zip(xs, labels, strict=True):
                z = b + sum(wi * xi for wi, xi in zip(w, x, strict=True))
                p = 1 / (1 + math.exp(-max(min(z, 30), -30)))
                err = p - y
                gb += err
                for i, xi in enumerate(x):
                    gw[i] += err * xi
            w = [wi - lr * (g / n + l2 * wi) for wi, g in zip(w, gw, strict=True)]
            b -= lr * gb / n
        return cls(w, b)

    def save(self, path: Path) -> None:
        Path(path).write_text(
            json.dumps({"features": FEATURE_ORDER, "weights": self.weights, "bias": self.bias}, indent=2),
            encoding="utf-8",
        )
