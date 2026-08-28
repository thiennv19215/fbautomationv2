"""MCP tool: get queue and account statistics."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="get_stats",
    description="Get real-time statistics of queue jobs (queued, running, completed, failed) and accounts.",
)
async def get_stats() -> dict:
    """Return stats summary from the bridge."""
    return await bridge.get_stats()
