"""MCP tool: stage media from a remote URL."""
from __future__ import annotations

from typing import Optional
from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="stage_media_from_url",
    description="Download media from an external URL and stage it directly into the FBEM bridge media store.",
)
async def stage_media_from_url(url: str, filename: Optional[str] = None) -> dict:
    """Download and stage a remote media file (video/image) onto the server."""
    return await bridge.stage_media_url(url=url, filename=filename)
