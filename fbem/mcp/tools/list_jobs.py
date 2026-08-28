"""MCP tool: list jobs in the posting queue."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="list_jobs",
    description="List queued, running, completed, or failed background jobs in the FBEM dispatcher.",
)
async def list_jobs(status: str | None = None, limit: int = 50) -> list[dict]:
    """List jobs filtered optionally by status ('queued', 'running', 'completed', 'failed', 'canceled')."""
    return await bridge.list_jobs(status=status, limit=limit)
