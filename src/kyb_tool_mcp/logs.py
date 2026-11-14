"""Logging for the hosted server: one JSON object per line on stderr, in the shape Google Cloud Logging
parses (`severity`, `message`, `logging.googleapis.com/trace`, extra fields under `jsonPayload`).

Privacy rule: log what happened, never who or what was checked. No company names, people, URLs,
request bodies, headers or keys. Only tool names, timings, decisions, flag codes and check statuses.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

TRACE_HEADER = b"x-cloud-trace-context"
_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar("kyb_trace", default=None)

_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    """Cloud Logging structured format. Fields passed as `extra={"fields": {...}}` become top-level keys."""

    def __init__(self, project: str | None = None) -> None:
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": record.name,
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        trace = _trace.get()
        if trace and self.project:
            entry["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace}"
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type and stack frames only: exception messages can carry request data
            # (company numbers, URLs), so they are never logged.
            etype, _, tb = record.exc_info
            frames = "".join(traceback.format_tb(tb))
            entry["error_type"] = etype.__name__
            entry["stack_trace"] = f"Traceback (most recent call last):\n{frames}{etype.__name__}"
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(fmt: str = "text", project: str | None = None, level: int = logging.INFO) -> None:
    """Configure the root logger on stderr (stdout is reserved for the MCP stdio transport)."""
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter(project))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Route uvicorn's own loggers through the same handler (and format).
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    for noisy in ("httpx", "httpx2", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def trace_id_from_header(value: str | None) -> str | None:
    """`X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1` -> TRACE_ID (hex), else None."""
    if not value:
        return None
    trace = value.split("/", 1)[0].strip()
    return trace if trace and all(c in "0123456789abcdefABCDEF" for c in trace) else None


class TraceMiddleware:
    """Pure ASGI middleware: remembers the Cloud Run trace id for log lines written during the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = dict(scope.get("headers") or []).get(TRACE_HEADER)
        token = _trace.set(trace_id_from_header(raw.decode("latin-1") if raw else None))
        try:
            await self.app(scope, receive, send)
        finally:
            _trace.reset(token)
