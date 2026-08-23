"""Background dispatcher for account-isolated automation jobs."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from . import admin_store, capture_store
from .bridge_client import bridge_client
from .text_utils import normalize_browser_text

logger = logging.getLogger(__name__)


def _render(value, account: dict):
    """Render the small, deterministic variable set supported by scripts."""
    if isinstance(value, str):
        values = {
            "{{account_name}}": account.get("name", ""),
            "{{page_name}}": account.get("name", ""),
            "{{facebook_id}}": account.get("facebook_id", ""),
            "{{page_id}}": account.get("facebook_id", ""),
            "{{date}}": datetime.now().strftime("%Y-%m-%d"),
        }
        for needle, replacement in values.items():
            value = value.replace(needle, str(replacement))
        return value
    if isinstance(value, list):
        return [_render(item, account) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, account) for key, item in value.items()}
    return value


async def execute_job(job: dict) -> None:
    session = bridge_client.get_session(job.get("extension_id"))
    if session is None or not getattr(session, "ws", None):
        admin_store.retry_or_fail(job["id"], "extension_not_connected", waiting=True)
        return
    account = admin_store.get_row("accounts", job["account_id"])
    if not account or not account.get("enabled"):
        admin_store.finish_job(job["id"], "failed", error="account_missing_or_disabled")
        return
    template = capture_store.load_template(session.extension_id) or {}
    payload = _render(dict(job.get("input") or {}), account)
    payload["pageId"] = account["facebook_id"]
    payload["template"] = template
    payload["switchTemplate"] = (template.get("graphql_ops") or {}).get("CometProfileSwitchMutation")
    method = job["kind"]
    if method == "post_reel" and not capture_store.template_complete(template):
        admin_store.finish_job(job["id"], "failed", error="no_reel_template_captured")
        return
    if method == "post_photos" and not capture_store.photo_template_complete(template):
        admin_store.finish_job(job["id"], "failed", error="no_photo_template_captured")
        return
    if method not in {"post_reel", "post_photos", "switch_profile", "get_identity"}:
        admin_store.finish_job(job["id"], "failed", error=f"unsupported_job_kind: {method}")
        return
    try:
        lock = getattr(session, "operation_lock", None)
        if lock is not None:
            async with lock:
                response = await bridge_client._send(method, payload, extension_id=session.extension_id, timeout=300.0)
        else:
            response = await bridge_client._send(method, payload, extension_id=session.extension_id, timeout=300.0)

        if response.get("error") or (isinstance(response.get("status"), int) and response["status"] >= 400):
            admin_store.retry_or_fail(job["id"], str(response.get("error") or response))
        else:
            res_data = normalize_browser_text(response.get("data") or response)
            admin_store.finish_job(job["id"], "succeeded", result=res_data)
            try:
                from ..bot import telegram_service as telegram_bot
                caption_txt = str(payload.get("caption") or "")[:60]
                permalink = res_data.get("permalinkUrl") if isinstance(res_data, dict) else None
                asyncio.create_task(
                    telegram_bot.send_notification(
                        f"🎉 <b>[Hàng Đợi] Đăng bài thành công!</b>\n"
                        f"📄 Page: <b>{account.get('name')}</b>\n"
                        f"📝 Caption: <i>{caption_txt}...</i>",
                        permalink=permalink,
                    )
                )
            except Exception as tg_err:
                logger.warning("Telegram notification failed for job %s: %s", job["id"], tg_err)

            try:
                from .server import cleanup_media_file
                if method == "post_reel" and payload.get("videoUrl"):
                    cleanup_media_file(str(payload["videoUrl"]), job_id=job["id"])
                elif method == "post_photos" and payload.get("imageUrls"):
                    for img in payload["imageUrls"]:
                        cleanup_media_file(str(img), job_id=job["id"])
            except Exception as clean_err:
                logger.warning(f"Error cleaning media for job {job['id']}: {clean_err}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("job %s failed", job["id"])
        admin_store.retry_or_fail(job["id"], str(exc))


async def run_dispatcher() -> None:
    admin_store.init_db()
    running: set[asyncio.Task] = set()
    while True:
        running = {task for task in running if not task.done()}
        job = await asyncio.to_thread(admin_store.claim_next_job)
        if job:
            task = asyncio.create_task(execute_job(job), name=f"fbem-job-{job['id']}")
            running.add(task)
        else:
            await asyncio.sleep(0.5)
