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
import shutil
import time
from contextlib import asynccontextmanager
from typing import Optional, Any
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi import Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import capture_store
from . import history_store
from . import pages_store
from ..bot import telegram_service as telegram_bot
from .config import WS_HOST, HTTP_PORT, media_dir
from .bridge_client import bridge_client
from .ws_server import run_ws_server

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
    telegram_bot.start_bot_task()
    logger.info("fb-bridge started (ws:9224 + http:47102). Waiting for the Chrome extension…")
    try:
        yield
    finally:
        telegram_bot.stop_bot_task()
        ws_task.cancel()
        try:
            await ws_task
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

_STATIC_DIR = Path(__file__).parent / "static"
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


class TelegramConfigBody(BaseModel):
    token: str
    chatId: str
    enabled: bool = True
    autoPost: bool = True


class TelegramTestBody(BaseModel):
    token: str
    chatId: str


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
    # Anchor freshness to the most recent of: explicit reload/connect, or a real
    # captured request (any tab activity keeps it fresh).
    last_act = session.last_active_at if session else bridge_client.last_active_at
    actives = [t for t in (last_act, capture["last_capture_at"]) if t]
    ttl = _ttl_block(max(actives) if actives else None)
    return {
        "ok": True,
        "extension_connected": bridge_client.connected,
        "extension_count": bridge_client.extension_count,
        "extensions": bridge_client.list_extensions(),
        "fb_user": session.fb_user if session else bridge_client.fb_user,
        # Proof the extension is live on a logged-in FB tab: it streams captured
        # requests as soon as the tab (re)loads. tab_active flips true on reload.
        "tab_active": capture["tab_active"],
        "last_capture_at": capture["last_capture_at"],
        "captures": capture["captures"],
        # Tab TTL (auto-reload freshness window).
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
        raise HTTPException(status_code=503, detail="extension_not_connected — load the Chrome extension")
    if not body.videoUrl.strip():
        raise HTTPException(status_code=400, detail="empty_videoUrl")

    ext_id = body.clean_extension_id
    template = capture_store.load_template(ext_id)
    if not capture_store.template_complete(template):
        raise HTTPException(
            status_code=502,
            detail="no_template_captured — manually post one Reel on facebook.com to seed the "
            "crawler (need BOTH the rupload video-upload and the publish mutation)",
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
        if resp.get("error"):
            history_store.update_job(job["id"], "failed", error=resp["error"])
            raise HTTPException(status_code=502, detail=str(resp["error"])[:300])

        data = resp.get("data") or {}
        if not isinstance(data, dict):
            history_store.update_job(job["id"], "failed", error="invalid_response_data")
            raise HTTPException(status_code=502, detail="invalid_response_data")

        res = {
            "ok": True,
            "videoId": data.get("videoId"),
            "permalinkUrl": data.get("permalinkUrl"),
        }
        history_store.update_job(job["id"], "succeeded", result=res)
        asyncio.create_task(
            telegram_bot.send_notification(
                f"🎉 <b>Xuất bản Reel Thành Công!</b>\n🆔 ID: <code>{res['videoId']}</code>\n📝 Caption: <i>{body.caption[:60]}...</i>",
                permalink=res.get("permalinkUrl"),
            )
        )
        return res
    except Exception as e:
        history_store.update_job(job["id"], "failed", error=str(e))
        raise


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

    ext_id = body.clean_extension_id
    template = capture_store.load_template(ext_id)
    if not capture_store.photo_template_complete(template):
        raise HTTPException(
            status_code=502,
            detail="no_photo_template_captured — manually post one photo (and one album) on "
            "facebook.com to seed the crawler (need the ComposerStoryCreateMutation with photo attachments)",
        )

    job = history_store.add_job("post_photos", body.model_dump(), extension_id=ext_id, page_id=body.pageId, caption=body.caption)
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if body.pageId else None

    try:
        resp = await bridge_client.post_photos(
            image_urls=urls,
            caption=body.caption,
            page_id=body.pageId,
            template=template,
            scheduled_publish_time=body.scheduledPublishTime,
            switch_template=switch_tpl,
            extension_id=ext_id,
        )
        if resp.get("error"):
            history_store.update_job(job["id"], "failed", error=resp["error"])
            raise HTTPException(status_code=502, detail=str(resp["error"])[:300])

        data = resp.get("data") or {}
        if not isinstance(data, dict):
            history_store.update_job(job["id"], "failed", error="invalid_response_data")
            raise HTTPException(status_code=502, detail="invalid_response_data")

        res = {
            "ok": True,
            "postId": data.get("postId"),
            "photoIds": data.get("photoIds"),
            "permalinkUrl": data.get("permalinkUrl"),
        }
        history_store.update_job(job["id"], "succeeded", result=res)
        asyncio.create_task(
            telegram_bot.send_notification(
                f"🎉 <b>Đăng Ảnh Thành Công!</b>\n🆔 Post ID: <code>{res['postId']}</code>\n📝 Caption: <i>{body.caption[:60]}...</i>",
                permalink=res.get("permalinkUrl"),
            )
        )
        return res
    except Exception as e:
        history_store.update_job(job["id"], "failed", error=str(e))
        raise


@app.post("/api/upload-media")
async def upload_media(file: UploadFile = File(...)) -> dict:
    """Upload a media file (mp4, jpg, png) directly from UI/API into media dir."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="no_filename")

    clean_name = Path(file.filename).name
    ext = Path(clean_name).suffix.lower()
    if ext not in (".mp4", ".mov", ".jpg", ".jpeg", ".png"):
        raise HTTPException(status_code=400, detail=f"unsupported_format_{ext}")

    out_dir = media_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    dest = out_dir / clean_name
    idx = 0
    while dest.exists():
        idx += 1
        dest = out_dir / f"{Path(clean_name).stem}_{idx}{ext}"

    with dest.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)

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


@app.get("/api/history")
def get_history(limit: int = 50) -> dict:
    """List recent posting jobs and statuses."""
    return {"items": history_store.list_jobs(limit)}


@app.get("/api/jobs")
def get_jobs(limit: int = 50) -> dict:
    """Alias for /api/history."""
    return {"items": history_store.list_jobs(limit)}


@app.get("/api/accounts")
def get_accounts() -> dict:
    """Alias for /api/extensions."""
    return {"items": bridge_client.list_extensions()}


@app.get("/api/scripts")
def get_scripts() -> dict:
    return {"items": []}


@app.delete("/api/history")
def clear_history() -> dict:
    """Clear posting history."""
    history_store.clear_jobs()
    return {"ok": True}


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
    # The switch needs a captured CometProfileSwitchMutation (full fingerprints);
    # a hand-built body is rejected (profile_switcher_comet_login=null).
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
def get_template(extension_id: Optional[str] = None) -> dict:
    """Return the current captured template (debug). Empty object if none yet."""
    return capture_store.load_template(extension_id) or {}


@app.post("/api/launch-profiles")
def launch_profiles() -> dict:
    """Launch all detected Chrome profiles in silent minimized background mode."""
    from . import chrome_launcher
    launched = chrome_launcher.launch_all_profiles_background()
    return {"ok": True, "launched": launched}
