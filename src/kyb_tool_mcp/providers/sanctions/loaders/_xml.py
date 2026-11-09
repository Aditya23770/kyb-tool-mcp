"""Namespace-agnostic XML helpers for list loaders."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def strip_ns(el: ET.Element) -> ET.Element:
    for e in el.iter():
        e.tag = local(e.tag)
    return el


def iter_elements(path: Path, tag: str) -> Iterator[ET.Element]:
    """Stream elements named `tag` (any namespace), freeing memory as we go."""
    for _event, el in ET.iterparse(str(path), events=("end",)):
        if local(el.tag) == tag:
            yield strip_ns(el)
            el.clear()


def text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def texts(el: ET.Element, path: str) -> list[str]:
    return [(f.text or "").strip() for f in el.findall(path) if f.text and f.text.strip()]
