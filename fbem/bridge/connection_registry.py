"""Multi-extension connection registry.

Each Chrome profile owns one stable ``extension_id`` and one independent
WebSocket session. Requests, callback authentication, identity and telemetry
are isolated per session so connecting a second extension never replaces the
first one.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .text_utils import normalize_browser_text


@dataclass
class ExtensionSession:
    extension_id: str
    ws: Any
    callback_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    connected_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    fb_user: Optional[dict] = None
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    request_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    last_error: Optional[str] = None
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, method: str, params: dict, timeout: float = 180.0) -> dict:
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.request_count += 1
        try:
            await self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self.failed_count += 1
            self.last_error = "timeout"
            return {"error": "timeout"}
        except Exception as exc:  # noqa: BLE001
            self.failed_count += 1
            self.last_error = str(exc)
            return {"error": str(exc)}
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, payload: dict) -> bool:
        payload = normalize_browser_text(payload)
        future = self.pending.get(str(payload.get("id") or ""))
        if future is None or future.done():
            return False
        failed = bool(payload.get("error")) or (
            isinstance(payload.get("status"), int) and payload["status"] >= 400
        )
        if failed:
            self.failed_count += 1
            self.last_error = str(payload.get("error") or f"API_{payload.get('status')}")[:300]
        else:
            self.success_count += 1
        future.set_result(payload)
        return True

    def close(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("extension_disconnected"))
        self.pending.clear()

    def public(self) -> dict:
        return {
            "id": self.extension_id,
            "connected": True,
            "connectedAt": int(self.connected_at),
            "lastActiveAt": int(self.last_active_at),
            "fbUser": self.fb_user,
            "busy": self.operation_lock.locked(),
            "pending": len(self.pending),
            "requestCount": self.request_count,
            "successCount": self.success_count,
            "failedCount": self.failed_count,
            "lastError": self.last_error,
        }


class ConnectionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, ExtensionSession] = {}

    def register(self, extension_id: str, ws: Any) -> ExtensionSession:
        existing = self._sessions.pop(extension_id, None)
        if existing:
            existing.close()
        session = ExtensionSession(extension_id=extension_id, ws=ws)
        self._sessions[extension_id] = session
        return session

    def unregister(self, extension_id: str, ws: Any) -> None:
        session = self._sessions.get(extension_id)
        if session is None or session.ws is not ws:
            return
        self._sessions.pop(extension_id, None)
        session.close()

    def get(self, extension_id: str) -> Optional[ExtensionSession]:
        return self._sessions.get(extension_id)

    def require(self, extension_id: str) -> ExtensionSession:
        session = self.get(extension_id)
        if session is None:
            raise KeyError(f"extension_not_connected: {extension_id}")
        return session

    def by_secret(self, secret: str) -> Optional[ExtensionSession]:
        return next((s for s in self._sessions.values() if secrets.compare_digest(s.callback_secret, secret)), None)

    def default(self) -> Optional[ExtensionSession]:
        """Compatibility selection for legacy APIs when exactly one is online."""
        if len(self._sessions) == 1:
            return next(iter(self._sessions.values()))
        return None

    def list(self) -> list[dict]:
        return [session.public() for session in self._sessions.values()]

    def handle_message(self, extension_id: str, data: dict) -> None:
        session = self.get(extension_id)
        if session is None:
            return
        message_type = data.get("type")
        if message_type in {"fb_ready", "last_active", "ping", "pong"}:
            session.last_active_at = time.time()
            return
        if message_type == "fb_user" and isinstance(data.get("fbUser"), dict):
            session.fb_user = normalize_browser_text(data["fbUser"])
            session.last_active_at = time.time()
            return
        if data.get("id"):
            session.resolve(data)


connection_registry = ConnectionRegistry()
