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


def get_row(table: str, item_id: str) -> dict | None:
    if table not in {"accounts", "scripts", "jobs"}:
        raise ValueError("invalid table")
    with _connect() as db:
        return _row(db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())


def save_account(data: dict, item_id: str | None = None) -> dict:
    now = int(time.time())
    item_id = item_id or str(uuid.uuid4())
    with _connect() as db:
        existing = db.execute("SELECT created_at FROM accounts WHERE id=?", (item_id,)).fetchone()
        db.execute("""INSERT OR REPLACE INTO accounts
          (id,name,facebook_id,extension_id,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
          (item_id, data["name"].strip(), str(data["facebookId"]).strip(),
           str(data["extensionId"]).strip(), int(data.get("enabled", True)),
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


def create_job(account: dict, kind: str, payload: dict, *, script_id: str | None = None,
               idempotency_key: str | None = None) -> dict:
    now = int(time.time())
    item_id = str(uuid.uuid4())
    key = idempotency_key or item_id
    with _connect() as db:
        try:
            db.execute("""INSERT INTO jobs
              (id,account_id,extension_id,script_id,kind,input_json,status,idempotency_key,created_at)
              VALUES(?,?,?,?,?,?,?,?,?)""", (item_id, account["id"], account["extension_id"], script_id,
              kind, json.dumps(payload, ensure_ascii=False), "queued", key, now))
        except sqlite3.IntegrityError:
            row = db.execute("SELECT * FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
            return _row(row) or {}
    return get_row("jobs", item_id) or {}


def claim_next_job() -> dict | None:
    """Atomically claim a job whose account has no running job."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT j.* FROM jobs j
          WHERE (j.status='queued' OR (j.status='waiting_connection' AND COALESCE(j.started_at,0) <= ?))
          AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.account_id=j.account_id AND r.status='running')
          ORDER BY j.created_at LIMIT 1""", (int(time.time()) - 5,)).fetchone()
        if not row:
            return None
        updated = db.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=? AND status IN ('queued','waiting_connection')",
                             (int(time.time()), row["id"])).rowcount
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
