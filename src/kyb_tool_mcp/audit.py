"""Append-only SQLite audit log of every review (stays on the user's machine)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kyb_tool_mcp.evidence import EvidenceStore
from kyb_tool_mcp.schemas.application import Application
from kyb_tool_mcp.schemas.decision import ReviewResult
from kyb_tool_mcp.schemas.evidence import stable_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    case_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_json TEXT NOT NULL,
    decision TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    score INTEGER NOT NULL,
    flags_json TEXT NOT NULL,
    evidence_hashes_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reviews_case ON reviews(case_id);
"""


class AuditLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as c:
            c.executescript(SCHEMA)

    def record(self, app: Application, result: ReviewResult, store: EvidenceStore) -> int:
        input_json = app.model_dump_json()
        row = (
            datetime.now(UTC).isoformat(),
            result.case_id,
            stable_hash(input_json),
            input_json,
            result.decision,
            result.risk_level,
            result.score,
            json.dumps([f.model_dump(mode="json") for f in result.flags]),
            json.dumps({e.id: e.raw_hash for e in store.items()}),
            result.policy_version,
            result.kyb_tool_version,
            result.model_dump_json(),
        )
        with sqlite3.connect(self.path) as c:
            cur = c.execute(
                "INSERT INTO reviews(created_at, case_id, input_hash, input_json, decision, risk_level, score, "
                "flags_json, evidence_hashes_json, policy_version, tool_version, result_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            return int(cur.lastrowid or 0)

    def history(self, case_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT id, created_at, case_id, decision, risk_level, score, policy_version FROM reviews"
        args: list[Any] = []
        if case_id:
            sql += " WHERE case_id=?"
            args.append(case_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with sqlite3.connect(self.path) as c:
            cols = ["id", "created_at", "case_id", "decision", "risk_level", "score", "policy_version"]
            return [dict(zip(cols, r, strict=True)) for r in c.execute(sql, args)]
