"""Per-call Companies House key (library API): used when given, configured key as fallback,
missing key stays fail-closed (`not_configured`) with the caller's hint."""

from __future__ import annotations

from pathlib import Path

from kyb_tool_mcp.config import Config
from kyb_tool_mcp.pipeline import ReviewPipeline
from tests.conftest import TODAY, clean_application, clean_world


def pipeline(config: Config, index: Path, key: str | None, hint: str | None = None) -> ReviewPipeline:
    world = clean_world()
    world.companies_house_key = "per-call-key"
    kwargs = {"missing_key_hint": hint} if hint else {}
    if key is None:
        config.settings.companies_house_api_key = None
    else:
        config.settings = type(config.settings)(  # type: ignore[call-arg]
            _env_file=None,
            companies_house_api_key=key,
            kyb_data_dir=config.settings.kyb_data_dir,
            kyb_audit=False,
            kyb_http_cache=False,
        )
    return ReviewPipeline(config, transport=world.transport(), index_path=index, today=TODAY, **kwargs)


async def test_per_call_key_used_when_no_configured_key(config: Config, mini_index_path: Path) -> None:
    pipe = pipeline(config, mini_index_path, None)
    r = await pipe.review(clean_application(), companies_house_api_key="per-call-key")
    assert {c.check: c.status for c in r.checks}["registry"] == "done"
    out = await pipe.registry_lookup("GB", "09876543", None, companies_house_api_key="per-call-key")
    assert out["records"] and not out["not_configured"]


async def test_configured_key_still_works(config: Config, mini_index_path: Path) -> None:
    pipe = pipeline(config, mini_index_path, "per-call-key")
    r = await pipe.review(clean_application())
    assert {c.check: c.status for c in r.checks}["registry"] == "done"


async def test_missing_key_is_not_configured_with_hint(config: Config, mini_index_path: Path) -> None:
    config.providers.registry = ["companies_house"]
    pipe = pipeline(config, mini_index_path, None, hint="add the header")
    r = await pipe.review(clean_application())
    reg = next(c for c in r.checks if c.check == "registry")
    assert reg.status == "not_configured" and "add the header" in (reg.note or "")
    assert "CHECK_NOT_CONFIGURED" in {f.code for f in r.flags}


async def test_default_hint_names_env_var(config: Config, mini_index_path: Path) -> None:
    config.providers.registry = ["companies_house"]
    pipe = pipeline(config, mini_index_path, None)
    out = await pipe.registry_lookup("GB", "09876543", None)
    assert any("COMPANIES_HOUSE_API_KEY" in m for m in out["not_configured"])
