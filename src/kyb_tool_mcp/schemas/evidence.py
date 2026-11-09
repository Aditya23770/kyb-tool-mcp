"""Evidence: every fact a flag relies on is stored with provenance."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def stable_hash(obj: Any) -> str:
    """SHA-256 of a JSON-serialisable object (sorted keys) or bytes/str."""
    if isinstance(obj, bytes):
        data = obj
    elif isinstance(obj, str):
        data = obj.encode()
    else:
        data = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def _dump(payload: Any) -> Any:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, list):
        return [_dump(p) for p in payload]
    return payload


class Evidence(BaseModel, Generic[T]):
    """`Evidence[T]` = {id, provider, kind, fetched_at, request, payload: T, raw_hash}."""

    id: str
    provider: str
    kind: str = Field(description="e.g. registry.company, screening.candidates, website.snapshot")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: dict[str, Any] = Field(default_factory=dict)
    payload: T
    raw_hash: str

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        kind: str,
        request: dict[str, Any],
        payload: T,
        raw: Any = None,
    ) -> Evidence[T]:
        """Create evidence whose id is derived from provider, kind, request and content (deterministic)."""
        content = raw if raw is not None else _dump(payload)
        raw_hash = stable_hash(content)
        ident = stable_hash({"p": provider, "k": kind, "r": request, "h": raw_hash})[:12]
        return cls(
            id=f"ev_{provider}_{ident}",
            provider=provider,
            kind=kind,
            request=request,
            payload=payload,
            raw_hash=raw_hash,
        )
