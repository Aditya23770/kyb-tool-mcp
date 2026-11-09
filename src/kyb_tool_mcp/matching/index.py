"""Local sanctions index: SQLite FTS5 (tokens) + phonetic keys + FTS5 trigram (typos).

Built by `kyb-tool update-lists` (or `IndexBuilder` directly). Read by `SanctionsIndex`.
"""

from __future__ import annotations

import gc
import json
import math
import os
import sqlite3
import tempfile
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kyb_tool_mcp.matching.normalize import (
    company_tokens,
    merge_particles,
    person_tokens,
    phonetic,
)
from kyb_tool_mcp.schemas.screening import ListEntity

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, schema TEXT NOT NULL, caption TEXT, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entities_source ON entities(source);
CREATE TABLE IF NOT EXISTS names (
    id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL, weak INTEGER
);
CREATE INDEX IF NOT EXISTS ix_names_entity ON names(entity_id);
CREATE TABLE IF NOT EXISTS phon (key TEXT NOT NULL, entity_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_phon_key ON phon(key);
CREATE TABLE IF NOT EXISTS tokfreq (token TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS names_fts USING fts5(text, entity_id UNINDEXED, tokenize='unicode61');
"""

TRIGRAM_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS tri USING fts5(text, entity_id UNINDEXED, tokenize='trigram');"
)


def entity_name_tokens(name: str, schema: str) -> list[str]:
    return person_tokens(name) if schema.lower() == "person" else company_tokens(name)


def _fts_text(tokens: list[str]) -> str:
    merged = merge_particles(tokens)
    extra = [t for t in merged if t not in tokens]
    return " ".join(tokens + extra)


class IndexBuilder:
    """Builds a fresh index into a temp file and atomically swaps it into place."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="kyb-index-", suffix=".sqlite", dir=self.path.parent)
        os.close(fd)
        self.tmp = Path(tmp)
        self.conn = sqlite3.connect(self.tmp)
        self.conn.executescript(SCHEMA_SQL)
        try:
            self.conn.execute(TRIGRAM_SQL)
            self.trigram = True
        except sqlite3.OperationalError:  # pragma: no cover - very old SQLite
            self.trigram = False
        self.counts: Counter[str] = Counter()
        self.tokfreq: Counter[str] = Counter()

    def add(self, entities: Iterable[ListEntity]) -> int:
        cur = self.conn.cursor()
        n = 0
        for ent in entities:
            data = ent.model_dump(mode="json", by_alias=True)
            cur.execute(
                "INSERT OR REPLACE INTO entities(id, source, schema, caption, data) VALUES (?,?,?,?,?)",
                (ent.id, ent.source, ent.schema_, ent.caption, json.dumps(data, ensure_ascii=False)),
            )
            pkeys: set[str] = set()
            ent_tokens: set[str] = set()
            for name, weak in [(nm, 0) for nm in ent.names] + [(nm, 1) for nm in ent.weak_names]:
                toks = entity_name_tokens(name, ent.schema_)
                if not toks:
                    continue
                norm = " ".join(toks)
                cur.execute(
                    "INSERT INTO names(entity_id, name, norm, weak) VALUES (?,?,?,?)",
                    (ent.id, name, norm, weak),
                )
                text = _fts_text(toks)
                cur.execute("INSERT INTO names_fts(text, entity_id) VALUES (?,?)", (text, ent.id))
                if self.trigram:
                    cur.execute(
                        "INSERT INTO tri(text, entity_id) VALUES (?,?)", (norm.replace(" ", ""), ent.id)
                    )
                for t in toks:
                    ent_tokens.add(t)
                    if len(t) >= 3:
                        pkeys.add(phonetic(t))
            for k in pkeys:
                cur.execute("INSERT INTO phon(key, entity_id) VALUES (?,?)", (k, ent.id))
            self.tokfreq.update(ent_tokens)
            self.counts[ent.source] += 1
            n += 1
        cur.close()
        return n

    def finish(self, extra_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        cur = self.conn.cursor()
        cur.executemany("INSERT OR REPLACE INTO tokfreq(token, n) VALUES (?,?)", self.tokfreq.items())
        meta = {
            "built_at": datetime.now(UTC).isoformat(),
            "sources": dict(self.counts),
            "entities": sum(self.counts.values()),
            "trigram": self.trigram,
            **(extra_meta or {}),
        }
        cur.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
            [(k, json.dumps(v)) for k, v in meta.items()],
        )
        cur.close()
        self.conn.commit()
        self.conn.execute("VACUUM")
        self.conn.close()
        # Python 3.11 on Windows keeps the file locked until every statement is released.
        gc.collect()
        os.replace(self.tmp, self.path)
        return meta

    def abort(self) -> None:
        self.conn.close()
        self.tmp.unlink(missing_ok=True)


def _best_rank(rows: list[Any], limit: int) -> dict[str, float]:
    """Keep each entity's best (lowest) bm25 rank, as a positive relevance score, top `limit`."""
    best: dict[str, float] = {}
    for eid, rank in rows:
        score = -float(rank)
        if eid not in best or score > best[eid]:
            best[eid] = score
    return dict(sorted(best.items(), key=lambda kv: -kv[1])[:limit])


class IndexMissingError(RuntimeError):
    pass


class SanctionsIndex:
    """Read-only query interface over a built index."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise IndexMissingError(
                f"Sanctions index not found at {self.path}. Run `kyb-tool update-lists` first."
            )
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()
        self._meta = {k: json.loads(v) for k, v in self.conn.execute("SELECT key, value FROM meta")}
        self._n = max(int(self._meta.get("entities", 1)), 1)
        self._has_tri = bool(self._meta.get("trigram"))
        self._entity_cache: dict[str, ListEntity] = {}
        self._idf_cache: dict[str, float] = {}
        self.candidate_cache: dict[tuple[Any, ...], list[Any]] = {}

    @property
    def meta(self) -> dict[str, Any]:
        return dict(self._meta)

    def sources(self) -> list[str]:
        return list(self._meta.get("sources", {}))

    def close(self) -> None:
        self.conn.close()

    # ---- statistics ------------------------------------------------------
    def idf(self, token: str) -> float:
        cached = self._idf_cache.get(token)
        if cached is not None:
            return cached
        row = self.conn.execute("SELECT n FROM tokfreq WHERE token=?", (token,)).fetchone()
        n = row[0] if row else 0
        value = math.log((self._n + 1) / (n + 1))
        if len(self._idf_cache) < 200_000:
            self._idf_cache[token] = value
        return value

    def max_idf(self) -> float:
        return math.log(self._n + 1)

    # ---- blocking --------------------------------------------------------
    def fts_candidates(self, tokens: list[str], limit: int) -> dict[str, float]:
        toks = sorted({t for t in tokens + merge_particles(tokens) if t})
        if not toks:
            return {}
        q = " OR ".join(f'"{t}"' for t in toks)
        rows = self.conn.execute(
            "SELECT entity_id, bm25(names_fts) AS r FROM names_fts WHERE names_fts MATCH ? ORDER BY r LIMIT ?",
            (q, limit * 3),
        ).fetchall()
        return _best_rank(rows, limit)

    def phonetic_candidates(self, tokens: list[str], limit: int) -> dict[str, int]:
        keys = sorted({phonetic(t) for t in tokens if len(t) >= 3})
        if not keys:
            return {}
        need = min(2, len(keys))
        ph = ",".join("?" * len(keys))
        rows = self.conn.execute(
            f"SELECT entity_id, count(DISTINCT key) c FROM phon WHERE key IN ({ph}) "
            "GROUP BY entity_id HAVING c >= ? ORDER BY c DESC LIMIT ?",
            (*keys, need, limit),
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def trigram_candidates(self, tokens: list[str], limit: int) -> dict[str, float]:
        if not self._has_tri:
            return {}
        s = "".join(tokens)
        if len(s) < 4 or len(s) > 24:
            return {}
        grams = sorted({s[i : i + 3] for i in range(len(s) - 2)})
        q = " OR ".join(f'"{g}"' for g in grams)
        rows = self.conn.execute(
            "SELECT entity_id, bm25(tri) AS r FROM tri WHERE tri MATCH ? ORDER BY r LIMIT ?",
            (q, limit * 3),
        ).fetchall()
        return _best_rank(rows, limit)

    # ---- entities --------------------------------------------------------
    def get_entities(self, ids: Iterable[str]) -> list[ListEntity]:
        out: list[ListEntity] = []
        missing = [i for i in ids if i not in self._entity_cache]
        for chunk_start in range(0, len(missing), 500):
            chunk = missing[chunk_start : chunk_start + 500]
            ph = ",".join("?" * len(chunk))
            for _id, data in self.conn.execute(f"SELECT id, data FROM entities WHERE id IN ({ph})", chunk):
                self._entity_cache[_id] = ListEntity.model_validate(json.loads(data))
        for i in ids:
            if i in self._entity_cache:
                out.append(self._entity_cache[i])
        return out

    def get_entity(self, entity_id: str) -> ListEntity | None:
        ents = self.get_entities([entity_id])
        return ents[0] if ents else None

    def count(self) -> int:
        row = self.conn.execute("SELECT count(*) FROM entities").fetchone()
        return int(row[0])

    def iter_entities(self, source: str | None = None, limit: int | None = None) -> list[ListEntity]:
        sql = "SELECT data FROM entities"
        args: list[Any] = []
        if source:
            sql += " WHERE source=?"
            args.append(source)
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return [ListEntity.model_validate(json.loads(r[0])) for r in self.conn.execute(sql, args)]
