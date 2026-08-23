"""MCP tool: switch the acting Facebook page/profile."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    description=(
        "Switch the browser session to a target Facebook page/profile id so "
        "subsequent post_reel / post_photos go out AS that page (one account, many "
        "Pages). Requires a captured CometProfileSwitchMutation template."
    )
)
async def switch_profile(target_id: str, extension_id: str | None = None) -> dict:
    """Switch the acting identity, then reload the tab.

    Args:
        target_id: The page/profile id to switch to.
        extension_id: Optional Chrome extension / profile id to target if multiple are connected.
    Returns: ``{ ok, identityId, identityName }``.
    """
    return await bridge.switch_profile(target_id, extension_id=extension_id)
