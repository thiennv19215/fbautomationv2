"""FBEM bridge — local Facebook native-composer upload bridge to a Chrome extension.

It bridges a Chrome MV3 extension over WebSocket (:9224) + HTTP callback (:47102)
and drives **native Facebook Reel / Photo uploads** that fire the same internal
web API the logged-in user's browser uses (NOT the Graph API), plus a **crawler**
that records the genuine native upload requests when the user manually posts — so
the replay is template-driven and self-healing.

What it exposes (all on 127.0.0.1):
  GET  /api/health         — bridge status (extension connected? templates?)
  POST /post-reel          — { videoUrl, caption, pageId? } → { ok, videoId, permalinkUrl }
  POST /post-photos        — { imageUrls[], caption, pageId? } → { ok, postId, photoIds, permalinkUrl }
  POST /switch-profile     — { targetId } → switch the acting page/profile
  GET  /api/current-identity — page/profile the tab currently posts AS
  POST /api/ext/callback   — extension POSTs responses here (secret-gated)
  POST /api/ext/capture    — extension POSTs recorded native requests here (secret-gated)
  GET  /api/template       — current captured template.json (debug)

Run:
  fbem-bridge            # or: python -m fbem.bridge
Then load the Chrome extension (extension/) and open a logged-in facebook.com tab.

This is a LOCAL CONTENT TOOL — loopback-only, never network-reachable.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import random
import shutil
import time
from contextlib import asynccontextmanager
from typing import Optional, Any
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi import Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import admin_store
from . import capture_store
from . import history_store
from . import pages_store
from ..bot import telegram_service as telegram_bot
from .config import WS_HOST, HTTP_PORT, media_dir
from .bridge_client import bridge_client
from .job_runner import run_dispatcher
from .ws_server import run_ws_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fbem.bridge")

if WS_HOST not in ("127.0.0.1", "localhost", "::1"):
    raise RuntimeError(
        f"FBEM_WS_HOST must be loopback (got {WS_HOST!r}); the extension WS is "
        "unauthenticated by design and must not be network-reachable."
    )


def cleanup_media_file(url_or_path: str, job_id: str | None = None) -> None:
    """Safely remove staged media file if no other active jobs reference it."""
    if not url_or_path:
        return
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url_or_path)
        filename = None
        if parsed.query:
            qs = parse_qs(parsed.query)
            if "name" in qs:
                filename = qs["name"][0]
        if not filename:
            filename = Path(parsed.path).name

        if not filename:
            return

        active_count = admin_store.count_active_jobs_with_media(filename, exclude_job_id=job_id)
        if active_count > 0:
            logger.info("Keep media file %s: referenced by %d other active job(s)", filename, active_count)
            return

        target_file = media_dir() / filename
        if target_file.is_file():
            target_file.unlink(missing_ok=True)
            logger.info("Cleaned up media file: %s", filename)
    except Exception as exc:
        logger.warning("Failed to cleanup media file %s: %s", url_or_path, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    admin_store.init_db()
    ws_task = asyncio.create_task(run_ws_server(), name="ext-ws-server")
    dispatcher_task = asyncio.create_task(run_dispatcher(), name="fbem-dispatcher")
    telegram_bot.start_bot_task()
    logger.info("fb-bridge started (ws:9224 + http:47102) with queue dispatcher. Waiting for the Chrome extension…")
    try:
        yield
    finally:
        telegram_bot.stop_bot_task()
        dispatcher_task.cancel()
        ws_task.cancel()
        try:
            await asyncio.gather(ws_task, dispatcher_task, return_exceptions=True)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        logger.info("fb-bridge stopped")


app = FastAPI(title="fbem-bridge", version="0.1.0", lifespan=lifespan)

# The crawler/replay run inside facebook.com page context, so their POSTs to the
# loopback sink are cross-origin and trigger a CORS preflight (OPTIONS). The
# server is loopback-only and every mutating route is secret-gated, so reflecting
# any origin is safe here and lets the preflight succeed.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_methods=["*"],
    allow_headers=["*"],
)

import sys

def _get_static_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "fbem" / "bridge" / "static"
        if bundled.exists():
            return bundled
    return Path(__file__).parent / "static"

_STATIC_DIR = _get_static_dir()
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse("/ui/")


class PostReelBody(BaseModel):
    videoUrl: str
    caption: str
    pageId: Optional[str] = None
    scheduledPublishTime: int | None = None
    extensionId: Optional[str] = None
    extension_id: Optional[str] = None

    @property
    def clean_extension_id(self) -> Optional[str]:
        return self.extensionId or self.extension_id

    @field_validator("scheduledPublishTime")
    @classmethod
    def _check_schedule(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 1_000_000_000 or v > 10_000_000_000:
            raise ValueError(
                f"scheduledPublishTime must be epoch SECONDS (got {v}; looks like ms or out of range)"
            )
        return v


class PostPhotosBody(BaseModel):
    imageUrls: list[str]
    caption: str
    pageId: Optional[str] = None
    scheduledPublishTime: int | None = None
    extensionId: Optional[str] = None
    extension_id: Optional[str] = None

    @property
    def clean_extension_id(self) -> Optional[str]:
        return self.extensionId or self.extension_id

    @field_validator("scheduledPublishTime")
    @classmethod
    def _check_schedule(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 1_000_000_000 or v > 10_000_000_000:
            raise ValueError(
                f"scheduledPublishTime must be epoch SECONDS (got {v}; looks like ms or out of range)"
            )
        return v


class SwitchProfileBody(BaseModel):
    targetId: str
    extensionId: Optional[str] = None
    extension_id: Optional[str] = None

    @property
    def clean_extension_id(self) -> Optional[str]:
        return self.extensionId or self.extension_id


class SavePageBody(BaseModel):
    id: str
    name: str
    extensionId: Optional[str] = None
    note: Optional[str] = ""


class StageLocalPathBody(BaseModel):
    localPath: str


class StageUrlBody(BaseModel):
    url: str
    filename: Optional[str] = None
    folder: Optional[str] = None


class TelegramConfigBody(BaseModel):
    token: str
    chatId: str
    enabled: bool = True
    autoPost: bool = True


class TelegramTestBody(BaseModel):
    token: str
    chatId: str


class AccountBody(BaseModel):
    name: str
    facebookId: Optional[str] = None
    facebook_id: Optional[str] = None
    extensionId: Optional[str] = None
    extension_id: Optional[str] = None
    accountType: Optional[str] = "page"
    account_type: Optional[str] = None
    parentId: Optional[str] = None
    parent_id: Optional[str] = None
    notes: Optional[str] = ""
    assignedFolder: Optional[str] = ""
    assigned_folder: Optional[str] = None
    defaultScriptId: Optional[str] = ""
    default_script_id: Optional[str] = None
    enabled: Optional[bool] = True


class ScriptBody(BaseModel):
    name: str
    description: Optional[str] = ""
    kind: str = "post_reel"
    config: Optional[dict] = None
    enabled: Optional[bool] = True


class EnqueueJobBody(BaseModel):
    accountId: Optional[str] = None
    account_id: Optional[str] = None
    extensionId: Optional[str] = None
    extension_id: Optional[str] = None
    scriptId: Optional[str] = None
    script_id: Optional[str] = None
    kind: str = "post_reel"
    input: Optional[dict] = None
    idempotencyKey: Optional[str] = None
    idempotency_key: Optional[str] = None
    runAt: Optional[int] = None
    run_at: Optional[int] = None
    delaySeconds: Optional[int] = None
    delay_seconds: Optional[int] = None


class BulkJobsBody(BaseModel):
    accountIds: list[str] = []
    account_ids: Optional[list[str]] = None
    scriptId: Optional[str] = None
    script_id: Optional[str] = None
    kind: str = "post_reel"
    input: Optional[dict] = None
    staggerSeconds: Optional[int] = None
    stagger_seconds: Optional[int] = None
    jitterSeconds: Optional[int] = None
    jitter_seconds: Optional[int] = None
    enableStagger: Optional[bool] = None
    enable_stagger: Optional[bool] = None


class QueueSettingsBody(BaseModel):
    staggerSeconds: Optional[int] = None
    stagger_seconds: Optional[int] = None
    jitterSeconds: Optional[int] = None
    jitter_seconds: Optional[int] = None
    autoStagger: Optional[bool] = None
    auto_stagger: Optional[bool] = None


class ScanPagesBody(BaseModel):
    extensionId: Optional[str] = None
    extension_id: Optional[str] = None


class FolderBody(BaseModel):
    name: str


_TAB_TTL_S = int(os.getenv("FBEM_TAB_TTL_S", "7200"))  # 2h


def _ttl_block(last_active_at: Optional[float]) -> dict:
    """Per-service freshness/TTL: anchored to the last (re)load. ttl_remaining_s
    counts down to the next auto-reload; stale=True once it elapses."""
    now = time.time()
    remaining = (
        max(0, int(last_active_at + _TAB_TTL_S - now)) if last_active_at is not None else 0
    )
    return {
        "last_active_at": int(last_active_at) if last_active_at is not None else None,
        "ttl_s": _TAB_TTL_S,
        "ttl_remaining_s": remaining,
        "stale": last_active_at is None or (now - last_active_at) > _TAB_TTL_S,
    }


@app.get("/api/extensions")
def list_extensions() -> dict:
    """List all currently connected Chrome extensions."""
    return {"items": bridge_client.list_extensions()}


@app.get("/api/health")
def health(extension_id: Optional[str] = None) -> dict:
    session = bridge_client.get_session(extension_id)
    scope = session.extension_id if session else extension_id
    tpl = capture_store.load_template(scope)
    capture = capture_store.capture_stats(scope)
    last_act = session.last_active_at if session else bridge_client.last_active_at
    actives = [t for t in (last_act, capture["last_capture_at"]) if t]
    ttl = _ttl_block(max(actives) if actives else None)
    return {
        "ok": True,
        "extension_connected": bridge_client.connected,
        "extension_count": bridge_client.extension_count,
        "extensions": bridge_client.list_extensions(),
        "fb_user": session.fb_user if session else bridge_client.fb_user,
        "tab_active": capture["tab_active"],
        "last_capture_at": capture["last_capture_at"],
        "captures": capture["captures"],
        "last_active_at": ttl["last_active_at"],
        "ttl_s": ttl["ttl_s"],
        "ttl_remaining_s": ttl["ttl_remaining_s"],
        "stale": ttl["stale"],
        "has_template": capture_store.template_complete(tpl),
        "has_photo_template": capture_store.photo_template_complete(tpl),
        "capture": capture,
        "ws_stats": bridge_client.ws_stats,
    }


@app.post("/post-reel")
async def post_reel(body: PostReelBody) -> dict:
    """Publish a native Facebook Reel via the extension. 503 if the extension
    isn't connected; 502 if no template has been captured yet (user must
    real-play one manual upload to seed it)."""
    if not bridge_client.connected:
        raise HTTPException(
            status_code=503,
            detail="⚠️ Chưa kết nối Chrome Extension. Hãy mở trình duyệt Chrome đã cài đặt tiện ích FBEM và vào trang facebook.com!",
        )
    if not body.videoUrl.strip():
        raise HTTPException(status_code=400, detail="Đường dẫn video (videoUrl) không được để trống")

    ext_id = body.clean_extension_id
    template = capture_store.load_template(ext_id)
    if not capture_store.template_complete(template):
        raise HTTPException(
            status_code=502,
            detail="⚠️ Chưa có Mẫu Đăng Reel (no_template_captured). Bạn hãy mở tab Chrome facebook.com và đăng tay 1 Reel bất kỳ để hệ thống tự động học mẫu gói tin nhé!",
        )

    job = history_store.add_job("post_reel", body.model_dump(), extension_id=ext_id, page_id=body.pageId, caption=body.caption)
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if body.pageId else None

    try:
        resp = await bridge_client.post_reel(
            video_url=body.videoUrl.strip(),
            caption=body.caption,
            page_id=body.pageId,
            template=template,
            scheduled_publish_time=body.scheduledPublishTime,
            switch_template=switch_tpl,
            extension_id=ext_id,
        )
    except Exception as exc:
        history_store.fail_job(job["id"], str(exc))
        raise

    if resp.get("error"):
        history_store.fail_job(job["id"], str(resp["error"]))
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])

    data = resp.get("data") or {}
    history_store.finish_job(
        job["id"],
        video_id=data.get("videoId"),
        permalink_url=data.get("permalinkUrl"),
    )
    cleanup_media_file(body.videoUrl.strip(), job_id=job["id"])
    return {
        "ok": True,
        "videoId": data.get("videoId"),
        "permalinkUrl": data.get("permalinkUrl"),
    }


@app.post("/post-photos")
async def post_photos(body: PostPhotosBody) -> dict:
    """Publish a photo or photo-album via the extension."""
    if not bridge_client.connected:
        raise HTTPException(
            status_code=503,
            detail="⚠️ Chưa kết nối Chrome Extension. Hãy mở trình duyệt Chrome đã cài đặt tiện ích FBEM và vào trang facebook.com!",
        )
    if not body.imageUrls:
        raise HTTPException(status_code=400, detail="empty_imageUrls")

    ext_id = body.clean_extension_id
    template = capture_store.load_template(ext_id)
    if not capture_store.photo_template_complete(template):
        raise HTTPException(
            status_code=502,
            detail="⚠️ Chưa có Mẫu Đăng Ảnh (no_photo_template_captured). Bạn hãy mở tab Chrome facebook.com và đăng tay 1 bài Ảnh bất kỳ để hệ thống tự động học mẫu nhé!",
        )

    job = history_store.add_job("post_photos", body.model_dump(), extension_id=ext_id, page_id=body.pageId, caption=body.caption)
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if body.pageId else None

    try:
        resp = await bridge_client.post_photos(
            image_urls=body.imageUrls,
            caption=body.caption,
            page_id=body.pageId,
            template=template,
            scheduled_publish_time=body.scheduledPublishTime,
            switch_template=switch_tpl,
            extension_id=ext_id,
        )
    except Exception as exc:
        history_store.fail_job(job["id"], str(exc))
        raise

    if resp.get("error"):
        history_store.fail_job(job["id"], str(resp["error"]))
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])

    data = resp.get("data") or {}
    history_store.finish_job(
        job["id"],
        post_id=data.get("postId"),
        permalink_url=data.get("permalinkUrl"),
    )
    for u in body.imageUrls:
        cleanup_media_file(u, job_id=job["id"])
    return {
        "ok": True,
        "postId": data.get("postId"),
        "photoIds": data.get("photoIds") or [],
        "permalinkUrl": data.get("permalinkUrl"),
    }


@app.get("/api/accounts")
def list_accounts() -> dict:
    """List all accounts / pages."""
    return {"items": admin_store.list_rows("accounts")}


@app.post("/api/accounts")
def create_account(body: AccountBody) -> dict:
    """Create or update an account/page."""
    fb_id = body.facebookId or body.facebook_id
    if not fb_id:
        raise HTTPException(status_code=400, detail="facebookId is required")
    row = admin_store.save_account({
        "name": body.name,
        "facebookId": fb_id,
        "extensionId": body.extensionId or body.extension_id,
        "accountType": body.accountType or body.account_type or "page",
        "parentId": body.parentId or body.parent_id,
        "notes": body.notes or "",
        "assignedFolder": body.assignedFolder or body.assigned_folder or "",
        "defaultScriptId": body.defaultScriptId or body.default_script_id or "",
        "enabled": True if body.enabled is None else body.enabled,
    })
    return {"ok": True, "account": row}


@app.put("/api/accounts/{account_id}")
def update_account(account_id: str, body: AccountBody) -> dict:
    """Update existing account."""
    row = admin_store.get_row("accounts", account_id)
    if not row:
        raise HTTPException(status_code=404, detail="account_not_found")
    fb_id = body.facebookId or body.facebook_id or row.get("facebook_id")
    updated = admin_store.save_account({
        "id": account_id,
        "name": body.name,
        "facebookId": fb_id,
        "extensionId": body.extensionId or body.extension_id or row.get("extension_id"),
        "accountType": body.accountType or body.account_type or row.get("account_type"),
        "parentId": body.parentId or body.parent_id or row.get("parent_id"),
        "notes": body.notes if body.notes is not None else row.get("notes"),
        "assignedFolder": body.assignedFolder if body.assignedFolder is not None else row.get("assigned_folder"),
        "defaultScriptId": body.defaultScriptId if body.defaultScriptId is not None else row.get("default_script_id"),
        "enabled": body.enabled if body.enabled is not None else row.get("enabled", 1),
    })
    return {"ok": True, "account": updated}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: str) -> dict:
    """Delete an account."""
    admin_store.delete_account(account_id)
    return {"ok": True}


@app.get("/api/scripts")
def list_scripts() -> dict:
    """List all scripts."""
    return {"items": admin_store.list_rows("scripts")}


@app.post("/api/scripts")
def create_script(body: ScriptBody) -> dict:
    """Create or update a script."""
    row = admin_store.save_script({
        "name": body.name,
        "description": body.description or "",
        "kind": body.kind,
        "config": body.config or {},
        "enabled": True if body.enabled is None else body.enabled,
    })
    return {"ok": True, "script": row}


@app.put("/api/scripts/{script_id}")
def update_script(script_id: str, body: ScriptBody) -> dict:
    """Update existing script."""
    row = admin_store.get_row("scripts", script_id)
    if not row:
        raise HTTPException(status_code=404, detail="script_not_found")
    updated = admin_store.save_script({
        "id": script_id,
        "name": body.name,
        "description": body.description if body.description is not None else row.get("description"),
        "kind": body.kind or row.get("kind"),
        "config": body.config if body.config is not None else row.get("config"),
        "enabled": body.enabled if body.enabled is not None else row.get("enabled", 1),
    })
    return {"ok": True, "script": updated}


@app.delete("/api/scripts/{script_id}")
def delete_script(script_id: str) -> dict:
    """Delete a script."""
    admin_store.delete_row("scripts", script_id)
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs(status: Optional[str] = None, limit: int = 100) -> dict:
    """List jobs from SQLite queue."""
    return {"items": admin_store.list_jobs(status=status, limit=limit)}


@app.get("/api/settings/queue")
def get_queue_settings() -> dict:
    """Get queue anti-spam stagger & delay settings."""
    return admin_store.get_queue_settings()


@app.post("/api/settings/queue")
def save_queue_settings(body: QueueSettingsBody) -> dict:
    """Save queue anti-spam stagger & delay settings."""
    settings = {}
    if body.staggerSeconds is not None:
        settings["staggerSeconds"] = max(0, body.staggerSeconds)
    elif body.stagger_seconds is not None:
        settings["staggerSeconds"] = max(0, body.stagger_seconds)

    if body.jitterSeconds is not None:
        settings["jitterSeconds"] = max(0, body.jitterSeconds)
    elif body.jitter_seconds is not None:
        settings["jitterSeconds"] = max(0, body.jitter_seconds)

    if body.autoStagger is not None:
        settings["autoStagger"] = bool(body.autoStagger)
    elif body.auto_stagger is not None:
        settings["autoStagger"] = bool(body.auto_stagger)

    return admin_store.save_queue_settings(settings)


@app.post("/api/jobs")
def enqueue_job(body: EnqueueJobBody) -> dict:
    """Enqueue a job with optional idempotency key and scheduled run_at."""
    acc_id = body.accountId or body.account_id
    account = admin_store.get_row("accounts", acc_id) if acc_id else None
    if not account:
        raise HTTPException(status_code=404, detail="account_not_found")

    now = int(time.time())
    run_at = body.runAt if body.runAt is not None else body.run_at
    if run_at is None and (body.delaySeconds or body.delay_seconds):
        delay = body.delaySeconds or body.delay_seconds or 0
        run_at = now + delay

    job = admin_store.create_job(
        account=account,
        kind=body.kind,
        payload=body.input or {},
        script_id=body.scriptId or body.script_id,
        idempotency_key=body.idempotencyKey or body.idempotency_key,
        run_at=run_at,
    )
    return {"ok": True, "job": job}


@app.post("/api/jobs/bulk")
def enqueue_bulk_jobs(body: BulkJobsBody) -> dict:
    """Enqueue jobs across multiple accounts with anti-spam stagger delay."""
    account_ids = body.accountIds or body.account_ids or []
    queue_cfg = admin_store.get_queue_settings()

    stagger_enabled = (
        body.enableStagger if body.enableStagger is not None
        else (body.enable_stagger if body.enable_stagger is not None else queue_cfg.get("autoStagger", True))
    )

    stagger_sec = (
        body.staggerSeconds if body.staggerSeconds is not None
        else (body.stagger_seconds if body.stagger_seconds is not None else queue_cfg.get("staggerSeconds", 30))
    )
    jitter_sec = (
        body.jitterSeconds if body.jitterSeconds is not None
        else (body.jitter_seconds if body.jitter_seconds is not None else queue_cfg.get("jitterSeconds", 10))
    )

    now = int(time.time())
    jobs = []
    accumulated_delay = 0

    for idx, acc_id in enumerate(account_ids):
        account = admin_store.get_row("accounts", acc_id)
        if not account:
            continue

        if idx == 0 or not stagger_enabled:
            job_run_at = now
        else:
            jitter = random.randint(0, max(0, jitter_sec)) if jitter_sec > 0 else 0
            accumulated_delay += stagger_sec + jitter
            job_run_at = now + accumulated_delay

        job = admin_store.create_job(
            account=account,
            kind=body.kind,
            payload=body.input or {},
            script_id=body.scriptId or body.script_id,
            run_at=job_run_at,
        )
        jobs.append(job)
    return {"ok": True, "count": len(jobs), "jobs": jobs}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Cancel a queued job."""
    res = admin_store.cancel_job(job_id)
    if not res:
        raise HTTPException(status_code=400, detail="Cannot cancel job (may already be running or finished)")
    return {"ok": True, "job": res}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    """Retry a failed or canceled job."""
    res = admin_store.retry_job(job_id)
    if not res:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": res}


