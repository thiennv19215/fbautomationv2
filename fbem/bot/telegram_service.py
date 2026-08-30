"""Telegram Bot Service for FBEM — remote posting, command controls, and notifications."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ..bridge import capture_store, history_store, pages_store
from ..bridge.bridge_client import bridge_client
from ..bridge.config import HTTP_PORT, home_dir, media_dir

logger = logging.getLogger("fbem.bot")

_CONFIG_PATH = home_dir() / "telegram.json"
_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _WORKSPACE_ROOT / ".env"
_WORKSPACE_CONFIG_PATH = _WORKSPACE_ROOT / "telegram_config.json"
_bot_task: Optional[asyncio.Task] = None
_pending_media: dict[str, dict] = {}
_waiting_for_caption: dict[str, str] = {}  # temp store for media awaiting inline button selection


def _read_env_file() -> dict[str, str]:
    candidates = [Path(".env"), _ENV_PATH]
    for env_p in candidates:
        if env_p.exists():
            try:
                res = {}
                for line in env_p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        res[k.strip()] = v.strip().strip('"').strip("'")
                return res
            except Exception as exc:
                logger.warning("failed to read %s: %s", env_p, exc)
    return {}


def get_config() -> dict:
    # 1. Environment variables & .env file priority
    env_file = _read_env_file()
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or env_file.get("TELEGRAM_BOT_TOKEN") or "").strip()
    env_chat_id = (os.getenv("TELEGRAM_CHAT_ID") or env_file.get("TELEGRAM_CHAT_ID") or "").strip()
    env_enabled = env_file.get("TELEGRAM_BOT_ENABLED", "true").lower() in ("true", "1", "yes")
    env_autopost = env_file.get("TELEGRAM_AUTO_POST", "true").lower() in ("true", "1", "yes")

    if env_token and env_token != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        return {
            "token": env_token,
            "chatId": env_chat_id if env_chat_id != "YOUR_TELEGRAM_CHAT_ID_HERE" else "",
            "enabled": env_enabled,
            "autoPost": env_autopost,
        }

    # 2. Check workspace config file: telegram_config.json
    if _WORKSPACE_CONFIG_PATH.exists():
        try:
            data = json.loads(_WORKSPACE_CONFIG_PATH.read_text(encoding="utf-8"))
            if data.get("token") and data.get("token") != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
                return {
                    "token": data.get("token", ""),
                    "chatId": data.get("chatId", "") if data.get("chatId") != "YOUR_TELEGRAM_CHAT_ID_HERE" else "",
                    "enabled": bool(data.get("enabled", True)),
                    "autoPost": bool(data.get("autoPost", True)),
                }
        except Exception as exc:
            logger.warning("failed to load workspace telegram_config.json: %s", exc)

    # 3. Check home config file: ~/.fbem/telegram.json
    if not _CONFIG_PATH.exists():
        return {"token": "", "chatId": "", "enabled": False, "autoPost": True}
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return {
            "token": data.get("token", ""),
            "chatId": data.get("chatId", ""),
            "enabled": bool(data.get("enabled", False)),
            "autoPost": bool(data.get("autoPost", True)),
        }
    except Exception as exc:
        logger.warning("failed to load telegram.json: %s", exc)
        return {"token": "", "chatId": "", "enabled": False, "autoPost": True}


_GROUPS_CONFIG_PATH = home_dir() / "telegram_groups.json"


def get_groups_config() -> dict[str, dict]:
    """Load per-group configuration from ~/.fbem/telegram_groups.json."""
    if not _GROUPS_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_GROUPS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed to load telegram_groups.json: %s", exc)
        return {}


def save_group_config(chat_id: str, group_data: dict) -> dict:
    """Save or update configuration for a specific group."""
    all_groups = get_groups_config()
    cid = str(chat_id)
    existing = all_groups.get(cid, {})
    all_groups[cid] = {**existing, **group_data}
    try:
        _GROUPS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GROUPS_CONFIG_PATH.write_text(json.dumps(all_groups, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("failed to save telegram_groups.json: %s", exc)
    return all_groups


def delete_group_config(chat_id: str) -> dict:
    """Delete a group configuration."""
    all_groups = get_groups_config()
    cid = str(chat_id)
    if cid in all_groups:
        del all_groups[cid]
        try:
            _GROUPS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _GROUPS_CONFIG_PATH.write_text(json.dumps(all_groups, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("failed to update telegram_groups.json: %s", exc)
    return all_groups


def save_config(token: str, chat_id: str, enabled: bool = True, auto_post: bool = True) -> dict:
    cfg = {
        "token": token.strip(),
        "chatId": chat_id.strip(),
        "enabled": enabled,
        "autoPost": auto_post,
    }
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error("failed to save telegram.json: %s", exc)
    return cfg


async def send_message(text: str, chat_id: Optional[str] = None, reply_markup: Optional[dict] = None) -> bool:
    cfg = get_config()
    token = cfg.get("token")
    cid = chat_id or cfg.get("chatId")
    if not token or not cid:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("telegram send_message failed: %s", exc)
        return False


async def send_notification(text: str, permalink: Optional[str] = None) -> bool:
    cfg = get_config()
    if not cfg.get("enabled") or not cfg.get("token") or not cfg.get("chatId"):
        return False

    msg = text
    if permalink:
        msg += f"\n👉 <a href='{permalink}'>Xem bài viết trên Facebook</a>"
    return await send_message(msg)


async def test_connection(token: str, chat_id: str) -> dict:
    if not token.strip():
        return {"ok": False, "error": "Vui lòng nhập Bot Token"}
    if not chat_id.strip():
        return {"ok": False, "error": "Vui lòng nhập Chat ID"}

    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
    payload = {
        "chat_id": chat_id.strip(),
        "text": "🎉 <b>FBEM Studio Kết Nối Thành Công!</b>\n\nTelegram Bot đã sẵn sàng nhận lệnh và đăng bài tự động lên Facebook.",
        "parse_mode": "HTML",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return {"ok": True, "message": "Gửi tin nhắn test thành công!"}
            data = resp.json()
            return {"ok": False, "error": data.get("description", f"Lỗi HTTP {resp.status_code}")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _download_telegram_file(token: str, file_id: str, ext: str) -> Optional[Path]:
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            # 1. Get file path
            info_res = await client.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
            if info_res.status_code != 200:
                return None
            info_data = info_res.json()
            file_path = info_data.get("result", {}).get("file_path")
            if not file_path:
                return None

            # 2. Download file
            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            out_dir = media_dir()
            out_dir.mkdir(parents=True, exist_ok=True)
            filename = f"tg_{int(time.time())}_{Path(file_path).stem}{ext}"
            dest = out_dir / filename

            file_res = await client.get(download_url)
            if file_res.status_code == 200:
                dest.write_bytes(file_res.content)
                return dest
    except Exception as exc:
        logger.error("failed to download telegram file: %s", exc)
    return None


async def _download_direct_url(url: str, default_ext: str = ".mp4") -> tuple[Optional[Path], str, Optional[str]]:
    """Download video/image from a direct URL with validation.
    Returns (dest_path, media_type, error_msg).
    """
    try:
        out_dir = media_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True, trust_env=False, headers=headers) as client:
            # First check headers with streaming GET
            async with client.stream("GET", url) as response:
                if response.status_code not in (200, 206):
                    logger.warning("direct url download failed status=%s for %s", response.status_code, url)
                    return None, "", f"Máy chủ trả về mã lỗi HTTP {response.status_code}"

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type:
                    return None, "", "Đường dẫn bạn gửi là trang web HTML, không phải link file Video/Ảnh trực tiếp"

                # Detect extension
                ext = default_ext
                media_type = "video"
                if "image/" in content_type or any(url.lower().endswith(x) for x in (".jpg", ".jpeg", ".png", ".webp")):
                    ext = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
                    media_type = "photo"
                elif "video/" in content_type or any(url.lower().endswith(x) for x in (".mp4", ".mov", ".webm", ".avi")):
                    ext = ".mp4" if "mp4" in content_type else ".mov"
                    media_type = "video"

                filename = f"url_{int(time.time())}_{abs(hash(url)) % 100000}{ext}"
                dest = out_dir / filename

                total_downloaded = 0
                with open(dest, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        total_downloaded += len(chunk)

                if total_downloaded < 500:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return None, "", "File tải về quá nhỏ hoặc rỗng (dưới 500 bytes)"

                # Check if file starts with HTML doctype
                try:
                    with open(dest, "rb") as check_f:
                        start_bytes = check_f.read(128).lower()
                        if b"<!doctype html" in start_bytes or b"<html" in start_bytes:
                            dest.unlink(missing_ok=True)
                            return None, "", "Nội dung tải về là mã HTML trang web thay vì file media thực tế"
                except Exception:
                    pass

                return dest, media_type, None
    except httpx.ConnectTimeout:
        return None, "", "Kết nối tới link bị quá thời gian (Timeout)"
    except httpx.ConnectError:
        return None, "", "Không thể kết nối tới máy chủ chứa link (Connection Error)"
    except Exception as exc:
        logger.error("failed to download direct URL %s: %s", url, exc)
        return None, "", str(exc)


async def _execute_post_reel(chat_id: str, dest_path: Path, caption: str, page_id: Optional[str] = None, ext_id: Optional[str] = None, scheduled_publish_time: Optional[int] = None):
    if page_id and (not ext_id or ext_id == "default"):
        p_info = pages_store.get_page(page_id)
        if p_info and p_info.get("extensionId"):
            ext_id = p_info["extensionId"]

    sched_info = ""
    if scheduled_publish_time:
        import datetime
        dt_readable = datetime.datetime.fromtimestamp(scheduled_publish_time).strftime("%H:%M %d/%m/%Y")
        sched_info = f"\n📅 <b>Hẹn giờ đăng:</b> <code>{dt_readable}</code>"

    await send_message(f"⏳ <b>Đang gửi Video Reel lên Facebook...</b>{sched_info}\n<i>Vui lòng đợi vài giây để hệ thống xuất bản.</i>", chat_id=chat_id)
    template = capture_store.load_template(ext_id)
    if not capture_store.template_complete(template):
        await send_message(
            "⚠️ <b>Chưa có Mẫu Reel:</b>\nVui lòng mở Chrome đăng tay 1 Reel trên Facebook để hệ thống lấy mẫu trước.",
            chat_id=chat_id,
        )
        return

    staged_url = f"http://127.0.0.1:{HTTP_PORT}/local-video?name={quote(dest_path.name)}"
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if page_id else None

    job = history_store.add_job("post_reel", {"videoUrl": staged_url, "caption": caption, "scheduledPublishTime": scheduled_publish_time}, extension_id=ext_id, page_id=page_id, caption=caption)
    try:
        resp = await bridge_client.post_reel(
            video_url=staged_url,
            caption=caption,
            page_id=page_id,
            template=template,
            switch_template=switch_tpl,
            extension_id=ext_id,
            scheduled_publish_time=scheduled_publish_time,
        )
        if resp.get("error"):
            history_store.update_job(job["id"], "failed", error=resp["error"])
            await send_message(f"❌ <b>Đăng Reel thất bại:</b>\n<code>{resp['error']}</code>", chat_id=chat_id)
            return

        data = resp.get("data") or {}
        res = {"ok": True, "videoId": data.get("videoId"), "permalinkUrl": data.get("permalinkUrl")}
        history_store.update_job(job["id"], "succeeded", result=res)

        # Auto clean up video file on success to save disk space
        try:
            if dest_path.exists():
                dest_path.unlink()
                logger.info("cleaned up local video file after success: %s", dest_path.name)
        except Exception as e:
            logger.warning("failed to delete media file: %s", e)

        title_msg = "🎉 <b>ĐÃ HẸN GIỜ ĐĂNG REEL THÀNH CÔNG!</b>" if scheduled_publish_time else "🎉 <b>XUẤT BẢN REEL THÀNH CÔNG!</b>"
        msg_text = (
            f"{title_msg}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Video ID:</b> <code>{res.get('videoId')}</code>\n"
            f"📝 <b>Caption:</b> <i>{caption[:80]}...</i>\n"
            f"{sched_info}\n"
            "🗑️ <i>Đã tự động xóa file tạm trên máy tính.</i>"
        )
        markup = None
        if res.get("permalinkUrl"):
            markup = {"inline_keyboard": [[{"text": "👉 Xem Video trên Facebook ↗", "url": res["permalinkUrl"]}]]}
        await send_message(msg_text, chat_id=chat_id, reply_markup=markup)
    except Exception as exc:
        history_store.update_job(job["id"], "failed", error=str(exc))
        await send_message(f"❌ <b>Lỗi đăng bài:</b> {exc}", chat_id=chat_id)


async def _execute_post_photo(chat_id: str, dest_path: Path, caption: str, page_id: Optional[str] = None, ext_id: Optional[str] = None, scheduled_publish_time: Optional[int] = None):
    if page_id and (not ext_id or ext_id == "default"):
        p_info = pages_store.get_page(page_id)
        if p_info and p_info.get("extensionId"):
            ext_id = p_info["extensionId"]

    sched_info = ""
    if scheduled_publish_time:
        import datetime
        dt_readable = datetime.datetime.fromtimestamp(scheduled_publish_time).strftime("%H:%M %d/%m/%Y")
        sched_info = f"\n📅 <b>Hẹn giờ đăng:</b> <code>{dt_readable}</code>"

    await send_message(f"⏳ <b>Đang gửi Ảnh lên Facebook...</b>{sched_info}\n<i>Vui lòng đợi vài giây để hệ thống xuất bản.</i>", chat_id=chat_id)
    template = capture_store.load_template(ext_id)
    if not capture_store.photo_template_complete(template):
        await send_message(
            "⚠️ <b>Chưa có Mẫu Ảnh:</b>\nVui lòng mở Chrome đăng tay 1 ảnh trên Facebook để hệ thống lấy mẫu trước.",
            chat_id=chat_id,
        )
        return

    staged_url = f"http://127.0.0.1:{HTTP_PORT}/local-image?name={quote(dest_path.name)}"
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if page_id else None

    job = history_store.add_job("post_photos", {"imageUrls": [staged_url], "caption": caption, "scheduledPublishTime": scheduled_publish_time}, extension_id=ext_id, page_id=page_id, caption=caption)
    try:
        resp = await bridge_client.post_photos(
            image_urls=[staged_url],
            caption=caption,
            page_id=page_id,
            template=template,
            switch_template=switch_tpl,
            extension_id=ext_id,
            scheduled_publish_time=scheduled_publish_time,
        )
        if resp.get("error"):
            history_store.update_job(job["id"], "failed", error=resp["error"])
            await send_message(f"❌ <b>Đăng ảnh thất bại:</b>\n<code>{resp['error']}</code>", chat_id=chat_id)
            return

        data = resp.get("data") or {}
        res = {"ok": True, "postId": data.get("postId"), "permalinkUrl": data.get("permalinkUrl")}
        history_store.update_job(job["id"], "succeeded", result=res)

        # Auto clean up photo file on success to save disk space
        try:
            if dest_path.exists():
                dest_path.unlink()
                logger.info("cleaned up local photo file after success: %s", dest_path.name)
        except Exception as e:
            logger.warning("failed to delete media file: %s", e)

        title_msg = "🎉 <b>ĐÃ HẸN GIỜ ĐĂNG ẢNH THÀNH CÔNG!</b>" if scheduled_publish_time else "🎉 <b>ĐĂNG ẢNH THÀNH CÔNG!</b>"
        msg_text = (
            f"{title_msg}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Post ID:</b> <code>{res.get('postId')}</code>\n"
            f"📝 <b>Caption:</b> <i>{caption[:80]}...</i>\n"
            f"{sched_info}\n"
            "🗑️ <i>Đã tự động xóa file tạm trên máy tính.</i>"
        )
        markup = None
        if res.get("permalinkUrl"):
            markup = {"inline_keyboard": [[{"text": "👉 Xem bài viết trên Facebook ↗", "url": res["permalinkUrl"]}]]}
        await send_message(msg_text, chat_id=chat_id, reply_markup=markup)
    except Exception as exc:
        history_store.update_job(job["id"], "failed", error=str(exc))
        await send_message(f"❌ <b>Lỗi đăng ảnh:</b> {exc}", chat_id=chat_id)


_MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Trạng thái"}, {"text": "📑 Fanpages"}, {"text": "👥 Nick Online"}],
        [{"text": "🚀 Bật Chrome ngầm"}, {"text": "➕ Thêm / Đổi tên Page"}],
        [{"text": "📜 Lịch sử bài đăng"}, {"text": "🎯 Mẫu Capture"}, {"text": "❓ Hướng dẫn"}],
    ],
    "resize_keyboard": True,
    "persistent": True,
}


def extract_smart_caption(url_or_name: str) -> str:
    """Auto-extract and format a clean engaging caption from a URL filename or local file path."""
    import re
    from urllib.parse import urlparse, unquote

    path = urlparse(url_or_name).path if "://" in url_or_name else url_or_name
    name = unquote(path.split("/")[-1])
    name = re.sub(r"\.(mp4|mov|avi|webm|jpg|jpeg|png)$", "", name, flags=re.IGNORECASE)
    # Remove random hash hex (e.g. 2d2cf1774c687ed68fd72b51eff38e241221fdabfc63100b2b75424306535ee0_)
    name = re.sub(r"^[a-f0-9]{20,64}_+", "", name, flags=re.IGNORECASE)
    # Remove tail hash (e.g. _acd2b10f)
    name = re.sub(r"_[a-f0-9]{6,16}$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"_\d{8,14}_\d+$", "", name)
    # Replace separators
    name = re.sub(r"[_+\-]+", " ", name).strip()
    words = name.split()
    if not words:
        return "Video Reels cực hot 🔥 #reels #viral #xuhuong #trending"

    # Clean words
    title = " ".join(w.capitalize() if w.isupper() else w for w in words)
    
    # Auto-detect hashtags based on title keywords
    lower_t = title.lower()
    tags = ["#reels", "#viral", "#xuhuong"]
    if "review" in lower_t:
        tags.append("#review")
    elif "phim" in lower_t:
        tags.append("#reviewphim")
    elif "hai" in lower_t or "funny" in lower_t:
        tags.append("#haihuoc")
    elif "game" in lower_t or "gaming" in lower_t:
        tags.append("#gaming")
    else:
        tags.append("#trending")

    return f"{title} 🔥 {' '.join(tags)}"


def _extract_schedule(caption: str) -> tuple[str, Optional[int]]:
    """Check if caption contains #schedule or #hengio and return clean caption + epoch timestamp."""
    import re
    from datetime import datetime, timedelta

    # 1. Pattern: #hengio 2h / #hengio 30m / #hengio 1d
    rel_match = re.search(r"#(?:schedule|hengio)\s+(\d+)\s*(h|m|d|gio|phut|ngay)\b", caption, re.IGNORECASE)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        now = datetime.now()
        if unit in ("h", "gio"):
            target_dt = now + timedelta(hours=val)
        elif unit in ("m", "phut"):
            target_dt = now + timedelta(minutes=val)
        elif unit in ("d", "ngay"):
            target_dt = now + timedelta(days=val)
        else:
            target_dt = now + timedelta(hours=val)
        clean_cap = re.sub(r"#(?:schedule|hengio)\s+\d+\s*(?:h|m|d|gio|phut|ngay)\b", "", caption, flags=re.IGNORECASE).strip()
        return clean_cap, int(target_dt.timestamp())

    # 2. Pattern: #hengio YYYY-MM-DD HH:MM or #hengio DD/MM/YYYY HH:MM
    dt_match = re.search(r"#(?:schedule|hengio)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})", caption, re.IGNORECASE)
    if dt_match:
        d_str = dt_match.group(1)
        t_str = dt_match.group(2)
        fmt = "%Y-%m-%d %H:%M" if "-" in d_str else "%d/%m/%Y %H:%M"
        try:
            dt = datetime.strptime(f"{d_str} {t_str}", fmt)
            clean_cap = re.sub(r"#(?:schedule|hengio)\s+(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\s+\d{1,2}:\d{2}", "", caption, flags=re.IGNORECASE).strip()
            return clean_cap, int(dt.timestamp())
        except Exception:
            pass

    # 3. Pattern: #hengio HH:MM (e.g. #hengio 20:00)
    time_only_match = re.search(r"#(?:schedule|hengio)\s+(\d{1,2}:\d{2})", caption, re.IGNORECASE)
    if time_only_match:
        t_str = time_only_match.group(1)
        try:
            now = datetime.now()
            h, m = map(int, t_str.split(":"))
            target_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target_dt <= now:
                target_dt += timedelta(days=1)  # next day
            clean_cap = re.sub(r"#(?:schedule|hengio)\s+\d{1,2}:\d{2}", "", caption, flags=re.IGNORECASE).strip()
            return clean_cap, int(target_dt.timestamp())
        except Exception:
            pass

    return caption, None


