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
import time
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import capture_store
from .config import WS_HOST, media_dir
from .bridge_client import bridge_client
from .connection_registry import connection_registry, ExtensionSession
from .ws_server import run_ws_server
from .job_runner import run_dispatcher
from . import admin_store
from .text_utils import normalize_browser_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fbem.bridge")

if WS_HOST not in ("127.0.0.1", "localhost", "::1"):
    raise RuntimeError(
        f"FBEM_WS_HOST must be loopback (got {WS_HOST!r}); the extension WS is "
        "unauthenticated by design and must not be network-reachable."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_task = asyncio.create_task(run_ws_server(), name="ext-ws-server")
    job_task = asyncio.create_task(run_dispatcher(), name="job-dispatcher")
    logger.info("fb-bridge started (ws:9224 + http:47102). Waiting for the Chrome extension…")
    try:
        yield
    finally:
        ws_task.cancel()
        job_task.cancel()
        try:
            await ws_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        try:
            await job_task
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

_STATIC_DIR = Path(__file__).with_name("static")
app.mount("/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="dashboard")


@app.get("/", include_in_schema=False)
def dashboard() -> RedirectResponse:
    return RedirectResponse("/ui/")


def _session(extension_id: Optional[str] = None) -> ExtensionSession:
    session = connection_registry.get(extension_id) if extension_id else connection_registry.default()
    if session is None:
        detail = "extension_not_connected" if not connection_registry.list() else "extension_id_required"
        raise HTTPException(status_code=503 if not connection_registry.list() else 409, detail=detail)
    return session


@app.get("/api/extensions")
def list_extensions() -> dict:
    return {"items": connection_registry.list()}


class AccountBody(BaseModel):
    name: str
    facebookId: str
    extensionId: str
    enabled: bool = True


class ScriptBody(BaseModel):
    name: str
    description: str = ""
    kind: str
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class JobBody(BaseModel):
    accountId: str
    kind: str | None = None
    input: dict = Field(default_factory=dict)
    scriptId: str | None = None
    idempotencyKey: str | None = None


class BulkJobBody(BaseModel):
    accountIds: list[str]
    kind: str | None = None
    input: dict = Field(default_factory=dict)
    scriptId: str | None = None


@app.get("/api/accounts")
def list_accounts() -> dict:
    online = {item["id"]: item for item in connection_registry.list()}
    items = admin_store.list_rows("accounts")
    for item in items:
        item["extension"] = online.get(item["extension_id"])
    return {"items": items}


@app.post("/api/accounts")
def create_account(body: AccountBody) -> dict:
    if not body.name.strip() or not body.facebookId.strip() or not body.extensionId.strip():
        raise HTTPException(status_code=400, detail="name, facebookId and extensionId are required")
    return admin_store.save_account(body.model_dump())


@app.put("/api/accounts/{account_id}")
def update_account(account_id: str, body: AccountBody) -> dict:
    if not admin_store.get_row("accounts", account_id):
        raise HTTPException(status_code=404, detail="account_not_found")
    return admin_store.save_account(body.model_dump(), account_id)


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: str) -> dict:
    deleted, error = admin_store.delete_account(account_id)
    if not deleted:
        raise HTTPException(status_code=409 if error == "account_has_active_jobs" else 404, detail=error)
    return {"ok": True}


@app.get("/api/scripts")
def list_scripts() -> dict:
    return {"items": admin_store.list_rows("scripts")}


@app.post("/api/scripts")
def create_script(body: ScriptBody) -> dict:
    if body.kind not in {"post_reel", "post_photos", "switch_profile"}:
        raise HTTPException(status_code=400, detail="invalid_script_kind")
    return admin_store.save_script(body.model_dump())


@app.put("/api/scripts/{script_id}")
def update_script(script_id: str, body: ScriptBody) -> dict:
    if not admin_store.get_row("scripts", script_id):
        raise HTTPException(status_code=404, detail="script_not_found")
    return admin_store.save_script(body.model_dump(), script_id)


@app.delete("/api/scripts/{script_id}")
def delete_script(script_id: str) -> dict:
    if not admin_store.delete_script(script_id):
        raise HTTPException(status_code=404, detail="script_not_found")
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs() -> dict:
    return {"items": admin_store.list_rows("jobs")}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = admin_store.get_row("jobs", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


@app.post("/api/jobs")
def create_job(body: JobBody) -> dict:
    account = admin_store.get_row("accounts", body.accountId)
    if not account:
        raise HTTPException(status_code=404, detail="account_not_found")
    payload = dict(body.input)
    kind = body.kind
    if body.scriptId:
        script = admin_store.get_row("scripts", body.scriptId)
        if not script or not script.get("enabled"):
            raise HTTPException(status_code=404, detail="script_not_found_or_disabled")
        kind = script["kind"]
        payload = {**(script.get("config") or {}), **payload}
    if kind not in {"post_reel", "post_photos", "switch_profile", "get_identity"}:
        raise HTTPException(status_code=400, detail="invalid_job_kind")
    return admin_store.create_job(account, kind, payload, script_id=body.scriptId,
                                  idempotency_key=body.idempotencyKey)


@app.post("/api/jobs/bulk")
def create_bulk_jobs(body: BulkJobBody) -> dict:
    if not body.accountIds:
        raise HTTPException(status_code=400, detail="accountIds_required")
    script = admin_store.get_row("scripts", body.scriptId) if body.scriptId else None
    if body.scriptId and (not script or not script.get("enabled")):
        raise HTTPException(status_code=404, detail="script_not_found_or_disabled")
    kind = script["kind"] if script else body.kind
    if kind not in {"post_reel", "post_photos", "switch_profile", "get_identity"}:
        raise HTTPException(status_code=400, detail="invalid_job_kind")
    payload = {**((script or {}).get("config") or {}), **body.input}
    jobs, errors = [], []
    for account_id in dict.fromkeys(body.accountIds):
        account = admin_store.get_row("accounts", account_id)
        if not account or not account.get("enabled"):
            errors.append({"accountId": account_id, "error": "account_not_found_or_disabled"})
            continue
        jobs.append(admin_store.create_job(account, kind, payload, script_id=body.scriptId))
    return {"items": jobs, "errors": errors}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if not admin_store.cancel_job(job_id):
        raise HTTPException(status_code=409, detail="job_cannot_be_cancelled")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    if not admin_store.retry_job(job_id):
        raise HTTPException(status_code=409, detail="job_cannot_be_retried")
    return admin_store.get_row("jobs", job_id) or {"ok": True}


@app.get("/api/dashboard")
def dashboard_summary() -> dict:
    return {**admin_store.dashboard_stats(), "extensions": len(connection_registry.list())}


@app.get("/api/extensions/{extension_id}/template-status")
def extension_template_status(extension_id: str) -> dict:
    template = capture_store.load_template(extension_id) or {}
    stats = capture_store.capture_stats(extension_id)
    return {
        "extensionId": extension_id,
        "reel": capture_store.template_complete(template),
        "photo": capture_store.photo_template_complete(template),
        "switchProfile": bool((template.get("graphql_ops") or {}).get("CometProfileSwitchMutation")),
        "updatedAt": template.get("updatedAt"),
        "capture": stats,
    }


@app.get("/api/extensions/{extension_id}/identity")
async def extension_identity(extension_id: str) -> dict:
    session = _session(extension_id)
    response = await session.send("get_identity", {}, timeout=15.0)
    if response.get("error"):
        raise HTTPException(status_code=502, detail=str(response["error"])[:300])
    data = normalize_browser_text(response.get("data") or {})
    return {"id": data.get("identityId"), "name": data.get("identityName")}


class PostReelBody(BaseModel):
    videoUrl: str
    caption: str
    pageId: Optional[str] = None
    scheduledPublishTime: int | None = None
    extensionId: str | None = None

    @field_validator("scheduledPublishTime")
    @classmethod
    def _check_schedule(cls, v: int | None) -> int | None:
        if v is None:
            return v
        # Must be epoch SECONDS. Reject millisecond epochs (~1e12) and nonsense.
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
    extensionId: str | None = None

    @field_validator("scheduledPublishTime")
    @classmethod
    def _check_schedule(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v < 1_000_000_000 or v > 10_000_000_000:
            raise ValueError(
                f"scheduledPublishTime must be epoch SECONDS (got {v}; looks like ms or out of range)"
            )

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
    extensionId: str | None = None


# How long a tab stays "fresh" before it should be reloaded. The extension
# auto-reloads within this window so a healthy tab never goes stale.
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


@app.get("/api/health")
def health() -> dict:
    default_session = connection_registry.default()
    scope = default_session.extension_id if default_session else None
    tpl = capture_store.load_template(scope)
    capture = capture_store.capture_stats(scope)
    extension_items = connection_registry.list()
    actives = [item["lastActiveAt"] for item in extension_items]
    if capture["last_capture_at"]:
        actives.append(capture["last_capture_at"])
    ttl = _ttl_block(max(actives) if actives else None)
    return {
        "ok": True,
        "extension_connected": bool(extension_items),
        "extension_count": len(extension_items),
        "extensions": extension_items,
        "fb_user": default_session.fb_user if default_session else None,
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
        "ws_stats": {"connected": len(extension_items), "pending": sum(x["pending"] for x in extension_items)},
    }


def cleanup_media_file(url_or_path: str) -> None:
    """Delete a media file from media dirs once scheduled or posted successfully."""
    try:
        from urllib.parse import urlparse, parse_qs
        filename = None
        if "name=" in url_or_path:
            qs = parse_qs(urlparse(url_or_path).query)
            filename = qs.get("name", [None])[0]
        if not filename:
            filename = Path(url_or_path).name

        if filename:
            p1 = _VIDEO_DIR / filename
            if p1.is_file():
                p1.unlink(missing_ok=True)
                logger.info("Deleted posted media file from media_dir: %s", p1)

            p2 = Path("media").resolve() / filename
            if p2.is_file():
                p2.unlink(missing_ok=True)
                logger.info("Deleted posted media file from workspace media: %s", p2)

        p3 = Path(url_or_path).expanduser().resolve()
        if p3.is_file():
            p3.unlink(missing_ok=True)
            logger.info("Deleted source media file: %s", p3)
    except Exception as e:
        logger.warning("Error cleaning up media file %s: %s", url_or_path, e)


@app.post("/post-reel")
async def post_reel(body: PostReelBody) -> dict:
    """Publish a native Facebook Reel via the extension. 503 if the extension
    isn't connected; 502 if no template has been captured yet (user must
    real-play one manual upload to seed it)."""
    if not bridge_client.connected:
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    if not body.videoUrl.strip():
        raise HTTPException(status_code=400, detail="empty_videoUrl")

    template = capture_store.load_template()
    if not capture_store.template_complete(template):
        raise HTTPException(
            status_code=502,
            detail="no_template_captured — manually post one Reel on facebook.com to seed the "
            "crawler (need BOTH the rupload video-upload and the publish mutation)",
        )

    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if body.pageId else None
    resp = await bridge_client.post_reel(
        video_url=body.videoUrl.strip(),
        caption=body.caption,
        page_id=body.pageId,
        template=template,
        scheduled_publish_time=body.scheduledPublishTime,
        switch_template=switch_tpl,
    )
    if resp.get("error"):
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])

    data = resp.get("data") or {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="invalid_response_data")

    cleanup_media_file(body.videoUrl.strip())

    return {
        "ok": True,
        "videoId": data.get("videoId"),
        "permalinkUrl": data.get("permalinkUrl"),
    }


@app.post("/post-photos")
async def post_photos(body: PostPhotosBody) -> dict:
    """Publish a native Facebook photo / album post via the extension. One image
    url = a single photo; many = a multi-photo album (e.g. a comic strip).
    503 if the extension isn't connected; 502 if no photo template captured yet."""
    if not bridge_client.connected:
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    urls = [u.strip() for u in body.imageUrls if u and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="empty_imageUrls")

    template = capture_store.load_template()
    if not capture_store.photo_template_complete(template):
        raise HTTPException(
            status_code=502,
            detail="no_photo_template_captured — manually post one photo (and one album) on "
            "facebook.com to seed the crawler (need the ComposerStoryCreateMutation with photo attachments)",
        )

    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if body.pageId else None
    resp = await bridge_client.post_photos(
        image_urls=urls,
        caption=body.caption,
        page_id=body.pageId,
        template=template,
        scheduled_publish_time=body.scheduledPublishTime,
        switch_template=switch_tpl,
    )
    if resp.get("error"):
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])

    data = resp.get("data") or {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="invalid_response_data")

    for u in urls:
        cleanup_media_file(u)

    return {
        "ok": True,
        "postId": data.get("postId"),
        "photoIds": data.get("photoIds"),
        "permalinkUrl": data.get("permalinkUrl"),
    }