@app.get("/api/stats")
def dashboard_stats() -> dict:
    """Get dashboard stats."""
    return admin_store.dashboard_stats()


@app.get("/api/history")
def get_history(limit: int = 100) -> dict:
    """Return unified history of all post jobs from both SQLite and history.json."""
    sqlite_jobs = admin_store.list_jobs(limit=limit)
    file_jobs = history_store.list_jobs(limit=limit)

    seen_ids = set()
    combined = []

    # Map SQLite jobs
    for j in sqlite_jobs:
        job_id = j.get("id")
        seen_ids.add(job_id)
        input_data = j.get("input") or {}
        result_data = j.get("result") or {}
        combined.append({
            "id": job_id,
            "kind": j.get("kind", "post_reel"),
            "extensionId": j.get("extension_id"),
            "pageId": input_data.get("pageId") or j.get("account_id"),
            "caption": input_data.get("caption") or "",
            "status": j.get("status", "queued"),
            "runAt": j.get("run_at") or j.get("created_at") or int(time.time()),
            "createdAt": j.get("created_at") or int(time.time()),
            "updatedAt": j.get("updated_at") or int(time.time()),
            "result": result_data,
            "error": j.get("error"),
        })

    # Map JSON file jobs (if not already mapped)
    for j in file_jobs:
        if j.get("id") not in seen_ids:
            seen_ids.add(j.get("id"))
            if "runAt" not in j:
                j["runAt"] = j.get("createdAt") or int(time.time())
            combined.append(j)

    combined.sort(key=lambda x: x.get("createdAt", 0), reverse=True)
    return {"items": combined[:limit]}