def _is_authorized(chat_id: str, from_user_id: str | None = None) -> bool:
    """Check if the sender/chat is authorized to control the bot."""
    cfg = get_config()
    admin_cid = str(cfg.get("chatId") or "").strip()
    cid = str(chat_id).strip()
    uid = str(from_user_id or "").strip()

    # If no admin chatId is configured yet, allow initial configuration
    if not admin_cid:
        return True

    # Check if sender is admin
    if cid == admin_cid or (uid and uid == admin_cid):
        return True

    # Check if group is registered and active
    groups = get_groups_config()
    if cid in groups:
        return True

    return False


async def _handle_update(token: str, update: dict):
    try:
        # 1. Handle Callback Query (Inline button click)
        if "callback_query" in update:
            cq = update["callback_query"]
            cq_id = cq.get("id")
            data = cq.get("data", "")
            from_user = cq.get("from", {}).get("id")
            chat_id = str(from_user)

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={"callback_query_id": cq_id})
            except Exception:
                pass

            if not _is_authorized(chat_id, str(from_user)):
                logger.warning("Unauthorized callback query from %s (data=%s)", from_user, data)
                await send_message("⛔ <b>Bạn không có quyền thao tác trên hệ thống này!</b>", chat_id=chat_id)
                return


            if data.startswith("g:"):
                # format: g:<action>:<chat_id>[:extra]
                gparts = data.split(":")
                action = gparts[1] if len(gparts) > 1 else ""
                target_cid = gparts[2] if len(gparts) > 2 else chat_id
                
                if action == "close":
                    await send_message("❌ <b>Đã đóng cài đặt nhóm.</b>", chat_id=chat_id)
                    return

                if action == "sp": # Choose default page
                    pages = pages_store.list_pages()
                    if not pages:
                        await send_message("⚠️ Chưa có Fanpage nào được lưu trong hệ thống. Vui lòng thêm Fanpage trên Web Dashboard!", chat_id=chat_id)
                        return
                    ik = []
                    for p in pages:
                        ik.append([{"text": f"📢 {p['name']}", "callback_data": f"g:setp:{target_cid}:{p['id']}"}])
                    ik.append([{"text": "❌ Hủy", "callback_data": "g:close:0"}])
                    await send_message("🎯 <b>CHỌN FANPAGE MẶC ĐỊNH CHO NHÓM:</b>\n<i>Khi gửi video/link, bot sẽ tự động xuất bản lên Trang này:</i>", chat_id=chat_id, reply_markup={"inline_keyboard": ik})
                    return

                if action == "setp": # Set chosen page
                    page_id = gparts[3] if len(gparts) > 3 else ""
                    pages = pages_store.list_pages()
                    p_match = next((p for p in pages if p["id"] == page_id), None)
                    p_name = p_match["name"] if p_match else page_id
                    save_group_config(target_cid, {"default_page_id": page_id, "default_page_name": p_name, "auto_post": True})
                    await send_message(f"✅ <b>ĐÃ CẤU HÌNH NHÓM THÀNH CÔNG!</b>\n━━━━━━━━━━━━━━━━━━━━\n• 📢 <b>Trang mặc định:</b> {p_name}\n• ⚡ <b>Tự động đăng (Auto-Post):</b> ĐÃ BẬT\n\n<i>Bây giờ bất kỳ ai gửi link/video vào nhóm, Bot sẽ tự động xuất bản ngay!</i>", chat_id=chat_id)
                    return

                if action == "ta": # Toggle auto post
                    all_grp = get_groups_config()
                    current_auto = all_grp.get(target_cid, {}).get("auto_post", False)
                    new_auto = not current_auto
                    save_group_config(target_cid, {"auto_post": new_auto})
                    st_str = "BẬT 🟢 (Tự động đăng ngay)" if new_auto else "TẮT 🔴 (Hỏi chọn nút mỗi lần)"
                    await send_message(f"⚡ <b>Chế độ Auto-Post của nhóm:</b> {st_str}", chat_id=chat_id)
                    return

                if action == "del":
                    delete_group_config(target_cid)
                    await send_message("🗑️ <b>Đã xóa cấu hình riêng của nhóm này.</b> (Trở về cấu hình mặc định)", chat_id=chat_id)
                    return

            if data.startswith("p:") or data.startswith("post:"):
                # format: p:<media_key>:<target_key>
                parts = data.split(":")
                if len(parts) >= 3:
                    media_key = parts[1]
                    target_key = parts[2]
                    if target_key in ("c", "cancel"):
                        _pending_media.pop(media_key, None)
                        _waiting_for_caption.pop(chat_id, None)
                        await send_message("❌ <b>Đã hủy bài đăng.</b>", chat_id=chat_id)
                        return

                    if target_key == "edcap":
                        if media_key in _pending_media:
                            _waiting_for_caption[chat_id] = media_key
                            await send_message(
                                "✏️ <b>NHẬP CAPTION MỚI:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                                "Vui lòng gửi nội dung Caption & Hashtag mới cho bài đăng này:",
                                chat_id=chat_id,
                            )
                        else:
                            await send_message("⚠️ Phiên đăng bài đã hết hạn hoặc bị hủy. Vui lòng gửi lại link!", chat_id=chat_id)
                        return

                    pending = _pending_media.pop(media_key, None)
                    if pending:
                        target_info = pending.get("targets", {}).get(target_key)
                        if target_info:
                            p_id = target_info.get("page_id")
                            ext_id = target_info.get("ext_id")
                            clean_cap, sched = _extract_schedule(pending["caption"])

                            dest_path = pending.get("path")
                            if not dest_path and pending.get("download_task"):
                                downloaded_dest, _, err = await pending["download_task"]
                                if not downloaded_dest:
                                    await send_message(f"❌ <b>Tải media từ link thất bại:</b> {err or 'Không thể tải file'}", chat_id=chat_id)
                                    return
                                dest_path = downloaded_dest

                            if not dest_path or not Path(dest_path).exists():
                                await send_message("❌ <b>Không tìm thấy file media để xuất bản.</b> Vui lòng gửi lại link!", chat_id=chat_id)
                                return

                            if pending["kind"] == "video":
                                await _execute_post_reel(chat_id, dest_path, clean_cap, page_id=p_id, ext_id=ext_id, scheduled_publish_time=sched)
                            else:
                                await _execute_post_photo(chat_id, dest_path, clean_cap, page_id=p_id, ext_id=ext_id, scheduled_publish_time=sched)
            return

        # 2. Handle Messages
        msg = update.get("message")
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id"))
        from_user = msg.get("from", {})
        from_user_id = str(from_user.get("id") or "")
        text = (msg.get("text") or "").strip()
        caption = (msg.get("caption") or "").strip()
        lower_text = text.lower()

        # Check authorization: only configured admin or registered group
        if not _is_authorized(chat_id, from_user_id):
            logger.warning("Unauthorized message from chat_id=%s, from_user_id=%s", chat_id, from_user_id)
            if msg.get("chat", {}).get("type", "private") == "private":
                await send_message(
                    f"⛔ <b>TRUY CẬP BỊ TỪ CHỐI</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"Tài khoản của bạn (ID: <code>{from_user_id or chat_id}</code>) chưa được cấu hình làm Quản trị viên điều khiển FBEM.",
                    chat_id=chat_id,
                )
            return

        # Track Group Chat Activity
        chat_type = msg.get("chat", {}).get("type", "private")
        chat_title = msg.get("chat", {}).get("title") or msg.get("chat", {}).get("username") or f"Chat {chat_id}"
        if chat_type in ("group", "supergroup", "channel") or str(chat_id).startswith("-"):
            save_group_config(chat_id, {"title": chat_title, "type": chat_type, "last_active": int(time.time())})

        # Group Setup / Config Command
        if lower_text in ("/setup", "/config", "/caidat", "/setpage", "/help@bot", "/config@bot", "/setup@bot"):
            grp_cfg = get_groups_config().get(chat_id, {})
            p_name = grp_cfg.get("default_page_name") or "(Chưa gán Trang nào)"
            auto_st = "🟢 ĐANG BẬT" if grp_cfg.get("auto_post") else "🔴 ĐANG TẮT"
            tags_st = grp_cfg.get("default_hashtags") or "(Không có)"

            ik = [
                [{"text": "🎯 Chọn Fanpage Mặc Định", "callback_data": f"g:sp:{chat_id}"}],
                [{"text": f"⚡ Auto-Post: {auto_st}", "callback_data": f"g:ta:{chat_id}"}],
                [{"text": "🗑️ Xóa cấu hình nhóm", "callback_data": f"g:del:{chat_id}"}, {"text": "❌ Đóng", "callback_data": "g:close:0"}],
            ]
            await send_message(
                f"⚙️ <b>CẤU HÌNH NHÓM: {chat_title}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"• 📢 <b>Fanpage mặc định:</b> {p_name}\n"
                f"• ⚡ <b>Tự động đăng (Auto-Post):</b> {auto_st}\n"
                f"• 🏷️ <b>Hashtag mặc định:</b> <code>{tags_st}</code>\n\n"
                "<i>Bấm nút bên dưới để tùy chỉnh riêng cho nhóm này:</i>",
                chat_id=chat_id,
                reply_markup={"inline_keyboard": ik}
            )
            return

        # Handle Waiting for Caption Reply
        if chat_id in _waiting_for_caption and text and not text.startswith("/"):
            media_key = _waiting_for_caption.pop(chat_id)
            if media_key in _pending_media:
                _pending_media[media_key]["caption"] = text
                pending = _pending_media[media_key]
                
                # Re-render Fanpage selection menu
                inline_keyboard = []
                for tk, target_info in pending.get("targets", {}).items():
                    p_id = target_info.get("page_id")
                    if p_id:
                        p_match = next((p for p in pages_store.list_pages() if p.get("id") == p_id), None)
                        p_name = p_match["name"] if p_match else f"Trang {p_id[:6]}"
                        inline_keyboard.append([{"text": f"📢 {p_name}", "callback_data": f"p:{media_key}:{tk}"}])
                    else:
                        inline_keyboard.append([{"text": "👤 Nick cá nhân", "callback_data": f"p:{media_key}:{tk}"}])
                inline_keyboard.append([
                    {"text": "✏️ Đổi Caption khác", "callback_data": f"p:{media_key}:edcap"},
                    {"text": "❌ Hủy bỏ", "callback_data": f"p:{media_key}:c"},
                ])

                cap_preview = f"\n📝 <b>Caption mới:</b> <i>{text[:100]}</i>"
                await send_message(
                    f"✅ <b>ĐÃ CẬP NHẬT CAPTION THÀNH CÔNG!</b>{cap_preview}\n\n🎯 <b>Chọn nơi đăng ngay:</b>",
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": inline_keyboard},
                )
                return

        # Commands & Menu Buttons
        if lower_text.startswith("/start") or lower_text.startswith("/help") or "hướng dẫn" in lower_text:
            welcome = (
                "⚡ <b>FBEM STUDIO — TỰ ĐỘNG HÓA FACEBOOK</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Chào bạn! Bot này giúp bạn quản lý dàn Via và đăng bài Reels/Ảnh lên Facebook siêu tốc.\n\n"
                "<b>🚀 CÁCH ĐĂNG BÀI 1-CHẠM:</b>\n"
                "1️⃣ <b>Gửi Video (.mp4)</b> hoặc <b>Ảnh</b> kèm Caption vào đây.\n"
                "2️⃣ Bấm chọn <b>Fanpage</b> hoặc <b>Nick Via</b> tương ứng.\n"
                "3️⃣ Nhận ngay <b>Link bài viết Facebook</b> sau khi đăng xong!\n\n"
                "<b>⚙️ CÁC LỆNH QUẢN LÝ NHANH:</b>\n"
                "• <code>/addpage &lt;ID&gt; &lt;Tên&gt;</code> — Lưu Fanpage mới\n"
                "• <code>/rename &lt;ID&gt; &lt;Tên Mới&gt;</code> — Đổi tên Fanpage\n"
                "• <code>/delpage &lt;ID&gt;</code> — Xóa Fanpage\n"
                "• <code>/launch</code> — Kích hoạt dàn Chrome chạy ngầm\n"
                "• <code>#schedule YYYY-MM-DD HH:MM</code> — Thêm vào Caption để hẹn giờ"
            )
            await send_message(welcome, chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/launch") or "bật chrome" in lower_text or "chạy chrome" in lower_text:
            await send_message("⏳ <b>Đang kích hoạt dàn Profile Chrome chạy ngầm trên máy tính...</b>", chat_id=chat_id)
            try:
                from ..bridge.chrome_launcher import launch_all_profiles
                profs = await asyncio.to_thread(launch_all_profiles)
                await asyncio.sleep(2)
                exts = bridge_client.list_extensions()
                await send_message(
                    f"🚀 <b>Đã kích hoạt {len(profs)} Profile Chrome ngầm!</b>\n"
                    f"🟢 Số Nick Via đang kết nối trực tiếp: <b>{len(exts)} Nick</b>\n\n"
                    "💡 <i>Các Profile phụ đang chạy 100% ẩn ngầm ngoài màn hình.</i>",
                    chat_id=chat_id,
                    reply_markup=_MAIN_KEYBOARD,
                )
            except Exception as e:
                await send_message(f"❌ <b>Lỗi bật Chrome:</b> {e}", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if "thêm" in lower_text and "page" in lower_text:
            guide = (
                "➕ <b>HƯỚNG DẪN QUẢN LÝ FANPAGE TỪ XA:</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<b>1️⃣ Thêm Fanpage mới:</b>\n"
                "<code>/addpage &lt;PageID&gt; &lt;Tên Fanpage&gt;</code>\n"
                "<i>Ví dụ:</i> <code>/addpage 61585679104398 Review Phim Hay</code>\n\n"
                "<b>2️⃣ Đổi tên Fanpage:</b>\n"
                "<code>/rename &lt;PageID&gt; &lt;Tên Mới&gt;</code>\n"
                "<i>Ví dụ:</i> <code>/rename 61585679104398 Tin Tức Nóng 24h</code>\n\n"
                "<b>3️⃣ Xóa Fanpage:</b>\n"
                "<code>/delpage &lt;PageID&gt;</code>\n\n"
                "👉 <i>Bạn chỉ cần copy cú pháp trên và gửi vào đây nhé!</i>"
            )
            await send_message(guide, chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/delpage") or lower_text.startswith("/xoapage"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await send_message("⚠️ <b>Cú pháp xóa Page:</b> <code>/delpage &lt;PageID&gt;</code>", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return
            p_id = parts[1].strip()
            pages_store.delete_page(p_id)
            await send_message(f"🗑️ <b>Đã xóa Fanpage ID <code>{p_id}</code> thành công!</b>", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/template") or "mẫu" in lower_text:
            tpl = capture_store.load_template() or {}
            exts = bridge_client.list_extensions()
            has_reel = "✅ Đã sẵn sàng" if bool(tpl.get("graphql")) else "⚠️ Chưa có (đăng tay 1 Reel trên FB để lấy)"
            has_photo = "✅ Đã sẵn sàng" if bool(tpl.get("graphql_photo")) else "⚠️ Chưa có (đăng tay 1 Ảnh trên FB để lấy)"
            has_switch = "✅ Đã sẵn sàng" if bool((tpl.get("graphql_ops") or {}).get("CometProfileSwitchMutation")) else "⚠️ Chưa có (chuyển profile 1 lần trên FB để lấy)"
            
            lines = [
                "🎯 <b>TÌNH TRẠNG MẪU CAPTURE (GỐC & VIA):</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                f"• 🎬 <b>Mẫu Video Reels:</b> {has_reel}",
                f"• 🖼️ <b>Mẫu Bài viết Ảnh:</b> {has_photo}",
                f"• 🔄 <b>Mẫu Chuyển Page/Profile:</b> {has_switch}",
                "",
                f"👥 <b>Đang kết nối:</b> <b>{len(exts)} Nick Via</b>",
            ]
            for i, e in enumerate(exts, 1):
                e_tpl = capture_store.load_template(e["id"]) or {}
                e_user = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                r_ok = "✅ Reel" if e_tpl.get("graphql") else "⚪ Reel"
                p_ok = "✅ Photo" if e_tpl.get("graphql_photo") else "⚪ Photo"
                lines.append(f"  {i}. 👤 <b>{e_user}</b>: [{r_ok}] [{p_ok}]")
                
            await send_message("\n".join(lines), chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/status") or "trạng thái" in lower_text:
            exts = bridge_client.list_extensions()
            ext_count = len(exts)
            connected = bridge_client.connected
            tpl = capture_store.load_template() or {}
            has_reel = "✅ Đã sẵn sàng" if bool(tpl.get("graphql")) else "⚠️ Chưa có"
            has_photo = "✅ Đã sẵn sàng" if bool(tpl.get("graphql_photo")) else "⚠️ Chưa có"
            pages = pages_store.list_pages()

            # Auto-fetch real Facebook name from open tab if not yet populated
            for e in exts:
                if not e.get("fbUser"):
                    try:
                        resp = await bridge_client.get_identity(extension_id=e["id"])
                        if resp.get("data"):
                            d = resp["data"]
                            session = bridge_client.get_session(e["id"])
                            fb_info = {"id": d.get("identityId"), "name": d.get("identityName")}
                            if session:
                                session.fb_user = fb_info
                            e["fbUser"] = fb_info
                    except Exception:
                        pass

            # Build list of active Vias
            if exts:
                via_lines = []
                for i, e in enumerate(exts, 1):
                    fb_u = e.get("fbUser") or {}
                    uname = fb_u.get("name") or f"Profile {e['id'][:6]}"
                    uid_str = f" (UID: <code>{fb_u.get('id')}</code>)" if fb_u.get("id") else ""
                    via_lines.append(f"  {i}. 👤 <b>{uname}</b>{uid_str} 🟢 <i>Online</i>")
                vias_text = "\n".join(via_lines)
            else:
                vias_text = "  ⚠️ <i>Chưa có Nick nào mở trình duyệt Chrome</i>"

            # Build list of Fanpages
            if pages:
                page_lines = []
                for p in pages:
                    owner_ext = p.get("extensionId")
                    owner_name = "Tất cả Nick" if not owner_ext else f"Nick {owner_ext[:6]}"
                    page_lines.append(f"  • 📢 <b>{p['name']}</b> (<code>{p['id']}</code>) — <i>Cầm bởi: {owner_name}</i>")
                pages_text = "\n".join(page_lines)
            else:
                pages_text = "  <i>Chưa có Fanpage nào được lưu.</i>"

            status_msg = (
                "⚡ <b>BÁO CÁO HỆ THỐNG FBEM STUDIO</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 <b>Kết nối Bridge:</b> {'Hoạt động tốt' if connected else '🔴 Mất kết nối'}\n\n"
                f"👥 <b>DANH SÁCH {ext_count} NICK VIA ONLINE:</b>\n"
                f"{vias_text}\n\n"
                f"📑 <b>DANH SÁCH FANPAGES ({len(pages)} trang):</b>\n"
                f"{pages_text}\n\n"
                "🎬 <b>TÌNH TRẠNG MẪU ĐĂNG BÀI:</b>\n"
                f"  • Mẫu Video Reels: {has_reel}\n"
                f"  • Mẫu Bài viết Ảnh: {has_photo}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>Gửi video hoặc ảnh vào đây để chọn Nick & Fanpage đăng bài ngay!</i>"
            )
            await send_message(status_msg, chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/addpage"):
            # Usage: /addpage 61585679104398 Tên Fanpage
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_message(
                    "⚠️ <b>Cú pháp thêm Fanpage:</b>\n<code>/addpage &lt;PageID&gt; &lt;Tên Fanpage&gt;</code>\n\nVí dụ:\n<code>/addpage 61585679104398 Review Phim Hay</code>",
                    chat_id=chat_id,
                    reply_markup=_MAIN_KEYBOARD,
                )
                return
            p_id, p_name = parts[1].strip(), parts[2].strip()
            pages_store.save_page(p_id, p_name)
            await send_message(
                f"✅ <b>Đã lưu Fanpage thành công!</b>\n• Tên: <b>{p_name}</b>\n• ID: <code>{p_id}</code>\n\n💡 <i>Bây giờ bạn có thể gửi Video để đăng lên Page này ngay!</i>",
                chat_id=chat_id,
                reply_markup=_MAIN_KEYBOARD,
            )
            return

        if lower_text.startswith("/rename") or lower_text.startswith("/renamepage") or lower_text.startswith("/doiten"):
            # Usage: /rename 61585679104398 Tên Mới Của Fanpage
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await send_message(
                    "⚠️ <b>Cú pháp đổi tên Fanpage:</b>\n<code>/rename &lt;PageID&gt; &lt;Tên Mới&gt;</code>\n\nVí dụ:\n<code>/rename 61585679104398 Review Phim Chiếu Rạp</code>",
                    chat_id=chat_id,
                    reply_markup=_MAIN_KEYBOARD,
                )
                return
            p_id, p_name = parts[1].strip(), parts[2].strip()
            pages_store.save_page(p_id, p_name)
            await send_message(
                f"✏️ <b>Đã đổi tên Fanpage thành công!</b>\n• ID: <code>{p_id}</code>\n• Tên mới: <b>{p_name}</b>",
                chat_id=chat_id,
                reply_markup=_MAIN_KEYBOARD,
            )
            return

        if lower_text.startswith("/pages") or "fanpage" in lower_text or "trang" in lower_text:
            pages = pages_store.list_pages()
            exts = bridge_client.list_extensions()
            ext_user_map = {}
            for e in exts:
                uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                ext_user_map[e["id"]] = uname

            if not pages:
                await send_message(
                    "📑 <b>Chưa có Fanpage nào được lưu.</b>\n\n"
                    "👉 Bạn có thể thêm nhanh bằng lệnh:\n<code>/addpage &lt;PageID&gt; &lt;Tên Page&gt;</code>\n"
                    "Hoặc mở Web Dashboard: <code>http://127.0.0.1:47102/</code>",
                    chat_id=chat_id,
                    reply_markup=_MAIN_KEYBOARD,
                )
                return
            lines = []
            for p in pages:
                owner_ext = p.get("extensionId")
                owner_name = ext_user_map.get(owner_ext) or ("Tất cả Nick" if not owner_ext else f"Nick {owner_ext[:6]}")
                lines.append(f"• 📢 <b>{p['name']}</b> (ID: <code>{p['id']}</code>)\n  └ 👤 Quản lý: <b>{owner_name}</b>\n  └ ✏️ Đổi tên: <code>/rename {p['id']} Tên_Mới</code>")
            await send_message("📑 <b>DANH SÁCH FANPAGE & NICK QUẢN LÝ:</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n\n".join(lines), chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/accounts") or lower_text.startswith("/profiles") or "nick" in lower_text or "online" in lower_text:
            exts = bridge_client.list_extensions()
            if not exts:
                await send_message("👥 <b>Chưa có Chrome Profile nào đang kết nối.</b>\nVui lòng mở trình duyệt Chrome đã cài Extension.", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return
            lines = []
            for e in exts:
                fb_user = e.get("fbUser") or {}
                name = fb_user.get("name") or "Nick Facebook"
                uid = fb_user.get("id") or "Chưa đọc UID"
                lines.append(f"• 👤 <b>{name}</b> (UID: <code>{uid}</code>)\n  └ Profile ID: <code>{e.get('id', '')[:8]}...</code>")
            await send_message("👥 <b>DANH SÁCH NICK VIA ĐANG ONLINE:</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n\n".join(lines), chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/history") or "lịch sử" in lower_text:
            jobs = history_store.list_jobs(5)
            if not jobs:
                await send_message("📜 <b>Chưa có lịch sử bài đăng nào.</b>", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return
            lines = []
            for j in jobs:
                st = "✅" if j.get("status") == "succeeded" else "❌"
                kind = "🎬 Reel" if j.get("kind") == "post_reel" else "🖼️ Ảnh"
                cap = j.get("caption", "").strip() or "(Không có caption)"
                if len(cap) > 30: cap = cap[:30] + "..."
                link = f" → <a href='{j['result']['permalinkUrl']}'>Xem link</a>" if j.get("result", {}).get("permalinkUrl") else ""
                lines.append(f"{st} <b>{kind}:</b> {cap}{link}")
            await send_message("📜 <b>5 BÀI ĐĂNG GẦN NHẤT:</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines), chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/accounts") or lower_text.startswith("/profiles") or "nick" in lower_text or "online" in lower_text:
            exts = bridge_client.list_extensions()
            if not exts:
                await send_message("👥 <b>Chưa có Chrome Profile nào đang kết nối.</b>\nVui lòng mở trình duyệt Chrome đã cài Extension.", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return
            lines = []
            for e in exts:
                fb_user = e.get("fbUser") or {}
                name = fb_user.get("name") or "Nick Facebook"
                uid = fb_user.get("id") or "Chưa đọc UID"
                lines.append(f"• 👤 <b>{name}</b> (UID: <code>{uid}</code>)\n  └ Profile ID: <code>{e.get('id', '')[:8]}...</code>")
            await send_message("👥 <b>DANH SÁCH NICK VIA ĐANG ONLINE:</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n\n".join(lines), chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        if lower_text.startswith("/history") or "lịch sử" in lower_text:
            jobs = history_store.list_jobs(5)
            if not jobs:
                await send_message("📜 <b>Chưa có lịch sử bài đăng nào.</b>", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return
            lines = []
            for j in jobs:
                st = "✅" if j.get("status") == "succeeded" else "❌"
                kind = "🎬 Reel" if j.get("kind") == "post_reel" else "🖼️ Ảnh"
                cap = j.get("caption", "").strip() or "(Không có caption)"
                if len(cap) > 30: cap = cap[:30] + "..."
                link = f" → <a href='{j['result']['permalinkUrl']}'>Xem link</a>" if j.get("result", {}).get("permalinkUrl") else ""
                lines.append(f"{st} <b>{kind}:</b> {cap}{link}")
            await send_message("📜 <b>5 BÀI ĐĂNG GẦN NHẤT:</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines), chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
            return

        # Handle Text with Video/Photo URL or Local File Path
        if text and not text.startswith("/"):
            import re
            url_match = re.search(r"https?://[^\s]+", text)
            local_path_match = re.search(r"[a-zA-Z]:\\[^\r\n]+\.(?:mp4|mov|jpg|jpeg|png)", text, re.IGNORECASE) or re.search(r"/[^\r\n]+\.(?:mp4|mov|jpg|jpeg|png)", text, re.IGNORECASE)
            
            dest = None
            media_type = "video"
            extracted_caption = text

            if local_path_match and Path(local_path_match.group(0)).exists():
                local_file = Path(local_path_match.group(0))
                dest = local_file
                media_type = "photo" if local_file.suffix.lower() in (".jpg", ".jpeg", ".png") else "video"
                extracted_caption = text.replace(local_path_match.group(0), "").strip()
                if not extracted_caption:
                    extracted_caption = extract_smart_caption(local_file.name)
                await send_message(f"📁 <i>Đã nhận file từ máy tính: {dest.name}</i>", chat_id=chat_id)
            elif url_match and ("http://" in url_match.group(0) or "https://" in url_match.group(0)) and not ("facebook.com" in url_match.group(0) or "t.me" in url_match.group(0)):
                raw_url = url_match.group(0)
                extracted_caption = text.replace(raw_url, "").strip().strip("|-: ").strip()
                if not extracted_caption:
                    extracted_caption = extract_smart_caption(raw_url)

                # Check Group Auto-Post Rule
                grp_cfg = get_groups_config().get(chat_id, {})
                if grp_cfg.get("default_page_id") and grp_cfg.get("auto_post"):
                    def_pid = grp_cfg["default_page_id"]
                    def_pname = grp_cfg.get("default_page_name") or f"Page {def_pid}"
                    extra_tags = grp_cfg.get("default_hashtags", "")
                    if extra_tags and extra_tags not in extracted_caption:
                        extracted_caption = f"{extracted_caption} {extra_tags}".strip()
                    
                    await send_message(f"🚀 <b>[Auto-Post Nhóm]</b> Đang tải & xuất bản video trực tiếp lên <b>{def_pname}</b>...", chat_id=chat_id)
                    dest, m_type, err = await _download_direct_url(raw_url)
                    if not dest or not dest.exists():
                        await send_message(f"❌ <b>Tải media thất bại:</b> {err or 'Lỗi kết nối'}", chat_id=chat_id)
                        return
                    clean_cap, sched = _extract_schedule(extracted_caption)
                    if m_type == "video":
                        await _execute_post_reel(chat_id, dest, clean_cap, page_id=def_pid, scheduled_publish_time=sched)
                    else:
                        await _execute_post_photo(chat_id, dest, clean_cap, page_id=def_pid, scheduled_publish_time=sched)
                    return

                # Fast probe in <200ms via HEAD request
                size_mb_str = ""
                media_type = "video"
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                        "Accept": "*/*",
                    }
                    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True, headers=headers) as client:
                        head_res = await client.head(raw_url)
                        if head_res.status_code in (200, 206):
                            cl = int(head_res.headers.get("content-length", 0))
                            if cl > 0:
                                size_mb_str = f" ({round(cl / (1024 * 1024), 1)} MB)"
                            ct = head_res.headers.get("content-type", "").lower()
                            if "image/" in ct or any(raw_url.lower().endswith(x) for x in (".jpg", ".jpeg", ".png", ".webp")):
                                media_type = "photo"
                except Exception:
                    pass

                # Launch background download task immediately without blocking menu display
                download_task = asyncio.create_task(_download_direct_url(raw_url))

                pages = pages_store.list_pages()
                exts = bridge_client.list_extensions()
                media_key = f"{'v' if media_type == 'video' else 'i'}{int(time.time()) % 100000}"
                
                target_map = {}
                inline_keyboard = []
                t_idx = 1
                for p in pages:
                    tk = str(t_idx)
                    target_map[tk] = {"page_id": p.get("id"), "ext_id": p.get("extensionId")}
                    inline_keyboard.append([{"text": f"📢 {p['name']}", "callback_data": f"p:{media_key}:{tk}"}])
                    t_idx += 1
                for e in exts:
                    tk = str(t_idx)
                    uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                    target_map[tk] = {"page_id": None, "ext_id": e["id"]}
                    inline_keyboard.append([{"text": f"👤 Nick cá nhân: {uname}", "callback_data": f"p:{media_key}:{tk}"}])
                    t_idx += 1
                inline_keyboard.append([
                    {"text": "✏️ Sửa Caption", "callback_data": f"p:{media_key}:edcap"},
                    {"text": "❌ Hủy bỏ", "callback_data": f"p:{media_key}:c"},
                ])
                
                _pending_media[media_key] = {
                    "kind": media_type,
                    "path": None,
                    "download_task": download_task,
                    "caption": extracted_caption or caption,
                    "targets": target_map,
                }
                
                cap_preview = f"\n📝 <b>Caption:</b> <i>{extracted_caption[:100]}</i>" if extracted_caption else ""
                media_label = "VIDEO" if media_type == "video" else "ẢNH"
                await send_message(
                    f"🎯 <b>CHỌN NƠI ĐĂNG {media_label}{size_mb_str}:</b>{cap_preview}\n\n<i>Bấm chọn Trang để đăng hoặc sửa Caption:</i>",
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": inline_keyboard},
                )
                return

        # Handle Media: Video
        video = msg.get("video") or msg.get("animation") or (msg.get("document") if msg.get("document", {}).get("mime_type", "").startswith("video/") else None)
        if video:
            if not caption:
                caption = extract_smart_caption(video.get("file_name") or "Video Reels")
            file_id = video.get("file_id")
            file_size_bytes = video.get("file_size", 0)
            file_size_mb = round((file_size_bytes / (1024 * 1024)), 1)

            if file_size_bytes > 20 * 1024 * 1024:
                big_msg = (
                    f"⚠️ <b>Video của bạn ({file_size_mb} MB) vượt quá giới hạn 20MB của Telegram Bot API.</b>\n\n"
                    "💡 <b>Cách khắc phục:</b>\n"
                    "1️⃣ <b>Gửi video &lt; 20MB</b> (Video Reels &lt; 90s chuẩn 1080p thường chỉ nặng khoảng 5–15MB).\n"
                    f"2️⃣ <b>Hoặc kéo thả trực tiếp vào Web Dashboard:</b> <code>http://127.0.0.1:47102/</code> (Không giới hạn dung lượng file)."
                )
                await send_message(big_msg, chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return

            await send_message(f"📥 <i>Đã nhận Video ({file_size_mb} MB). Đang tải về...</i>", chat_id=chat_id)
            dest = await _download_telegram_file(token, file_id, ".mp4")
            if not dest:
                await send_message("❌ Không thể tải video từ Telegram. Vui lòng thử lại!", chat_id=chat_id, reply_markup=_MAIN_KEYBOARD)
                return

            # Check Group Auto-Post Rule
            grp_cfg = get_groups_config().get(chat_id, {})
            if grp_cfg.get("default_page_id") and grp_cfg.get("auto_post"):
                def_pid = grp_cfg["default_page_id"]
                def_pname = grp_cfg.get("default_page_name") or f"Page {def_pid}"
                extra_tags = grp_cfg.get("default_hashtags", "")
                if extra_tags and extra_tags not in caption:
                    caption = f"{caption} {extra_tags}".strip()
                clean_cap, sched = _extract_schedule(caption)
                await send_message(f"🚀 <b>[Auto-Post Nhóm]</b> Đang xuất bản video trực tiếp lên <b>{def_pname}</b>...", chat_id=chat_id)
                await _execute_post_reel(chat_id, dest, clean_cap, page_id=def_pid, scheduled_publish_time=sched)
                return

            pages = pages_store.list_pages()
            exts = bridge_client.list_extensions()
            media_key = f"v{int(time.time()) % 100000}"

            target_map = {}
            inline_keyboard = []
            t_idx = 1
            for p in pages:
                tk = str(t_idx)
                target_map[tk] = {"page_id": p.get("id"), "ext_id": p.get("extensionId")}
                inline_keyboard.append([{"text": f"📢 {p['name']}", "callback_data": f"p:{media_key}:{tk}"}])
                t_idx += 1
            for e in exts:
                tk = str(t_idx)
                uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                target_map[tk] = {"page_id": None, "ext_id": e["id"]}
                inline_keyboard.append([{"text": f"👤 Nick cá nhân: {uname}", "callback_data": f"p:{media_key}:{tk}"}])
                t_idx += 1
            inline_keyboard.append([{"text": "❌ Hủy bỏ", "callback_data": f"p:{media_key}:c"}])

            _pending_media[media_key] = {"kind": "video", "path": dest, "caption": caption, "targets": target_map}

            cap_preview = f"\n📝 <b>Caption:</b> <i>{caption}</i>" if caption else ""
            await send_message(
                f"🎯 <b>CHỌN NƠI ĐĂNG VIDEO:</b>{cap_preview}\n\n<i>Bấm chọn Trang hoặc Nick để xuất bản ngay:</i>",
                chat_id=chat_id,
                reply_markup={"inline_keyboard": inline_keyboard},
            )
            return

        # Handle Media: Photo
        photos = msg.get("photo")
        if photos and isinstance(photos, list) and len(photos) > 0:
            best_photo = photos[-1]
            file_id = best_photo.get("file_id")
            await send_message("📥 <i>Đã nhận Ảnh. Đang xử lý...</i>", chat_id=chat_id)
            dest = await _download_telegram_file(token, file_id, ".jpg")
            if not dest:
                await send_message("❌ Không thể tải ảnh từ Telegram.", chat_id=chat_id)
                return

            # Check Group Auto-Post Rule
            grp_cfg = get_groups_config().get(chat_id, {})
            if grp_cfg.get("default_page_id") and grp_cfg.get("auto_post"):
                def_pid = grp_cfg["default_page_id"]
                def_pname = grp_cfg.get("default_page_name") or f"Page {def_pid}"
                extra_tags = grp_cfg.get("default_hashtags", "")
                if extra_tags and extra_tags not in caption:
                    caption = f"{caption} {extra_tags}".strip()
                clean_cap, sched = _extract_schedule(caption)
                await send_message(f"🚀 <b>[Auto-Post Nhóm]</b> Đang xuất bản ảnh trực tiếp lên <b>{def_pname}</b>...", chat_id=chat_id)
                await _execute_post_photo(chat_id, dest, clean_cap, page_id=def_pid, scheduled_publish_time=sched)
                return

            pages = pages_store.list_pages()
            exts = bridge_client.list_extensions()
            media_key = f"i{int(time.time()) % 100000}"

            target_map = {}
            inline_keyboard = []
            t_idx = 1
            for p in pages:
                tk = str(t_idx)
                target_map[tk] = {"page_id": p.get("id"), "ext_id": p.get("extensionId")}
                inline_keyboard.append([{"text": f"📢 {p['name']}", "callback_data": f"p:{media_key}:{tk}"}])
                t_idx += 1
            for e in exts:
                tk = str(t_idx)
                uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                target_map[tk] = {"page_id": None, "ext_id": e["id"]}
                inline_keyboard.append([{"text": f"👤 Nick cá nhân: {uname}", "callback_data": f"p:{media_key}:{tk}"}])
                t_idx += 1
            inline_keyboard.append([{"text": "❌ Hủy bỏ", "callback_data": f"p:{media_key}:c"}])

            _pending_media[media_key] = {"kind": "photo", "path": dest, "caption": caption, "targets": target_map}

            cap_preview = f"\n📝 <b>Caption:</b> <i>{caption}</i>" if caption else ""
            await send_message(
                f"🎯 <b>CHỌN NƠI ĐĂNG ẢNH:</b>{cap_preview}\n\n<i>Bấm chọn Trang hoặc Nick để xuất bản ngay:</i>",
                chat_id=chat_id,
                reply_markup={"inline_keyboard": inline_keyboard},
            )
            return
    except Exception as exc:
        logger.error("error processing telegram update: %s", exc, exc_info=True)


async def _polling_loop():
    logger.info("telegram bot polling loop started")
    offset = 0
    while True:
        try:
            cfg = get_config()
            token = cfg.get("token")
            enabled = cfg.get("enabled")
            if not enabled or not token:
                await asyncio.sleep(5.0)
                continue

            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=15"
            async with httpx.AsyncClient(timeout=25.0, trust_env=False) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    updates = data.get("result", [])
                    for update in updates:
                        offset = max(offset, update.get("update_id", 0) + 1)
                        asyncio.create_task(_handle_update(token, update))
                elif resp.status_code == 401 or resp.status_code == 404:
                    logger.warning("telegram bot token invalid (status %s), waiting...", resp.status_code)
                    await asyncio.sleep(10.0)
                else:
                    await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("telegram polling error: %s", exc)
            await asyncio.sleep(3.0)
    logger.info("telegram bot polling loop stopped")


def start_bot_task():
    global _bot_task
    if _bot_task is None or _bot_task.done():
        _bot_task = asyncio.create_task(_polling_loop(), name="telegram-bot-polling")


def stop_bot_task():
    global _bot_task
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        _bot_task = None
