"""MCP tool: enqueue background job."""
from __future__ import annotations

from typing import Optional
from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="enqueue_job",
    description=(
        "Enqueue a background posting or profile-switch job into the dispatcher queue. "
        "Supports delay_seconds or run_at timestamps to stagger posts automatically."
    ),
)
async def enqueue_job(
    kind: str,
    input_data: dict,
    account_id: Optional[str] = None,
    extension_id: Optional[str] = None,
    delay_seconds: Optional[int] = None,
    run_at: Optional[int] = None,
) -> dict:
    """Enqueue a job. kind is typically 'post_reel', 'post_photos', or 'switch_profile'."""
    payload: dict = {
        "kind": kind,
        "input": input_data,
    }
    if account_id:
        payload["accountId"] = account_id
    if extension_id:
        payload["extensionId"] = extension_id
    if delay_seconds is not None:
        payload["delaySeconds"] = delay_seconds
    if run_at is not None:
        payload["runAt"] = run_at
    return await bridge.enqueue_job(payload)
