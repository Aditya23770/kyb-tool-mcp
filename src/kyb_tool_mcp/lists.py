"""Download sanctions lists and build the local index (`kyb-tool-mcp update-lists`)."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from kyb_tool_mcp.config import Settings
from kyb_tool_mcp.matching.index import IndexBuilder
from kyb_tool_mcp.providers.sanctions.loaders import ofac_sdn, opensanctions_bulk, un_consolidated
from kyb_tool_mcp.schemas.screening import ListEntity


@dataclass(frozen=True)
class ListSource:
    name: str
    url: str
    filename: str
    loader: Callable[[Path], Iterator[ListEntity]]
    non_commercial: bool = False


SOURCES: dict[str, ListSource] = {
    "ofac_sdn": ListSource("ofac_sdn", ofac_sdn.URL, "ofac_sdn.xml", ofac_sdn.load),
    "un_consolidated": ListSource(
        "un_consolidated", un_consolidated.URL, "un_consolidated.xml", un_consolidated.load
    ),
    "opensanctions_bulk": ListSource(
        "opensanctions_bulk", opensanctions_bulk.URL, "opensanctions.ftm.json", opensanctions_bulk.load, True
    ),
}
DEFAULT_SOURCES = ["ofac_sdn", "un_consolidated"]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)  # stderr: stdout is reserved for MCP stdio


def download(src: ListSource, dest_dir: Path, user_agent: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.filename
    tmp = dest.with_suffix(dest.suffix + ".part")
    _log(f"downloading {src.name} from {src.url}")
    with (
        httpx.stream(
            "GET", src.url, follow_redirects=True, timeout=120, headers={"User-Agent": user_agent}
        ) as r,
        open(tmp, "wb") as fh,
    ):
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 16):
            fh.write(chunk)
    tmp.replace(dest)
    _log(f"  saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def build_index(
    sources: list[str] | None = None,
    settings: Settings | None = None,
    files: dict[str, Path] | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Download (unless `files` given) every source and rebuild the index atomically.

    Fails loudly: if any loader yields zero entities, the old index is kept and an error raised.
    """
    s = settings or Settings()
    names = sources or DEFAULT_SOURCES
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise ValueError(f"unknown list source(s): {unknown}. Known: {sorted(SOURCES)}")
    raw_dir = s.kyb_data_dir / "raw"
    builder = IndexBuilder(index_path or s.index_path)
    try:
        for name in names:
            src = SOURCES[name]
            if src.non_commercial:
                _log(f"NOTE: {name} is CC BY-NC (non-commercial use only).")
            path = (files or {}).get(name) or download(src, raw_dir, s.user_agent)
            n = builder.add(src.loader(path))
            if n == 0:
                raise RuntimeError(f"{name}: loader produced 0 entities; format may have changed")
            _log(f"  indexed {n} entities from {name}")
        meta = builder.finish({"list_sources": names})
    except BaseException:
        builder.abort()
        raise
    _log(f"index ready: {meta['entities']} entities -> {index_path or s.index_path}")
    return meta
