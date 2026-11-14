"""kyb-tool-mcp: open-source KYB screening and case-review engine, run locally as an MCP server.

Library usage::

    from kyb_tool_mcp import review_case, screen_entity
    result = await review_case({"case_id": "c1", "legal_name": "Acme Ltd", "registration_number": "01234567"})
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.2.0"

if TYPE_CHECKING:
    from kyb_tool_mcp.schemas.decision import ReviewResult


async def review_case(application: dict[str, Any], **kwargs: Any) -> ReviewResult:
    """Run the full KYB pipeline on an application dict."""
    from kyb_tool_mcp.pipeline import review_case as _review_case

    return await _review_case(application, **kwargs)


async def screen_entity(subject: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Screen one person or company against the local sanctions index."""
    from kyb_tool_mcp.pipeline import screen_entity as _screen_entity

    return await _screen_entity(subject, **kwargs)


__all__ = ["__version__", "review_case", "screen_entity"]