@app.post("/switch-profile")
async def switch_profile(body: SwitchProfileBody) -> dict:
    """Switch the browser session to a target profile/page id (then reload). After
    this, posts go out AS that page. 503 if the extension isn't connected."""
    if not bridge_client.connected:
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    if not body.targetId.strip():
        raise HTTPException(status_code=400, detail="empty_targetId")
    template = capture_store.load_template() or {}
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation")
    resp = await bridge_client.switch_profile(body.targetId.strip(), switch_tpl)
    if resp.get("error"):
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])
    data = resp.get("data") or {}
    return {
        "ok": True,
        "identityId": data.get("identityId"),
        "identityName": data.get("identityName"),
    }


@app.get("/api/current-identity")
async def current_identity() -> dict:
    """The page/profile the FB tab currently posts AS (read-only — no switch).
    Used to pre-fill the dashboard 'add page' form. 503 if the extension isn't
    connected; 502 if the page couldn't read its identity."""
    if not bridge_client.connected:
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    resp = await bridge_client.get_identity()
    if resp.get("error"):
        raise HTTPException(status_code=502, detail=str(resp["error"])[:300])
    data = resp.get("data") or {}
    return normalize_browser_text({"id": data.get("identityId"), "name": data.get("identityName")})


@app.post("/api/ext/callback")
async def ext_callback(
    body: FastAPIRequest,
    x_callback_secret: str | None = Header(default=None, alias="X-Callback-Secret"),
    x_extension_id: str | None = Header(default=None, alias="X-Extension-Id"),
) -> dict:
    """The extension POSTs its responses here, secret-gated."""
    session = connection_registry.get(x_extension_id or "") or (
        connection_registry.by_secret(x_callback_secret or "") if x_callback_secret else None
    )
    if not session or not x_callback_secret or not hmac.compare_digest(x_callback_secret, session.callback_secret):
        raise HTTPException(status_code=401, detail="invalid callback secret")
    try:
        payload = await body.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")
    if not isinstance(payload, dict) or "id" not in payload:
        raise HTTPException(status_code=400, detail="missing id")
    resolved = session.resolve(payload)
    if not resolved:
        resolved = bridge_client.resolve_callback(payload)
    return {"ok": resolved}