@app.post("/api/history/clear")
@app.delete("/api/history")
def clear_history() -> dict:
    """Clear all stored job history."""
    history_store.clear_jobs()
    return {"ok": True}


@app.get("/api/media")
def list_media() -> dict:
    """List staged media files and folder hierarchy."""
    base = media_dir()
    base.mkdir(parents=True, exist_ok=True)
    files = []
    folders = []

    for entry in sorted(base.rglob("*")):
        if entry.is_dir():
            rel = entry.relative_to(base).as_posix()
            folders.append({"name": entry.name, "path": rel})
        elif entry.is_file() and entry.suffix.lower() in (".mp4", ".mov", ".jpg", ".jpeg", ".png"):
            rel = entry.relative_to(base).as_posix()
            is_vid = entry.suffix.lower() in (".mp4", ".mov")
            route = "local-video" if is_vid else "local-image"
            files.append({
                "name": entry.name,
                "path": rel,
                "sizeBytes": entry.stat().st_size,
                "modifiedAt": int(entry.stat().st_mtime),
                "mediaType": "video" if is_vid else "image",
                "url": f"http://127.0.0.1:{HTTP_PORT}/{route}?name={quote(rel)}",
            })
    return {"files": files, "folders": folders}


@app.post("/api/media/upload")
async def upload_media_file(file: UploadFile = File(...), folder: Optional[str] = Form(None)) -> dict:
    """Upload a media file to the media directory."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing_filename")

    out_dir = media_dir()
    if folder:
        clean_folder = Path(folder.strip("/\\")).name
        out_dir = out_dir / clean_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    dest = out_dir / file.filename
    idx = 0
    while dest.exists():
        idx += 1
        dest = out_dir / f"{Path(file.filename).stem}_{idx}{Path(file.filename).suffix}"

    with open(dest, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    ext = dest.suffix.lower()
    is_video = ext in (".mp4", ".mov")
    route = "local-video" if is_video else "local-image"
    url = f"http://127.0.0.1:{HTTP_PORT}/{route}?name={quote(dest.name)}"

    return {
        "ok": True,
        "filename": dest.name,
        "mediaType": "video" if is_video else "image",
        "url": url,
        "sizeBytes": dest.stat().st_size,
    }


@app.post("/api/stage-url")
async def stage_url(body: StageUrlBody) -> dict:
    """Download a video/image directly from a public URL into the media directory."""
    raw_url = body.url.strip()
    if not raw_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL không hợp lệ. Vui lòng nhập link bắt đầu bằng http:// hoặc https://")

    out_dir = media_dir()
    if body.folder:
        clean_folder = Path(body.folder.strip("/\\")).name
        out_dir = out_dir / clean_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = ".mp4"
    if ".jpg" in raw_url.lower() or ".jpeg" in raw_url.lower():
        ext = ".jpg"
    elif ".png" in raw_url.lower():
        ext = ".png"
    elif ".mov" in raw_url.lower():
        ext = ".mov"

    clean_name = body.filename.strip() if body.filename else f"dl_{int(time.time())}_{abs(hash(raw_url)) % 100000}{ext}"
    if not clean_name.endswith(ext):
        clean_name += ext

    dest = out_dir / clean_name
    idx = 0
    while dest.exists():
        idx += 1
        dest = out_dir / f"{Path(clean_name).stem}_{idx}{ext}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True, trust_env=False, headers=headers) as client:
            async with client.stream("GET", raw_url) as response:
                if response.status_code not in (200, 206):
                    raise HTTPException(status_code=400, detail=f"Không thể tải file từ URL (mã HTTP {response.status_code})")
                
                # Dynamic extension from Content-Type if available
                ct = response.headers.get("content-type", "").lower()
                if "video" in ct and ext not in (".mp4", ".mov"):
                    ext = ".mp4"
                elif "image" in ct and ext not in (".jpg", ".jpeg", ".png"):
                    ext = ".jpg"

                with open(dest, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi tải file từ URL: {str(exc)}")

    is_video = ext in (".mp4", ".mov")
    route = "local-video" if is_video else "local-image"
    url = f"http://127.0.0.1:{HTTP_PORT}/{route}?name={quote(dest.name)}"

    return {
        "ok": True,
        "filename": dest.name,
        "mediaType": "video" if is_video else "image",
        "url": url,
        "sizeBytes": dest.stat().st_size,
    }


@app.delete("/api/media/files")
def delete_media_files(payload: dict) -> dict:
    """Delete specified media files."""
    paths = payload.get("paths", [])
    base = media_dir()
    deleted = 0
    for rel in paths:
        target = (base / rel).resolve()
        if target.is_relative_to(base) and target.is_file():
            target.unlink(missing_ok=True)
            deleted += 1
    return {"ok": True, "deleted": deleted}


@app.post("/api/media/folders")
def create_media_folder(body: FolderBody) -> dict:
    """Create a subfolder in the media directory."""
    clean = Path(body.name.strip("/\\")).name
    if not clean:
        raise HTTPException(status_code=400, detail="invalid_folder_name")
    target = media_dir() / clean
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "folder": clean}


@app.delete("/api/media/folders")
def delete_media_folder(payload: dict) -> dict:
    """Delete a subfolder in the media directory."""
    name = payload.get("name", "").strip("/\\")
    clean = Path(name).name
    if not clean:
        raise HTTPException(status_code=400, detail="invalid_folder_name")
    target = media_dir() / clean
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    return {"ok": True}


@app.get("/api/extensions/{extension_id}/template-status")
def extension_template_status(extension_id: str) -> dict:
    """Check template readiness for a specific extension."""
    tpl = capture_store.load_template(extension_id)
    return {
        "extensionId": extension_id,
        "hasTemplate": capture_store.template_complete(tpl),
        "hasPhotoTemplate": capture_store.photo_template_complete(tpl),
    }


@app.get("/api/extensions/{extension_id}/identity")
async def extension_identity(extension_id: str) -> dict:
    """Get active Facebook user identity for a specific extension."""
    session = bridge_client.get_session(extension_id)
    resp = await bridge_client.get_identity(extension_id=extension_id)
    if resp.get("error"):
        if session and session.fb_user:
            return session.fb_user
        return {"id": None, "name": None}
    data = resp.get("data") or {}
    p_id = data.get("identityId")
    p_name = data.get("identityName")
    if p_id:
        effective_name = p_name or f"Fanpage {p_id}"
        pages_store.save_page(p_id, effective_name, extension_id=extension_id)
        if session:
            session.fb_user = {"id": p_id, "name": effective_name}
        return {"id": p_id, "name": effective_name}
    return {"id": p_id, "name": p_name}


@app.post("/api/scan-pages")
async def scan_pages(body: ScanPagesBody) -> dict:
    ext_id = body.extensionId or body.extension_id
    session = bridge_client.get_session(ext_id)
    if not session:
        raise HTTPException(status_code=503, detail="extension_not_connected")

    resp = await bridge_client.get_identity(extension_id=ext_id)
    found = 0
    created = 0
    if not resp.get("error"):
        data = resp.get("data") or {}
        p_id = data.get("identityId")
        p_name = data.get("identityName") or f"Fanpage {p_id}"
        if p_id:
            found = 1
            pages_store.save_page(p_id, p_name, extension_id=ext_id)
            existing = [a for a in admin_store.list_rows("accounts") if a.get("facebook_id") == p_id]
            if not existing:
                admin_store.save_account({
                    "name": p_name,
                    "facebookId": p_id,
                    "extensionId": ext_id,
                    "accountType": "page",
                    "enabled": True,
                })
                created = 1
    return {"ok": True, "found": found, "created": created}


@app.get("/api/pages")
def list_pages() -> dict:
    """List saved Fanpages."""
    return {"items": pages_store.list_pages()}


@app.post("/api/pages")
def save_page(body: SavePageBody) -> dict:
    """Save or update a Fanpage in the persistent store."""
    page = pages_store.save_page(
        page_id=body.id,
        name=body.name,
        extension_id=body.extensionId,
        note=body.note or "",
    )
    return {"ok": True, "page": page}


@app.delete("/api/pages/{page_id}")
def delete_page(page_id: str) -> dict:
    """Remove a saved Fanpage."""
    pages_store.delete_page(page_id)
    return {"ok": True}


@app.post("/api/stage-local-path")
def stage_local_path(body: StageLocalPathBody) -> dict:
    """Stage a local file directly from computer path into media dir."""
    raw = body.localPath.strip().strip('"').strip("'")
    p = Path(raw).expanduser().resolve()
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file_not_found: {p}")

    ext = p.suffix.lower()
    if ext not in (".mp4", ".mov", ".jpg", ".jpeg", ".png"):
        raise HTTPException(status_code=400, detail=f"unsupported_format_{ext}")

    out_dir = media_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    dest = out_dir / p.name
    if p != dest:
        shutil.copy2(p, dest)

    is_video = ext in (".mp4", ".mov")
    route = "local-video" if is_video else "local-image"
    url = f"http://127.0.0.1:{HTTP_PORT}/{route}?name={quote(dest.name)}"

    return {
        "ok": True,
        "filename": dest.name,
        "mediaType": "video" if is_video else "image",
        "url": url,
        "sizeBytes": dest.stat().st_size,
    }


@app.get("/api/telegram/config")
def get_telegram_config() -> dict:
    """Get current Telegram bot settings."""
    return telegram_bot.get_config()


@app.post("/api/telegram/config")
def set_telegram_config(body: TelegramConfigBody) -> dict:
    """Save Telegram bot settings and start/restart polling."""
    cfg = telegram_bot.save_config(
        token=body.token,
        chat_id=body.chatId,
        enabled=body.enabled,
        auto_post=body.autoPost,
    )
    if body.enabled and body.token:
        telegram_bot.start_bot_task()
    else:
        telegram_bot.stop_bot_task()
    return {"ok": True, "config": cfg}


@app.post("/api/telegram/test")
async def test_telegram(body: TelegramTestBody) -> dict:
    """Send a test message to Telegram."""
    return await telegram_bot.test_connection(token=body.token, chat_id=body.chatId)


@app.post("/switch-profile")
async def switch_profile(body: SwitchProfileBody) -> dict:
    """Switch the browser session to a target profile/page id (then reload). After
    this, posts go out AS that page. 503 if the extension isn't connected."""
    if not bridge_client.connected:
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    if not body.targetId.strip():
        raise HTTPException(status_code=400, detail="empty_targetId")
    ext_id = body.clean_extension_id
    template = capture_store.load_template(ext_id) or {}
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation")
    resp = await bridge_client.switch_profile(body.targetId.strip(), switch_tpl, extension_id=ext_id)
    if resp.get("error"):
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])
    data = resp.get("data") or {}
    return {
        "ok": True,
        "identityId": data.get("identityId"),
        "identityName": data.get("identityName"),
    }


