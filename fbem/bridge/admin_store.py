"""SQLite persistence for dashboard accounts, scripts and queued jobs."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from .config import home_dir


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    home_dir().mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home_dir() / "fbem.db", timeout=20)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    with _connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, facebook_id TEXT NOT NULL,
          extension_id TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          account_type TEXT DEFAULT 'page', parent_id TEXT, notes TEXT DEFAULT '',
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scripts (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL, config_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, account_id TEXT NOT NULL, extension_id TEXT NOT NULL,
          script_id TEXT, kind TEXT NOT NULL, input_json TEXT NOT NULL,
          status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
          attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2,
          result_json TEXT, error TEXT, created_at INTEGER NOT NULL,
          started_at INTEGER, finished_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
        CREATE INDEX IF NOT EXISTS jobs_account_status ON jobs(account_id, status);
        """)
        cols = {col[1] for col in db.execute("PRAGMA table_info(accounts)").fetchall()}
        if "account_type" not in cols:
            db.execute("ALTER TABLE accounts ADD COLUMN account_type TEXT DEFAULT 'page'")
        if "parent_id" not in cols:
            db.execute("ALTER TABLE accounts ADD COLUMN parent_id TEXT")
        if "notes" not in cols:
            db.execute("ALTER TABLE accounts ADD COLUMN notes TEXT DEFAULT ''")
        if "assigned_folder" not in cols:
            db.execute("ALTER TABLE accounts ADD COLUMN assigned_folder TEXT DEFAULT ''")
        if "default_script_id" not in cols:
            db.execute("ALTER TABLE accounts ADD COLUMN default_script_id TEXT DEFAULT ''")

        job_cols = {col[1] for col in db.execute("PRAGMA table_info(jobs)").fetchall()}
        if "run_at" not in job_cols:
            db.execute("ALTER TABLE jobs ADD COLUMN run_at INTEGER DEFAULT 0")

        db.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
          key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at INTEGER NOT NULL
        );
        """)

        # Seed default script templates if empty
        script_count = db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
        if script_count == 0:
            now = int(time.time())
            default_templates = [
                (
                    "tpl-reel-viral",
                    "Nuôi Reel Xu Hướng (Viral Feed)",
                    "Mẫu đăng Reel tăng tương tác tự nhiên với hashtag viral tối ưu thuật toán Facebook Reels.",
                    "post_reel",
                    json.dumps({
                        "caption": "Cả nhà thấy clip này thế nào? Đừng quên thả tim và follow kênh để xem thêm nhiều video thú vị mỗi ngày nhé! ❤️",
                        "hashtags": "#reels #xuhuong #trending #viral #fyp #facebookreels #fbem",
                        "videoUrl": ""
                    }, ensure_ascii=False),
                    1, 1, now, now
                ),
                (
                    "tpl-review-affiliate",
                    "Review Sản Phẩm & Bán Hàng",
                    "Mẫu review sản phẩm, kích thích mua sắm và điều hướng comment / inbox mua hàng.",
                    "post_reel",
                    json.dumps({
                        "caption": "Review siêu phẩm đỉnh chóp cho cả nhà tham khảo. Chi tiết thông tin và link mua chính hãng để ở dưới comment nha! 🔥",
                        "hashtags": "#review #muasam #shopping #dealhot #xuhuong #reels #sanphammoi",
                        "videoUrl": ""
                    }, ensure_ascii=False),
                    1, 1, now, now
                ),
                (
                    "tpl-album-photos",
                    "Đăng Album Ảnh & Khuyến Mãi",
                    "Mẫu đăng 1 hoặc nhiều ảnh với lời chào theo tên Fanpage {{page_name}} và ngày đăng tự động {{date}}.",
                    "post_photos",
                    json.dumps({
                        "caption": "BST mới nhất đã cập bến nhà {{page_name}} ngày {{date}}! 🎁 Inbox ngay để nhận ưu đãi đặc biệt hôm nay.",
                        "hashtags": "#album #sanphammoi #khuyenmai #sale #hotdeal #feedback",
                        "imageUrls": []
                    }, ensure_ascii=False),
                    1, 1, now, now
                ),
                (
                    "tpl-tips-knowledge",
                    "Chia Sẻ Kiến Thức & Tips Hay",
                    "Mẫu video chia sẻ mẹo vặt, bài học hữu ích nhằm xây dựng tệp tương tác trung thành.",
                    "post_reel",
                    json.dumps({
                        "caption": "Bí quyết hữu ích mỗi ngày dành cho bạn. Lưu lại ngay kẻo quên nhé! Chúc cả nhà một ngày tràn đầy năng lượng! ✨",
                        "hashtags": "#kienthuc #meohay #lifestyle #kinhnghiem #xuhuong #reels",
                        "videoUrl": ""
                    }, ensure_ascii=False),
                    1, 1, now, now
                ),
            ]
            db.executemany("""
                INSERT OR IGNORE INTO scripts
                (id, name, description, kind, config_json, enabled, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, default_templates)


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for field in ("config_json", "input_json", "result_json"):
        if field in out:
            value = out.pop(field)
            out[field.removesuffix("_json")] = json.loads(value) if value else None
    for field in ("enabled",):
        if field in out:
            out[field] = bool(out[field])
    return out


