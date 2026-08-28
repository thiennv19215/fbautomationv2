"""MCP tool: get post history."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="get_history",
    description="Get recent publication history and upload results (permalink, video ID, post ID, timestamp).",
)
async def get_history(limit: int = 50) -> dict:
    """Return recent posting history records."""
    return await bridge.get_history(limit=limit)