@app.get("/api/current-identity")
async def current_identity(extension_id: Optional[str] = None) -> dict:
    """The page/profile the FB tab currently posts AS (read-only — no switch).
    Used to pre-fill the dashboard 'add page' form."""
    if not bridge_client.connected:
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    session = bridge_client.get_session(extension_id)
    resp = await bridge_client.get_identity(extension_id=extension_id)
    if resp.get("error"):
        if session and session.fb_user:
            return session.fb_user
        return {"id": None, "name": None}
    data = resp.get("data") or {}
    p_id = data.get("identityId")
    p_name = data.get("identityName")
    if p_id:
        ext_id = session.extension_id if session else extension_id
        effective_name = p_name or f"Fanpage {p_id}"
        pages_store.save_page(p_id, effective_name, extension_id=ext_id)
        if session:
            session.fb_user = {"id": p_id, "name": effective_name}
        return {"id": p_id, "name": effective_name}
    return {"id": p_id, "name": p_name}


@app.post("/api/ext/callback")
async def ext_callback(
    body: FastAPIRequest,
    x_callback_secret: str | None = Header(default=None, alias="X-Callback-Secret"),
    x_extension_id: str | None = Header(default=None, alias="X-Extension-Id"),
) -> dict:
    """The extension POSTs its responses here, secret-gated."""
    if not x_callback_secret or not hmac.compare_digest(
        x_callback_secret, bridge_client.callback_secret
    ):
        raise HTTPException(status_code=401, detail="invalid callback secret")
    try:
        payload = await body.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")
    if not isinstance(payload, dict) or "id" not in payload:
        raise HTTPException(status_code=400, detail="missing id")
    if x_extension_id and "extensionId" not in payload:
        payload["extensionId"] = x_extension_id
    return {"ok": bridge_client.resolve_callback(payload)}