def list_rows(table: str) -> list[dict]:
    if table not in {"accounts", "scripts", "jobs"}:
        raise ValueError("invalid table")
    order = "created_at DESC" if table == "jobs" else "updated_at DESC"
    with _connect() as db:
        return [_row(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()]


def list_jobs(status: str | None = None, limit: int = 100) -> list[dict]:
    with _connect() as db:
        if status:
            rows = db.execute("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]


def get_row(table: str, item_id: str) -> dict | None:
    if table not in {"accounts", "scripts", "jobs"}:
        raise ValueError("invalid table")
    with _connect() as db:
        return _row(db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())


def save_account(data: dict, item_id: str | None = None) -> dict:
    now = int(time.time())
    item_id = item_id or str(uuid.uuid4())
    name = str(data.get("name", "")).strip()
    facebook_id = str(data.get("facebookId") or data.get("facebook_id") or "").strip()
    extension_id = str(data.get("extensionId") or data.get("extension_id") or "").strip()
    account_type = str(data.get("accountType") or data.get("account_type") or "page").strip()
    parent_id = data.get("parentId") or data.get("parent_id")
    notes = str(data.get("notes") or "")
    assigned_folder = str(data.get("assignedFolder") or data.get("assigned_folder") or "").strip()
    default_script_id = str(data.get("defaultScriptId") or data.get("default_script_id") or "").strip()
    enabled = int(data.get("enabled", True))

    with _connect() as db:
        existing = db.execute("SELECT created_at FROM accounts WHERE id=?", (item_id,)).fetchone()
        db.execute("""INSERT OR REPLACE INTO accounts
          (id,name,facebook_id,extension_id,enabled,account_type,parent_id,notes,assigned_folder,default_script_id,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (item_id, name, facebook_id, extension_id, enabled,
           account_type, parent_id, notes, assigned_folder, default_script_id,
           existing[0] if existing else now, now))
    return get_row("accounts", item_id) or {}


def save_script(data: dict, item_id: str | None = None) -> dict:
    now = int(time.time())
    item_id = item_id or str(uuid.uuid4())
    with _connect() as db:
        existing = db.execute("SELECT created_at,version FROM scripts WHERE id=?", (item_id,)).fetchone()
        db.execute("""INSERT OR REPLACE INTO scripts
          (id,name,description,kind,config_json,enabled,version,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?)""", (item_id, data["name"].strip(), data.get("description", ""),
          data["kind"], json.dumps(data.get("config") or {}, ensure_ascii=False),
          int(data.get("enabled", True)), (existing[1] + 1) if existing else 1,
          existing[0] if existing else now, now))
    return get_row("scripts", item_id) or {}


def get_queue_settings() -> dict:
    with _connect() as db:
        row = db.execute("SELECT value_json FROM system_settings WHERE key='queue_settings'").fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                pass
    return {
        "staggerSeconds": 30,
        "jitterSeconds": 10,
        "autoStagger": True,
    }


def save_queue_settings(settings: dict) -> dict:
    current = get_queue_settings()
    current.update(settings)
    now = int(time.time())
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO system_settings (key, value_json, updated_at) VALUES ('queue_settings', ?, ?)",
            (json.dumps(current, ensure_ascii=False), now),
        )
    return current


def create_job(account: dict, kind: str, payload: dict, *, script_id: str | None = None,
               idempotency_key: str | None = None, run_at: int | None = None) -> dict:
    now = int(time.time())
    item_id = str(uuid.uuid4())
    key = idempotency_key or item_id
    scheduled_run_at = run_at if run_at is not None else now
    with _connect() as db:
        try:
            db.execute("""INSERT INTO jobs
              (id,account_id,extension_id,script_id,kind,input_json,status,idempotency_key,created_at,run_at)
              VALUES(?,?,?,?,?,?,?,?,?,?)""", (item_id, account["id"], account["extension_id"], script_id,
              kind, json.dumps(payload, ensure_ascii=False), "queued", key, now, scheduled_run_at))
        except sqlite3.IntegrityError:
            row = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            return _row(row) or {}
    return get_row("jobs", item_id) or {}


def claim_next_job() -> dict | None:
    """Atomically claim a job whose account has no running job and whose run_at timestamp has passed."""
    now = int(time.time())
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT j.* FROM jobs j
          WHERE (
            (j.status='queued' AND COALESCE(j.run_at, 0) <= ?)
            OR (j.status='waiting_connection' AND COALESCE(j.started_at,0) <= ?)
          )
          AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.account_id=j.account_id AND r.status='running')
          ORDER BY COALESCE(j.run_at, j.created_at) ASC, j.created_at ASC LIMIT 1""", (now, now - 5)).fetchone()
        if not row:
            return None
        updated = db.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=? AND status IN ('queued','waiting_connection')",
                             (now, row["id"])).rowcount
        if not updated:
            return None
    return get_row("jobs", row["id"])


def finish_job(item_id: str, status: str, *, result: Any = None, error: str | None = None) -> None:
    with _connect() as db:
        db.execute("UPDATE jobs SET status=?,result_json=?,error=?,finished_at=? WHERE id=?",
                   (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error, int(time.time()) if status in {"succeeded", "failed", "cancelled"} else None, item_id))


def retry_or_fail(item_id: str, error: str, waiting: bool = False) -> None:
    job = get_row("jobs", item_id)
    if not job:
        return
    if waiting:
        with _connect() as db:
            db.execute("UPDATE jobs SET status='waiting_connection',error=?,attempts=MAX(0,attempts-1),started_at=? WHERE id=?",
                       (error, int(time.time()), item_id))
    elif job["attempts"] < job["max_attempts"]:
        finish_job(item_id, "queued", error=error)
    else:
        finish_job(item_id, "failed", error=error)


def cancel_job(item_id: str) -> bool:
    with _connect() as db:
        return bool(db.execute("UPDATE jobs SET status='cancelled',finished_at=? WHERE id=? AND status IN ('queued','waiting_connection')",
                               (int(time.time()), item_id)).rowcount)


def retry_job(item_id: str) -> bool:
    """Requeue a terminal job while preserving its idempotent audit record."""
    with _connect() as db:
        return bool(db.execute("""UPDATE jobs SET status='queued',attempts=0,error=NULL,
            result_json=NULL,started_at=NULL,finished_at=NULL
            WHERE id=? AND status IN ('failed','cancelled')""", (item_id,)).rowcount)


def delete_account(item_id: str) -> tuple[bool, str | None]:
    with _connect() as db:
        active = db.execute("SELECT 1 FROM jobs WHERE account_id=? AND status IN ('queued','running','waiting_connection') LIMIT 1", (item_id,)).fetchone()
        if active:
            return False, "account_has_active_jobs"
        deleted = db.execute("DELETE FROM accounts WHERE id=?", (item_id,)).rowcount
        return bool(deleted), None if deleted else "account_not_found"


def delete_script(item_id: str) -> bool:
    with _connect() as db:
        # Jobs retain their expanded input and result as an immutable audit trail.
        db.execute("UPDATE jobs SET script_id=NULL WHERE script_id=?", (item_id,))
        return bool(db.execute("DELETE FROM scripts WHERE id=?", (item_id,)).rowcount)


def dashboard_stats() -> dict:
    now = int(time.time())
    day_start = now - (now % 86400)
    with _connect() as db:
        rows = db.execute("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
        statuses = {row["status"]: row["count"] for row in rows}
        today = db.execute("SELECT COUNT(*) FROM jobs WHERE status='succeeded' AND finished_at>=?", (day_start,)).fetchone()[0]
        return {
            "accounts": db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "enabledAccounts": db.execute("SELECT COUNT(*) FROM accounts WHERE enabled=1").fetchone()[0],
            "scripts": db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0],
            "jobsByStatus": statuses,
            "succeededToday": today,
        }


def count_active_jobs_with_media(rel_or_path: str, exclude_job_id: str | None = None) -> int:
    """Count other active jobs that reference this media file before deleting."""
    if not rel_or_path:
        return 0
    from pathlib import Path
    clean = rel_or_path.strip().replace("\\", "/").lstrip("/")
    base_name = Path(clean).name
    with _connect() as db:
        query = "SELECT id, input_json FROM jobs WHERE status IN ('queued', 'running', 'waiting_connection')"
        params = []
        if exclude_job_id:
            query += " AND id != ?"
            params.append(exclude_job_id)
        rows = db.execute(query, params).fetchall()
        count = 0
        for r in rows:
            inp = r["input_json"] or ""
            if clean in inp or base_name in inp:
                count += 1
        return count

