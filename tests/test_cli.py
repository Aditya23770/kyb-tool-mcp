from __future__ import annotations

import json
from pathlib import Path

import pytest

from kyb_tool_mcp import cli
from kyb_tool_mcp.config import Config
from kyb_tool_mcp.pipeline import ReviewPipeline
from tests.conftest import MINI, TODAY, clean_application, clean_world


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, config: Config, mini_index_path: Path) -> None:
    world = clean_world()
    original_init = ReviewPipeline.__init__

    def init(self: ReviewPipeline, *args: object, **kwargs: object) -> None:
        original_init(self, config, transport=world.transport(), index_path=mini_index_path, today=TODAY)

    monkeypatch.setattr(ReviewPipeline, "__init__", init)


def test_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "kyb-tool-mcp" in capsys.readouterr().out


@pytest.mark.usefixtures("patched")
def test_review_outputs_json_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = tmp_path / "case.json"
    case.write_text(json.dumps(clean_application()))
    assert cli.main(["review", str(case)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "APPROVE"

    bad = clean_application()
    bad["directors"] = []
    case.write_text(json.dumps({"application": bad, "expected": "ESCALATE"}))
    dest = tmp_path / "out.json"
    assert cli.main(["review", str(case), "--out", str(dest), "--exit-code"]) == 3
    assert json.loads(dest.read_text())["decision"] == "ESCALATE"


@pytest.mark.usefixtures("patched")
def test_screen_registry_website(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["screen", "Viktor Ivanovich Borovkov", "--dob", "1961-03"]) == 0
    assert json.loads(capsys.readouterr().out)["hits"][0]["outcome"] == "TRUE_MATCH"
    assert cli.main(["registry", "09876543"]) == 0
    assert json.loads(capsys.readouterr().out)["records"]
    assert cli.main(["website", "https://brightwater-analytics.co.uk"]) == 0
    assert json.loads(capsys.readouterr().out)["snapshot"]["reachable"]


def test_review_missing_file_is_error() -> None:
    assert cli.main(["review", "does-not-exist.json"]) == 1


def test_init_writes_files(tmp_path: Path) -> None:
    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"policy.yaml", "providers.yaml", ".env.example"}
    assert cli.main(["init", "--dir", str(tmp_path)]) == 0  # skip existing without error


def test_update_lists_from_local_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KYB_DATA_DIR", str(tmp_path))
    rc = cli.main(
        [
            "update-lists",
            "--file",
            f"ofac_sdn={MINI / 'ofac_sdn.xml'}",
            "--file",
            f"un_consolidated={MINI / 'un_consolidated.xml'}",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["entities"] == 9
    assert (tmp_path / "sanctions.sqlite").exists()


def test_update_lists_rejects_unknown_source() -> None:
    assert cli.main(["update-lists", "--sources", "nope"]) == 1


def test_policy_and_status(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KYB_DATA_DIR", str(tmp_path))
    assert cli.main(["policy"]) == 0
    assert "REJECT" in capsys.readouterr().out
    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert "error" in status["sanctions_index"]  # empty data dir => index missing, reported clearly
