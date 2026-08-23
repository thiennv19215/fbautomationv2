"""Bridge to the Chrome MV3 extension over WebSocket — Facebook Reel upload.

Generic transport ported + trimmed from flowgen's flow_client.py. KEEPS ONLY the
proven mechanism (pending asyncio futures keyed by uuid, callback secret, HTTP
callback resolution, telemetry, fire-and-forget notify) and drops all
Google-Flow-specific code (paygate, flow_key/ya29 caching, userinfo fetch, trpc,
captcha).

Control flow:
1. Extension opens WS to :9224.
2. Server sends ``{type:"callback_secret", secret}`` immediately.
3. When the server wants the extension to perform an action in the
   facebook.com page context, it calls e.g. ``bridge_client.post_reel(...)``
   which sends ``{id, method, params}`` over WS and awaits a future.
4. The extension performs the work inside the user's browser session and POSTs
   the response to ``/api/ext/callback`` with ``X-Callback-Secret``.
5. That HTTP handler resolves the pending future by id.
6. WS-side inbound messages from the extension (``fb_ready``,
   ``token_captured``, ``ping``/``pong``, ``fb_user``) update our stats.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ExtensionSession:
    """Represents an active connection from a single Chrome Profile extension."""

    def __init__(self, ws: Any, extension_id: str) -> None:
        self.ws = ws
        self.extension_id = extension_id
        self.fb_user: Optional[dict] = None
        self.last_active_at: float = time.time()
        self.connected_at: float = time.time()
        self.request_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.last_error: Optional[str] = None
        self.pending: dict[str, asyncio.Future] = {}

    def to_dict(self) -> dict:
        return {
            "id": self.extension_id,
            "connected": True,
            "connectedAt": int(self.connected_at),
            "lastActiveAt": int(self.last_active_at),
            "fbUser": self.fb_user,
            "pending": len(self.pending),
            "requestCount": self.request_count,
            "successCount": self.success_count,
            "failedCount": self.failed_count,
            "lastError": self.last_error,
        }


class BridgeClient:
    """Multi-session bridge client managing WebSocket connections to Chrome extensions."""

    DEFAULT_TIMEOUT = 180.0  # seconds

    def __init__(self) -> None:
        self._sessions: dict[str, ExtensionSession] = {}
        self._ws_to_id: dict[Any, str] = {}
        sec_file = Path.home() / ".fbem" / "bridge_secret.txt"
        if sec_file.exists():
            self._callback_secret = sec_file.read_text(encoding="utf-8").strip()
        else:
            self._callback_secret = secrets.token_urlsafe(32)
            try:
                sec_file.parent.mkdir(parents=True, exist_ok=True)
                sec_file.write_text(self._callback_secret, encoding="utf-8")
            except Exception:
                pass

    # ── connection management ──────────────────────────────────────────────
    @property
    def callback_secret(self) -> str:
        return self._callback_secret

    def _prune_dead_sessions(self) -> None:
        now = time.time()
        dead_ws = []
        for ws, ext_id in list(self._ws_to_id.items()):
            is_closed = getattr(ws, "closed", False)
            if is_closed:
                dead_ws.append(ws)
            elif ext_id in self._sessions:
                s = self._sessions[ext_id]
                # If inactive for too long and connection not responding
                if now - s.last_active_at > 180.0 and is_closed:
                    dead_ws.append(ws)
        for ws in dead_ws:
            self.unregister_extension(ws)

    @property
    def connected(self) -> bool:
        self._prune_dead_sessions()
        return bool(self._sessions)

    @property
    def extension_count(self) -> int:
        self._prune_dead_sessions()
        return len(self._sessions)

    def list_extensions(self) -> list[dict]:
        self._prune_dead_sessions()
        return [s.to_dict() for s in self._sessions.values()]

    def get_session(self, extension_id: Optional[str] = None) -> Optional[ExtensionSession]:
        self._prune_dead_sessions()
        if extension_id and extension_id in self._sessions:
            return self._sessions[extension_id]
        if not extension_id and self._sessions:
            # Default to the most recently active session
            return max(self._sessions.values(), key=lambda s: s.last_active_at)
        return None

    def register_extension(self, ws: Any, extension_id: Optional[str] = None) -> ExtensionSession:
        self._prune_dead_sessions()
        ext_id = extension_id or str(uuid.uuid4())
        # If ws was previously associated with another ID, unregister it
        if ws in self._ws_to_id and self._ws_to_id[ws] != ext_id:
            self.unregister_extension(ws)

        if ext_id in self._sessions:
            session = self._sessions[ext_id]
            session.ws = ws
            session.last_active_at = time.time()
        else:
            session = ExtensionSession(ws, ext_id)
            self._sessions[ext_id] = session

        self._ws_to_id[ws] = ext_id
        return session

    def unregister_extension(self, ws: Any) -> None:
        ext_id = self._ws_to_id.pop(ws, None)
        if ext_id and ext_id in self._sessions:
            session = self._sessions.pop(ext_id)
            for fut in session.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("extension_disconnected"))
            session.pending.clear()

    # ── compatibility accessors (default session) ──────────────────────────
    @property
    def fb_user(self) -> Optional[dict]:
        s = self.get_session()
        return s.fb_user if s else None

    @property
    def last_active_at(self) -> Optional[float]:
        s = self.get_session()
        return s.last_active_at if s else None

    # ── inbound handling ───────────────────────────────────────────────────
    async def handle_message(self, ws: Any, data: dict, ext_id: Optional[str] = None) -> ExtensionSession:
        effective_id = ext_id or data.get("extensionId") or self._ws_to_id.get(ws)
        session = self.register_extension(ws, effective_id)
        session.last_active_at = time.time()

        t = data.get("type")
        if t == "fb_ready":
            if data.get("fbUser"):
                session.fb_user = data["fbUser"]
            logger.info("fb_ready (extension %s connected, user: %s)", session.extension_id[:8], session.fb_user)
            return session
        if t == "last_active":
            return session
        if t == "fb_user":
            info = data.get("fbUser")
            if isinstance(info, dict):
                session.fb_user = info
                logger.info("fb_user captured for ext %s: %s", session.extension_id[:8], info.get("name") or info.get("id"))
            return session
        if t in ("ping", "pong"):
            return session

        # Inbound response over WS fallback
        req_id = data.get("id")
        if req_id:
            if req_id in session.pending:
                self._resolve(session, req_id, data)
            else:
                for s in self._sessions.values():
                    if req_id in s.pending:
                        self._resolve(s, req_id, data)
                        break
        return session

    def resolve_callback(self, data: dict) -> bool:
        """Called by the HTTP callback endpoint after validating the secret."""
        req_id = data.get("id")
        if not req_id:
            return False

        ext_id = data.get("extensionId")
        if ext_id and ext_id in self._sessions and req_id in self._sessions[ext_id].pending:
            self._resolve(self._sessions[ext_id], req_id, data)
            return True

        # Fallback: search across all active sessions
        for session in self._sessions.values():
            if req_id in session.pending:
                self._resolve(session, req_id, data)
                return True
        return False

    def _resolve(self, session: ExtensionSession, req_id: str, data: dict) -> None:
        fut = session.pending.pop(req_id, None)
        if not fut or fut.done():
            return
        status = data.get("status")
        http_error = isinstance(status, int) and status >= 400
        explicit_error = bool(data.get("error"))
        if http_error or explicit_error:
            session.failed_count += 1
            msg = data.get("error") or f"API_{status}"
            session.last_error = str(msg)[:200]
            fut.set_result(data)
        else:
            session.success_count += 1
            fut.set_result(data)

    # ── outbound ──────────────────────────────────────────────────────────
    async def notify(self, message: dict, extension_id: Optional[str] = None) -> bool:
        session = self.get_session(extension_id)
        if not session or not session.ws:
            return False
        try:
            await session.ws.send(json.dumps(message))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify failed on ext %s: %s", session.extension_id[:8], exc)
            return False

    async def _send(
        self, method: str, params: dict, extension_id: Optional[str] = None, timeout: Optional[float] = None
    ) -> dict:
        session = self.get_session(extension_id)
        if not session or not session.ws:
            return {"error": "extension_disconnected"}

        req_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        session.pending[req_id] = fut
        session.request_count += 1

        payload = {"id": req_id, "method": method, "params": params}
        try:
            await session.ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=timeout or self.DEFAULT_TIMEOUT)
        except asyncio.TimeoutError:
            session.pending.pop(req_id, None)
            session.failed_count += 1
            session.last_error = "timeout"
            return {"error": "timeout"}
        except ConnectionError as exc:
            session.pending.pop(req_id, None)
            session.failed_count += 1
            session.last_error = str(exc)
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            session.pending.pop(req_id, None)
            session.failed_count += 1
            session.last_error = str(exc)
            return {"error": str(exc)}

    async def post_reel(
        self,
        video_url: str,
        caption: str,
        page_id: Optional[str],
        template: dict,
        scheduled_publish_time: Optional[int] = None,
        switch_template: Optional[dict] = None,
        extension_id: Optional[str] = None,
        timeout: float = 300.0,
    ) -> dict:
        return await self._send(
            "post_reel",
            {
                "videoUrl": video_url,
                "caption": caption,
                "pageId": page_id,
                "switchTemplate": switch_template,
                "template": template,
                "scheduledPublishTime": scheduled_publish_time,
            },
            extension_id=extension_id,
            timeout=timeout,
        )

    async def post_photos(
        self,
        image_urls: list[str],
        caption: str,
        page_id: Optional[str],
        template: dict,
        scheduled_publish_time: Optional[int] = None,
        switch_template: Optional[dict] = None,
        extension_id: Optional[str] = None,
        timeout: float = 300.0,
    ) -> dict:
        return await self._send(
            "post_photos",
            {
                "imageUrls": image_urls,
                "caption": caption,
                "pageId": page_id,
                "switchTemplate": switch_template,
                "template": template,
                "scheduledPublishTime": scheduled_publish_time,
            },
            extension_id=extension_id,
            timeout=timeout,
        )

    async def switch_profile(
        self,
        target_id: str,
        switch_template: Optional[dict] = None,
        extension_id: Optional[str] = None,
        timeout: float = 60.0,
    ) -> dict:
        return await self._send(
            "switch_profile",
            {"targetId": target_id, "template": switch_template},
            extension_id=extension_id,
            timeout=timeout,
        )

    async def get_identity(self, extension_id: Optional[str] = None, timeout: float = 15.0) -> dict:
        return await self._send("get_identity", {}, extension_id=extension_id, timeout=timeout)

    # ── observability ─────────────────────────────────────────────────────
    @property
    def ws_stats(self) -> dict:
        total_pending = sum(len(s.pending) for s in self._sessions.values())
        total_req = sum(s.request_count for s in self._sessions.values())
        total_succ = sum(s.success_count for s in self._sessions.values())
        total_fail = sum(s.failed_count for s in self._sessions.values())
        return {
            "connected": self.connected,
            "extension_count": len(self._sessions),
            "pending": total_pending,
            "request_count": total_req,
            "success_count": total_succ,
            "failed_count": total_fail,
            "extensions": self.list_extensions(),
        }


bridge_client = BridgeClient()


def get_bridge_client() -> BridgeClient:
    return bridge_client
