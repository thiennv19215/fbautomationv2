"""MCP tool: list Facebook pages discovered by extension."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="list_pages",
    description="List Facebook Pages discovered/cached from connected browser extensions.",
)
async def list_pages(extension_id: str | None = None) -> dict:
    """Return discovered Facebook pages for an extension or all extensions."""
    return await bridge.list_pages(extension_id=extension_id)
