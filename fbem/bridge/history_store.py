"""Lightweight persistent job history store for FBEM."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional
from pathlib import Path

from .config import home_dir

logger = logging.getLogger(__name__)

_HISTORY_PATH = home_dir() / "history.json"


def _ensure_store() -> list[dict]:
    if not _HISTORY_PATH.exists():
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_PATH.write_text("[]", encoding="utf-8")
        return []
    try:
        data = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("failed to load history.json: %s", exc)
        return []


def _save_store(jobs: list[dict]) -> None:
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _HISTORY_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_HISTORY_PATH)
    except Exception as exc:
        logger.error("failed to save history.json: %s", exc)


def add_job(
    kind: str,
    payload: dict,
    extension_id: Optional[str] = None,
    page_id: Optional[str] = None,
    caption: str = "",
) -> dict:
    jobs = _ensure_store()
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "kind": kind,
        "extensionId": extension_id,
        "pageId": page_id,
        "caption": caption[:300] if caption else "",
        "status": "running",
        "createdAt": int(time.time()),
        "updatedAt": int(time.time()),
        "payload": payload,
        "result": None,
        "error": None,
    }
    jobs.insert(0, job)
    _save_store(jobs[:200])  # keep recent 200
    return job


def update_job(
    job_id: str,
    status: str,
    result: Optional[dict] = None,
    error: Optional[str] = None,
) -> Optional[dict]:
    jobs = _ensure_store()
    for job in jobs:
        if job.get("id") == job_id:
            job["status"] = status
            job["updatedAt"] = int(time.time())
            if result is not None:
                job["result"] = result
            if error is not None:
                job["error"] = str(error)
            _save_store(jobs)
            return job
    return None


def list_jobs(limit: int = 50) -> list[dict]:
    jobs = _ensure_store()
    return jobs[:limit]


def list_recent(limit: int = 50) -> list[dict]:
    return list_jobs(limit=limit)


def clear_jobs() -> bool:
    _save_store([])
    return True


def clear_history() -> bool:
    return clear_jobs()


def fail_job(job_id: str, error: str) -> Optional[dict]:
    return update_job(job_id, status="failed", error=error)


def finish_job(
    job_id: str,
    video_id: str | None = None,
    post_id: str | None = None,
    permalink_url: str | None = None,
) -> Optional[dict]:
    res = {}
    if video_id:
        res["videoId"] = video_id
    if post_id:
        res["postId"] = post_id
    if permalink_url:
        res["permalinkUrl"] = permalink_url
    return update_job(job_id, status="succeeded", result=res)