@app.post("/api/ext/capture")
async def ext_capture(
    body: FastAPIRequest,
    x_callback_secret: str | None = Header(default=None, alias="X-Callback-Secret"),
    x_extension_id: str | None = Header(default=None, alias="X-Extension-Id"),
) -> dict:
    """The crawler POSTs recorded native upload requests here, secret-gated."""
    session = connection_registry.get(x_extension_id or "") or (
        connection_registry.by_secret(x_callback_secret or "") if x_callback_secret else None
    )
    if not session or not x_callback_secret or not hmac.compare_digest(x_callback_secret, session.callback_secret):
        raise HTTPException(status_code=401, detail="invalid callback secret")
    try:
        payload = await body.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="capture body must be an object")
    await asyncio.to_thread(capture_store.save_capture, payload, session.extension_id)
    if len(connection_registry.list()) == 1:
        await asyncio.to_thread(capture_store.save_capture, payload)
    return {"ok": True}


# Reels and images both live in the one FBEM media dir (FBEM_MEDIA_DIR, else
# ~/.fbem/media). The MCP stages files here before posting; the extension fetches
# them over loopback. See fbem/bridge/config.py.
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
def get_template(extensionId: str | None = None) -> dict:
    """Return the current captured template (debug). Empty object if none yet."""
    scope = extensionId
    if not scope:
        default = connection_registry.default()
        scope = default.extension_id if default else None
    return capture_store.load_template(scope) or {}


@app.get("/api/media")
def list_media() -> dict:
    """List available media files in media directories."""
    items = []
    seen = set()
    dirs_to_check = [_VIDEO_DIR, Path("media").resolve()]
    for d in dirs_to_check:
        if d.is_dir():
            try:
                for p in d.iterdir():
                    if p.is_file() and p.name not in seen:
                        ext = p.suffix.lower()
                        if ext in {".mp4", ".mov", ".mkv", ".jpg", ".jpeg", ".png"}:
                            seen.add(p.name)
                            items.append({
                                "name": p.name,
                                "path": str(p),
                                "size": p.stat().st_size,
                                "kind": "video" if ext in {".mp4", ".mov", ".mkv"} else "photo",
                            })
            except Exception as e:
                logger.warning(f"Error reading media dir {d}: {e}")
    return {"items": sorted(items, key=lambda x: x["name"])}
