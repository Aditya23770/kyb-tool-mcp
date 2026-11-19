"""Structured logs for Google Cloud Logging: JSON shape, trace linking, and no personal data or keys."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from kyb_tool_mcp.config import Config
from kyb_tool_mcp.logs import JsonFormatter, trace_id_from_header
from tests.conftest import clean_application
from tests.test_http import BASE, KEY, app_for, http, world_requiring_key

TRACE = "105445aa7843bc8bf206b12000100000"


def test_json_formatter_shape() -> None:
    rec = logging.LogRecord("kyb_tool_mcp", logging.WARNING, __file__, 1, "hello %s", ("x",), None)
    rec.fields = {"event": "tool_call", "tool": "screen_entity"}
    out = json.loads(JsonFormatter("my-proj").format(rec))
    assert out["severity"] == "WARNING" and out["message"] == "hello x"
    assert out["event"] == "tool_call" and out["tool"] == "screen_entity"
    assert "logging.googleapis.com/trace" not in out  # no request, no trace


def test_error_logs_type_and_frames_but_not_the_message() -> None:
    number = "0987" + "6543"  # request data arrives at runtime, never in source lines
    try:
        raise RuntimeError(f"HTTP 500 for /company/{number}")
    except RuntimeError as e:
        rec = logging.LogRecord(
            "kyb_tool_mcp", logging.ERROR, __file__, 1, "tool_error", (), (type(e), e, e.__traceback__)
        )
    out = json.loads(JsonFormatter().format(rec))
    assert out["severity"] == "ERROR" and out["error_type"] == "RuntimeError"
    assert "test_logs.py" in out["stack_trace"]
    assert "09876543" not in json.dumps(out)


def test_trace_header_parsing() -> None:
    assert trace_id_from_header(f"{TRACE}/1;o=1") == TRACE
    assert trace_id_from_header(None) is None
    assert trace_id_from_header("not-hex!/1") is None


async def test_tool_calls_log_outcome_only_with_trace(config: Config, mini_index_path: Path) -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter("my-proj"))
    logger = logging.getLogger("kyb_tool_mcp")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        app = app_for(config, mini_index_path, world_requiring_key())
        headers = {"X-Companies-House-Key": KEY, "X-Cloud-Trace-Context": f"{TRACE}/1;o=1"}
        async with (
            http(app, headers) as hc,
            Client(streamable_http_client(f"{BASE}/mcp", http_client=hc)) as c,
        ):
            await c.call_tool("review_kyb_case", clean_application())
            await c.call_tool("screen_entity", {"name": "Sarah Holloway", "birth_year": 1979})
            await c.call_tool("check_website", {"url": "https://brightwater-analytics.co.uk"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    text = buf.getvalue()
    lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    calls = {line["tool"]: line for line in lines if line.get("event") == "tool_call"}
    assert set(calls) == {"review_kyb_case", "screen_entity", "check_website"}

    review = calls["review_kyb_case"]
    assert review["severity"] == "INFO" and review["decision"] == "APPROVE"
    assert review["key_supplied"] is True and review["checks"]["registry"] == "done"
    assert isinstance(review["duration_ms"], int)
    assert review["logging.googleapis.com/trace"] == f"projects/my-proj/traces/{TRACE}"

    # Outcome only: no key, no company, people, numbers or URLs.
    for secret in (
        KEY,
        "Brightwater",
        "BRIGHTWATER",
        "Holloway",
        "Okafor",
        "09876543",
        "brightwater-analytics",
    ):
        assert secret not in text, secret