@app.post("/api/ext/capture")
async def ext_capture(
    body: FastAPIRequest,
    x_callback_secret: str | None = Header(default=None, alias="X-Callback-Secret"),
    x_extension_id: str | None = Header(default=None, alias="X-Extension-Id"),
) -> dict:
    """The crawler POSTs recorded native upload requests here, secret-gated."""
    if not x_callback_secret or not hmac.compare_digest(
        x_callback_secret, bridge_client.callback_secret
    ):
        raise HTTPException(status_code=401, detail="invalid callback secret")
    try:
        payload = await body.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="capture body must be an object")
    ext_id = x_extension_id or payload.get("extensionId")
    await asyncio.to_thread(capture_store.save_capture, payload, extension_id=ext_id)
    return {"ok": True}


_VIDEO_DIR = media_dir()
_IMAGE_DIR = media_dir()


@app.get("/local-video")
def local_video(name: str) -> FileResponse:
    """Serve a locally-rendered mp4 to the extension over loopback, so the
    page-context fetch avoids cross-origin CORS. Basename-only (no traversal);
    .mp4 only; restricted to the FBEM media dir."""
    p = _VIDEO_DIR / Path(name).name
    if p.suffix.lower() != ".mp4" or not p.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(str(p), media_type="video/mp4")


_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


@app.get("/local-image")
def local_image(name: str) -> FileResponse:
    """Serve a locally-rendered image to the extension over loopback, so the
    page-context fetch avoids cross-origin CORS. Basename-only (no traversal);
    jpg/png only; restricted to the FBEM media dir."""
    p = _IMAGE_DIR / Path(name).name
    media = _IMAGE_TYPES.get(p.suffix.lower())
    if not media or not p.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(str(p), media_type=media)


@app.get("/api/template")
def get_template(extension_id: Optional[str] = None) -> dict:
    """Return the current captured template (debug). Empty object if none yet."""
    return capture_store.load_template(extension_id) or {}


@app.post("/api/launch-profiles")
def launch_profiles() -> dict:
    """Launch all detected Chrome profiles in silent minimized background mode."""
    from . import chrome_launcher
    launched = chrome_launcher.launch_all_profiles_background()
    return {"ok": True, "launched": launched}
