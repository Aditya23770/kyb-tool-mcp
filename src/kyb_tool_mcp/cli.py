"""`kyb-tool-mcp` command.

    kyb-tool-mcp                      # start the MCP server on stdio (what MCP clients run)
    kyb-tool-mcp serve                # same
    kyb-tool-mcp update-lists         # download sanctions lists + build the local index
    kyb-tool-mcp review case.json     # run a review from the terminal (testing / scripting)
    kyb-tool-mcp screen "Name" --dob 1971-03 --nationality RU
    kyb-tool-mcp registry 00445790
    kyb-tool-mcp website https://example.com
    kyb-tool-mcp status               # config, keys, index and provider status
    kyb-tool-mcp init                 # write editable policy.yaml / providers.yaml / .env.example
    kyb-tool-mcp policy               # print the decision rules

Exit codes for `review`: 0 APPROVE, 3 ESCALATE, 4 REJECT (with --exit-code), 1 error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from kyb_tool_mcp import __version__

ENV_EXAMPLE = """# kyb-tool-mcp configuration (copy to .env or set in your MCP client config)
# Free key: https://developer.company-information.service.gov.uk/
COMPANIES_HOUSE_API_KEY=
# Optional overrides
# KYB_DATA_DIR=~/.kyb-tool-mcp          # sanctions index, cache and audit log
# KYB_POLICY_FILE=./policy.yaml
# KYB_PROVIDERS_FILE=./providers.yaml
# KYB_AUDIT=true
"""

EXIT = {"APPROVE": 0, "ESCALATE": 3, "REJECT": 4}


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_serve(a: argparse.Namespace) -> int:
    if getattr(a, "http", False):
        from kyb_tool_mcp.mcp_server import run_http

        run_http(host=a.host, port=a.port)
        return 0
    from kyb_tool_mcp.mcp_server import run_stdio

    run_stdio()
    return 0


def cmd_update_lists(a: argparse.Namespace) -> int:
    from kyb_tool_mcp.lists import DEFAULT_SOURCES, build_index

    sources = [s.strip() for s in a.sources.split(",")] if a.sources else DEFAULT_SOURCES
    files = {}
    for spec in a.file or []:
        name, _, path = spec.partition("=")
        files[name] = Path(path)
    meta = build_index(sources, files=files or None)
    _print(meta)
    return 0


def cmd_review(a: argparse.Namespace) -> int:
    from kyb_tool_mcp.pipeline import ReviewPipeline

    data = json.loads(Path(a.case).read_text(encoding="utf-8"))
    app = data.get("application", data)
    result = asyncio.run(ReviewPipeline().review(app))
    out = result.model_dump(mode="json")
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        _err(f"{result.decision} ({result.risk_level}, score {result.score}) -> {a.out}")
    else:
        _print(out)
    return EXIT[result.decision] if a.exit_code else 0


def cmd_screen(a: argparse.Namespace) -> int:
    from kyb_tool_mcp.pipeline import screen_entity

    year = month = None
    if a.dob:
        parts = a.dob.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else None
    subject = {
        "name": a.name,
        "type": a.type,
        "birth_year": year,
        "birth_month": month,
        "nationality": a.nationality,
    }
    _print(asyncio.run(screen_entity(subject)))
    return 0


def cmd_registry(a: argparse.Namespace) -> int:
    from kyb_tool_mcp.pipeline import ReviewPipeline

    _print(asyncio.run(ReviewPipeline().registry_lookup(a.country, a.number, a.name)))
    return 0


def cmd_website(a: argparse.Namespace) -> int:
    from kyb_tool_mcp.pipeline import ReviewPipeline

    _print(asyncio.run(ReviewPipeline().check_website(a.url, a.industry, a.company)))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    from kyb_tool_mcp.config import load_config
    from kyb_tool_mcp.matching.index import IndexMissingError, SanctionsIndex

    cfg = load_config()
    s = cfg.settings
    try:
        idx = SanctionsIndex(s.index_path)
        index: dict[str, Any] = {"path": str(s.index_path), **idx.meta}
        idx.close()
    except IndexMissingError as e:
        index = {"path": str(s.index_path), "error": str(e)}
    _print(
        {
            "version": __version__,
            "data_dir": str(s.kyb_data_dir),
            "policy_version": cfg.policy.version,
            "companies_house_api_key": "set"
            if s.companies_house_api_key
            else "MISSING (registry falls back to GLEIF)",
            "providers": cfg.providers.model_dump(),
            "sanctions_index": index,
        }
    )
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    from kyb_tool_mcp.config import default_policy_text, default_providers_text

    target = Path(a.dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, text in (
        ("policy.yaml", default_policy_text()),
        ("providers.yaml", default_providers_text()),
        (".env.example", ENV_EXAMPLE),
    ):
        p = target / name
        if p.exists() and not a.force:
            _err(f"skip {p} (exists; use --force)")
            continue
        p.write_text(text, encoding="utf-8")
        _err(f"wrote {p}")
    return 0


def cmd_policy(_: argparse.Namespace) -> int:
    from kyb_tool_mcp.config import load_config
    from kyb_tool_mcp.policy import explain_policy

    print(explain_policy(load_config().policy))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kyb-tool-mcp", description="Local KYB MCP server and CLI.")
    p.add_argument("--version", action="version", version=f"kyb-tool-mcp {__version__}")
    sub = p.add_subparsers(dest="command")

    sv = sub.add_parser("serve", help="run the MCP server on stdio (default), or over HTTP with --http")
    sv.add_argument(
        "--http",
        action="store_true",
        help="serve Streamable HTTP at /mcp (hosted connector) instead of stdio",
    )
    sv.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind address (default 127.0.0.1; use 0.0.0.0 in a container)",
    )
    sv.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="HTTP port (default $PORT or 8080)",
    )
    sv.set_defaults(fn=cmd_serve)

    u = sub.add_parser("update-lists", help="download sanctions lists and build the local index")
    u.add_argument(
        "--sources", help="comma list: ofac_sdn,un_consolidated,opensanctions_bulk (non-commercial)"
    )
    u.add_argument(
        "--file", action="append", metavar="SOURCE=PATH", help="use a local file instead of downloading"
    )
    u.set_defaults(fn=cmd_update_lists)

    r = sub.add_parser("review", help="review an application JSON file")
    r.add_argument("case")
    r.add_argument("--out")
    r.add_argument("--exit-code", action="store_true", help="exit 3 on ESCALATE, 4 on REJECT")
    r.set_defaults(fn=cmd_review)

    s = sub.add_parser("screen", help="screen one name")
    s.add_argument("name")
    s.add_argument("--type", choices=["person", "company"], default="person")
    s.add_argument("--dob", help="YYYY or YYYY-MM")
    s.add_argument("--nationality")
    s.set_defaults(fn=cmd_screen)

    g = sub.add_parser("registry", help="registry lookup")
    g.add_argument("number", nargs="?")
    g.add_argument("--name")
    g.add_argument("--country", default="GB")
    g.set_defaults(fn=cmd_registry)

    w = sub.add_parser("website", help="check a website")
    w.add_argument("url")
    w.add_argument("--industry")
    w.add_argument("--company")
    w.set_defaults(fn=cmd_website)

    sub.add_parser("status", help="show configuration and index status").set_defaults(fn=cmd_status)
    i = sub.add_parser("init", help="write editable config files")
    i.add_argument("--dir", default=".")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)
    sub.add_parser("policy", help="print the decision rules").set_defaults(fn=cmd_policy)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn = getattr(args, "fn", cmd_serve)
    try:
        return int(fn(args))
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        if args.command in (None, "serve"):
            raise
        _err(f"error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
