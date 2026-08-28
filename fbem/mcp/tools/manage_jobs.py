"""MCP tool: cancel or retry a queue job."""
from __future__ import annotations

from ..registry import tool
from .. import bridge_api as bridge


@tool(
    name="cancel_job",
    description="Cancel a queued background job by job ID.",
)
async def cancel_job(job_id: str) -> dict:
    """Cancel a queued job."""
    return await bridge.cancel_job(job_id=job_id)


@tool(
    name="retry_job",
    description="Retry a failed or canceled background job by job ID.",
)
async def retry_job(job_id: str) -> dict:
    """Retry a failed or canceled job."""
    return await bridge.retry_job(job_id=job_id)
