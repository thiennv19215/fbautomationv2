"""MCP tool: list registered Facebook accounts/pages in the database."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="list_accounts",
    description="List stored Facebook accounts/pages managed by FBEM with their settings and bound extension IDs.",
)
async def list_accounts(extension_id: str | None = None) -> list[dict]:
    """Return all accounts configured in the bridge SQLite database."""
    return await bridge.list_accounts(extension_id=extension_id)
