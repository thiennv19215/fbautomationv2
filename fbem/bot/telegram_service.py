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
_pending_media: dict[str, dict] = {}  # temp store for media awaiting inline button selection


def _read_env_file() -> dict[str, str]:
    if not _ENV_PATH.exists():
        return {}
    res = {}
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                res[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as exc:
        logger.warning("failed to read .env: %s", exc)
    return res


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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return {"ok": True, "message": "Gửi tin nhắn test thành công!"}
            data = resp.json()
            return {"ok": False, "error": data.get("description", f"Lỗi HTTP {resp.status_code}")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _download_telegram_file(token: str, file_id: str, ext: str) -> Optional[Path]:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
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


async def _download_direct_url(url: str, ext: str = ".mp4") -> Optional[Path]:
    """Download video from a direct URL (no 20MB limit!)."""
    try:
        out_dir = media_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"url_{int(time.time())}_{abs(hash(url)) % 100000}{ext}"
        dest = out_dir / filename

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None
                with open(dest, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
        return dest
    except Exception as exc:
        logger.error("failed to download direct URL %s: %s", url, exc)
        return None


async def _execute_post_reel(chat_id: str, dest_path: Path, caption: str, page_id: Optional[str] = None, ext_id: Optional[str] = None):
    if page_id and (not ext_id or ext_id == "default"):
        p_info = pages_store.get_page(page_id)
        if p_info and p_info.get("extensionId"):
            ext_id = p_info["extensionId"]

    await send_message("⏳ <b>Đang đẩy Video Reel lên Facebook...</b>\n<i>Vui lòng đợi vài giây để hệ thống xuất bản.</i>", chat_id=chat_id)
    template = capture_store.load_template(ext_id)
    if not capture_store.template_complete(template):
        await send_message(
            "⚠️ <b>Chưa có Mẫu Reel:</b>\nVui lòng mở Chrome đăng tay 1 Reel trên Facebook để hệ thống lấy mẫu trước.",
            chat_id=chat_id,
        )
        return

    staged_url = f"http://127.0.0.1:{HTTP_PORT}/local-video?name={quote(dest_path.name)}"
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if page_id else None

    job = history_store.add_job("post_reel", {"videoUrl": staged_url, "caption": caption}, extension_id=ext_id, page_id=page_id, caption=caption)
    try:
        resp = await bridge_client.post_reel(
            video_url=staged_url,
            caption=caption,
            page_id=page_id,
            template=template,
            switch_template=switch_tpl,
            extension_id=ext_id,
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

        msg_text = (
            "🎉 <b>XUẤT BẢN REEL THÀNH CÔNG!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Video ID:</b> <code>{res.get('videoId')}</code>\n"
            f"📝 <b>Caption:</b> <i>{caption[:80]}...</i>\n"
            "🗑️ <i>Đã tự động xóa file tạm trên máy tính.</i>"
        )
        markup = None
        if res.get("permalinkUrl"):
            markup = {"inline_keyboard": [[{"text": "👉 Xem Video trên Facebook ↗", "url": res["permalinkUrl"]}]]}
        await send_message(msg_text, chat_id=chat_id, reply_markup=markup)
    except Exception as exc:
        history_store.update_job(job["id"], "failed", error=str(exc))
        await send_message(f"❌ <b>Lỗi đăng bài:</b> {exc}", chat_id=chat_id)


async def _execute_post_photo(chat_id: str, dest_path: Path, caption: str, page_id: Optional[str] = None, ext_id: Optional[str] = None):
    if page_id and (not ext_id or ext_id == "default"):
        p_info = pages_store.get_page(page_id)
        if p_info and p_info.get("extensionId"):
            ext_id = p_info["extensionId"]

    await send_message("⏳ <b>Đang đẩy Ảnh lên Facebook...</b>\n<i>Vui lòng đợi vài giây để hệ thống xuất bản.</i>", chat_id=chat_id)
    template = capture_store.load_template(ext_id)
    if not capture_store.photo_template_complete(template):
        await send_message(
            "⚠️ <b>Chưa có Mẫu Ảnh:</b>\nVui lòng mở Chrome đăng tay 1 ảnh trên Facebook để hệ thống lấy mẫu trước.",
            chat_id=chat_id,
        )
        return

    staged_url = f"http://127.0.0.1:{HTTP_PORT}/local-image?name={quote(dest_path.name)}"
    switch_tpl = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation") if page_id else None

    job = history_store.add_job("post_photos", {"imageUrls": [staged_url], "caption": caption}, extension_id=ext_id, page_id=page_id, caption=caption)
    try:
        resp = await bridge_client.post_photos(
            image_urls=[staged_url],
            caption=caption,
            page_id=page_id,
            template=template,
            switch_template=switch_tpl,
            extension_id=ext_id,
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

        msg_text = (
            "🎉 <b>ĐĂNG ẢNH THÀNH CÔNG!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Post ID:</b> <code>{res.get('postId')}</code>\n"
            f"📝 <b>Caption:</b> <i>{caption[:80]}...</i>\n"
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


def _extract_schedule(caption: str) -> tuple[str, Optional[int]]:
    """Check if caption contains #schedule YYYY-MM-DD HH:MM or #hengio and return clean caption + epoch."""
    import re
    from datetime import datetime

    pattern = r"#(?:schedule|hengio)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
    match = re.search(pattern, caption, re.IGNORECASE)
    if match:
        dt_str = match.group(1)
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            epoch = int(dt.timestamp())
            clean_cap = re.sub(pattern, "", caption, flags=re.IGNORECASE).strip()
            return clean_cap, epoch
        except Exception:
            pass
    return caption, None


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

            if data == "cancel":
                await send_message("❌ <b>Đã hủy bài đăng.</b>", chat_id=chat_id)
                return

            if data.startswith("post:"):
                # format: post:<media_key>:<page_id>:<ext_id>
                parts = data.split(":")
                if len(parts) >= 3:
                    media_key = parts[1]
                    target_page = parts[2]
                    target_ext = parts[3] if len(parts) >= 4 and parts[3] != "default" else None
                    pending = _pending_media.pop(media_key, None)
                    if pending:
                        p_id = None if target_page == "default" else target_page
                        clean_cap, sched = _extract_schedule(pending["caption"])
                        if pending["kind"] == "video":
                            await _execute_post_reel(chat_id, pending["path"], clean_cap, page_id=p_id, ext_id=target_ext)
                        else:
                            await _execute_post_photo(chat_id, pending["path"], clean_cap, page_id=p_id, ext_id=target_ext)
            return

        # 2. Handle Messages
        msg = update.get("message")
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id"))
        text = (msg.get("text") or "").strip()
        caption = (msg.get("caption") or "").strip()
        lower_text = text.lower()

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

        # Handle Text with Video URL or Local File Path
        if text and not text.startswith("/"):
            import re
            url_match = re.search(r"https?://[^\s]+\.(?:mp4|mov|avi|webm)(?:\?[^\s]*)?", text, re.IGNORECASE) or re.search(r"https?://[^\s]+", text)
            local_path_match = re.search(r"[a-zA-Z]:\\[^\r\n]+\.mp4", text, re.IGNORECASE) or re.search(r"/[^\r\n]+\.mp4", text, re.IGNORECASE)
            
            dest = None
            extracted_caption = text

            if local_path_match and Path(local_path_match.group(0)).exists():
                local_file = Path(local_path_match.group(0))
                dest = local_file
                extracted_caption = text.replace(local_path_match.group(0), "").strip()
                await send_message(f"📁 <i>Đã nhận file từ máy tính: {dest.name}</i>", chat_id=chat_id)
            elif url_match and ("http://" in url_match.group(0) or "https://" in url_match.group(0)) and not ("facebook.com" in url_match.group(0) or "t.me" in url_match.group(0)):
                video_url = url_match.group(0)
                extracted_caption = text.replace(video_url, "").strip()
                await send_message("🌐 <i>Đang tải Video trực tiếp từ Link (không giới hạn 20MB)...</i>", chat_id=chat_id)
                dest = await _download_direct_url(video_url, ".mp4")
                if not dest:
                    await send_message("❌ Không thể tải video từ đường dẫn này. Vui lòng kiểm tra lại link trực tiếp!", chat_id=chat_id)

            if dest:
                pages = pages_store.list_pages()
                exts = bridge_client.list_extensions()
                media_key = f"vid_{int(time.time())}"
                _pending_media[media_key] = {"kind": "video", "path": dest, "caption": extracted_caption or caption}
                inline_keyboard = []
                for p in pages:
                    inline_keyboard.append([{"text": f"📢 {p['name']}", "callback_data": f"post:{media_key}:{p['id']}:{p.get('extensionId') or 'default'}"}])
                for e in exts:
                    uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                    inline_keyboard.append([{"text": f"👤 Nick cá nhân: {uname}", "callback_data": f"post:{media_key}:default:{e['id']}"}])
                inline_keyboard.append([{"text": "❌ Hủy bỏ", "callback_data": "cancel"}])
                
                cap_preview = f"\n📝 <b>Caption:</b> <i>{extracted_caption[:80]}...</i>" if extracted_caption else ""
                await send_message(
                    f"🎯 <b>CHỌN NƠI ĐĂNG VIDEO:</b>{cap_preview}\n\n<i>Bấm chọn Trang hoặc Nick để xuất bản ngay:</i>",
                    chat_id=chat_id,
                    reply_markup={"inline_keyboard": inline_keyboard},
                )
                return

        # Handle Media: Video
        video = msg.get("video") or msg.get("animation") or (msg.get("document") if msg.get("document", {}).get("mime_type", "").startswith("video/") else None)
        if video:
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

            pages = pages_store.list_pages()
            exts = bridge_client.list_extensions()
            ext_user_map = {}
            for e in exts:
                uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                ext_user_map[e["id"]] = uname

            media_key = f"vid_{int(time.time())}"
            _pending_media[media_key] = {"kind": "video", "path": dest, "caption": caption}
            inline_keyboard = []

            # 1. Add buttons for Fanpages (clean, 1-click auto routing)
            for p in pages:
                inline_keyboard.append([{"text": f"📢 {p['name']}", "callback_data": f"post:{media_key}:{p['id']}:{p.get('extensionId') or 'default'}"}])

            # 2. Add buttons for personal profiles
            for e in exts:
                uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                inline_keyboard.append([{"text": f"👤 Nick cá nhân: {uname}", "callback_data": f"post:{media_key}:default:{e['id']}"}])

            inline_keyboard.append([{"text": "❌ Hủy bỏ", "callback_data": "cancel"}])
            markup = {"inline_keyboard": inline_keyboard}

            cap_preview = f"\n📝 <b>Caption:</b> <i>{caption}</i>" if caption else ""
            await send_message(
                f"🎯 <b>CHỌN NƠI ĐĂNG VIDEO:</b>{cap_preview}\n\n<i>Bấm chọn Trang hoặc Nick để xuất bản ngay:</i>",
                chat_id=chat_id,
                reply_markup=markup,
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

            pages = pages_store.list_pages()
            exts = bridge_client.list_extensions()

            media_key = f"img_{int(time.time())}"
            _pending_media[media_key] = {"kind": "photo", "path": dest, "caption": caption}
            inline_keyboard = []

            for p in pages:
                inline_keyboard.append([{"text": f"📢 {p['name']}", "callback_data": f"post:{media_key}:{p['id']}:{p.get('extensionId') or 'default'}"}])

            for e in exts:
                uname = (e.get("fbUser") or {}).get("name") or f"Nick {e['id'][:6]}"
                inline_keyboard.append([{"text": f"👤 Nick cá nhân: {uname}", "callback_data": f"post:{media_key}:default:{e['id']}"}])

            inline_keyboard.append([{"text": "❌ Hủy bỏ", "callback_data": "cancel"}])
            markup = {"inline_keyboard": inline_keyboard}

            cap_preview = f"\n📝 <b>Caption:</b> <i>{caption}</i>" if caption else ""
            await send_message(
                f"🎯 <b>CHỌN NƠI ĐĂNG ẢNH:</b>{cap_preview}\n\n<i>Bấm chọn Trang hoặc Nick để xuất bản ngay:</i>",
                chat_id=chat_id,
                reply_markup=markup,
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
            async with httpx.AsyncClient(timeout=25.0) as client:
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
