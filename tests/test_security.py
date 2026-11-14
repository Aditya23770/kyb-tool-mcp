"""Secrets never reach users: the caller's Companies House key must not appear in any tool output,
evidence, health response, log line or settings dump. Offline (FakeWorld + mini index)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from kyb_tool_mcp import cli
from kyb_tool_mcp.config import Config, Settings
from tests.conftest import clean_application, clean_world
from tests.test_http import BASE, KEY, app_for, http, mcp_client, world_requiring_key
from tests.test_mcp import payload


async def test_key_absent_from_every_response_and_log(
    config: Config, mini_index_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    app = app_for(config, mini_index_path, world_requiring_key())
    dumps: list[str] = []
    async with (
        http(app, {"X-Companies-House-Key": KEY}) as hc,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=hc)) as c,
    ):
        dumps.append(json.dumps(payload(await c.call_tool("review_kyb_case", clean_application()))))
        dumps.append(
            json.dumps(payload(await c.call_tool("registry_lookup", {"registration_number": "09876543"})))
        )
        dumps.append(json.dumps(payload(await c.call_tool("screen_entity", {"name": "Sarah Holloway"}))))
        dumps.append(str((await c.read_resource("kyb://policy")).contents))
        dumps.append(c.instructions or "")
        dumps.append((await hc.get("/healthz")).text)
    for text in dumps:
        assert KEY not in text
    assert KEY not in caplog.text


async def test_key_absent_from_error_path(
    config: Config, mini_index_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world = world_requiring_key()
    world.companies_house_key = "the-real-key"  # caller's key will be rejected (401)
    app = app_for(config, mini_index_path, world)
    async with mcp_client(app, {"X-Companies-House-Key": KEY}) as c:
        out = json.dumps(payload(await c.call_tool("review_kyb_case", clean_application())))
    assert "PROVIDER_ERROR" in out and "rejected" in out
    assert KEY not in out and "the-real-key" not in out
    assert KEY not in caplog.text


def test_settings_never_show_the_key() -> None:
    s = Settings(_env_file=None, companies_house_api_key=KEY)  # type: ignore[call-arg]
    assert KEY not in repr(s) and KEY not in str(s)
    assert KEY not in json.dumps(s.model_dump(mode="json"))
    assert s.companies_house_api_key is not None
    assert s.companies_house_api_key.get_secret_value() == KEY


def test_status_command_never_prints_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", KEY)
    monkeypatch.setenv("KYB_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # no stray .env
    assert cli.main(["status"]) == 0
    out = capsys.readouterr()
    assert KEY not in out.out and KEY not in out.err
    assert json.loads(out.out)["companies_house_api_key"] == "set"


def test_header_key_never_stored_on_server_objects(config: Config, mini_index_path: Path) -> None:
    app = app_for(config, mini_index_path, clean_world())
    assert KEY not in repr(vars(app))


def test_serve_http_flags_parse() -> None:
    args = cli.build_parser().parse_args(["serve", "--http", "--host", "127.0.0.1", "--port", "9001"])
    assert args.http is True and args.host == "127.0.0.1" and args.port == 9001
    plain = cli.build_parser().parse_args(["serve"])
    assert plain.http is False
