"""Persist crawler captures of genuine native Facebook upload requests.

The crawler (injected main-world script on facebook.com) snapshots real upload
requests when the user manually posts a Reel, and relays each through the
extension to ``POST /api/ext/capture``. Each capture is a dict shaped like:

    { kind: "rupload" | "graphql", url, method, headers, body, friendlyName? }

We append every capture to ``captures/<unix-ts>-<kind>.json`` (full audit trail)
and fold the latest of each kind into ``captures/template.json``:

    { "rupload": {...}, "graphql": {...}, "updatedAt": <unix-ts> }

The replay (``/post-reel``) loads ``template.json`` and substitutes fresh
volatile tokens. When FB rotates its payload shape, the user re-captures; no
code change needed (self-healing by design).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional
from pathlib import Path
from urllib.parse import parse_qs

from .config import captures_dir

logger = logging.getLogger(__name__)

_CAPTURES_DIR = captures_dir()
_TEMPLATE_PATH = _CAPTURES_DIR / "template.json"

# Live capture activity — proof the extension is attached to a logged-in
# facebook.com tab and actively observing it (updates on every captured request,
# including the trace stream that flows as soon as the FB tab (re)loads). This is
# the honest "extension is ready" signal; there is no separate token flag.
_last_capture_at: Optional[float] = None
_capture_count: int = 0
_last_capture_url: Optional[str] = None

# Which graphql op is the actual Reel publish. During reel creation FB fires
# dozens of graphql ops (typeaheads, queries); only ONE publishes the post, so
# "latest wins" would store the wrong one. Match the publish mutation by its
# friendly name. Override via FBEM_PUBLISH_OP_RE if FB renames it.
_PUBLISH_OP_RE = re.compile(
    os.getenv(
        "FBEM_PUBLISH_OP_RE",
        r"(Composer.*Create.*Mutation|Story.*Create.*Mutation|Reels?.*(Create|Publish).*Mutation)",
    ),
    re.IGNORECASE,
)


def _ensure_dir() -> None:
    _CAPTURES_DIR.mkdir(parents=True, exist_ok=True)


def _publish_attachment_kind(payload: dict) -> Optional[str]:
    """Inspect a captured ComposerStoryCreateMutation and classify its first
    attachment, so reel / photo / link publishes — which share the SAME friendly
    name — land in the right template slot instead of clobbering each other.

    Returns one of:
      ``"video"``  — a reel (video attachment)
      ``"photo"``  — a single photo / album
      ``"other"``  — parsed fine, but not media we template (link share, empty,
                     text-only). The caller must NOT write the reel/photo slots.
      ``None``     — the body couldn't be parsed (e.g. FB rotated the shape). The
                     caller falls back to the reel slot so re-capture still heals.
    """
    try:
        body = payload.get("body") or {}
        raw = body.get("value") if isinstance(body, dict) else None
        if not raw:
            return None
        qs = parse_qs(raw)
        variables = qs.get("variables", [None])[0]
        if not variables:
            return None
        atts = (json.loads(variables).get("input") or {}).get("attachments") or []
        if not atts:
            return "other"
        first = atts[0]
        if "video" in first:
            return "video"
        if "photo" in first:
            return "photo"
        return "other"
    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
        return None


def _get_target_dir(extension_id: str | None = None) -> Path:
    if extension_id and extension_id.strip():
        # sanitize extension_id
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", extension_id.strip())
        if safe_id:
            d = _CAPTURES_DIR / safe_id
            d.mkdir(parents=True, exist_ok=True)
            return d
    _ensure_dir()
    return _CAPTURES_DIR


def save_capture(payload: dict, extension_id: str | None = None) -> None:
    """Append a recorded native request to the captures dir and fold its kind
    into template.json. ``payload['kind']`` selects the template slot
    (defaults to "unknown" so nothing is silently dropped)."""
    ext_id = extension_id or payload.get("extensionId")
    target_dir = _get_target_dir(ext_id)
    template_path = target_dir / "template.json"

    kind = payload.get("kind") or "unknown"
    ts = int(time.time())

    # Record live activity for the readiness signal (every capture counts).
    global _last_capture_at, _capture_count, _last_capture_url
    _last_capture_at = time.time()
    _capture_count += 1
    if payload.get("url"):
        _last_capture_url = str(payload["url"])[:200]

    # Comprehensive trace: one request+response per line, for OFFLINE analysis.
    # Kept out of the per-file/template machinery so a full session is one stream.
    if kind == "trace":
        rec = dict(payload)
        rec["ts"] = ts
        with (target_dir / "trace.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("trace %s %s -> %s", payload.get("method"), payload.get("respStatus"), (payload.get("url") or "")[:70])
        return

    capture_path = target_dir / f"{ts}-{kind}.json"
    suffix = 0
    while capture_path.exists():
        suffix += 1
        capture_path = target_dir / f"{ts}-{kind}-{suffix}.json"
    capture_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("saved capture %s in %s", capture_path.name, target_dir.name)

    template = load_template(ext_id) or {}
    template["updatedAt"] = ts

    if kind == "graphql":
        friendly = payload.get("friendlyName") or "(unknown)"
        ops = template.setdefault("graphql_ops", {})
        ops[friendly] = payload
        if _PUBLISH_OP_RE.search(friendly):
            att_kind = _publish_attachment_kind(payload)
            if att_kind == "photo":
                template["graphql_photo"] = payload
                logger.info("folded PHOTO publish op=%s into graphql_photo", friendly)
            elif att_kind == "other":
                logger.info(
                    "ignored non-media CSCM publish op=%s — leaving reel/photo slots intact",
                    friendly,
                )
            else:
                template["graphql"] = payload
                logger.info("folded REEL publish op=%s into graphql (att=%s)", friendly, att_kind)
        else:
            logger.info("recorded graphql op=%s (not the publish mutation)", friendly)
    elif kind == "photo_upload":
        rb = payload.get("reqBody") or payload.get("body") or {}
        fields = {}
        if isinstance(rb, dict) and rb.get("type") == "formdata":
            for k, v in (rb.get("entries") or {}).items():
                fields[k] = {"__binary": True} if isinstance(v, dict) and v.get("__binary") else v
        template["photo_upload"] = {
            "url": payload.get("url"),
            "method": payload.get("method") or "POST",
            "formFields": fields,
        }
        logger.info("folded photo_upload template into template.json")
    elif kind == "upload_flow":
        flows = template.setdefault("upload_flow", [])
        flows.append(payload)
        del flows[:-12]  # keep the last 12
        logger.info("recorded upload_flow %s -> %s", payload.get("method"), (payload.get("url") or "")[:60])
    elif kind == "rupload":
        url = payload.get("url") or ""
        if re.search(r"/react_composer/attachments/photo/", url, re.IGNORECASE):
            logger.info("ignored photo-composer rupload — leaving reel rupload slot intact")
        elif (payload.get("method") or "").upper() in ("POST", "PUT"):
            template["rupload"] = payload
            logger.info("folded real rupload POST into template.json")
        else:
            logger.info("ignored non-POST rupload (%s)", payload.get("method"))
    else:
        template[kind] = payload
        logger.info("folded kind=%s into template.json", kind)

    # Atomic write
    tmp = template_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, template_path)

    # If this template has a complete mutation, also mirror it to root template.json
    if template.get("graphql") or template.get("graphql_photo") or template.get("rupload"):
        try:
            _ensure_dir()
            root_tpl = load_template() or {}
            # Merge fields into root template
            for k, v in template.items():
                if k == "graphql_ops" and isinstance(v, dict):
                    root_tpl.setdefault("graphql_ops", {}).update(v)
                elif v:
                    root_tpl[k] = v
            root_tpl["updatedAt"] = int(time.time())
            root_tmp = _TEMPLATE_PATH.with_suffix(".json.tmp")
            root_tmp.write_text(json.dumps(root_tpl, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(root_tmp, _TEMPLATE_PATH)
        except Exception as exc:
            logger.debug("failed to mirror template to root: %s", exc)


def template_complete(t: Optional[dict]) -> bool:
    """Usable for REEL replay once we have the (video) publish mutation."""
    return bool(t) and bool(t.get("graphql"))


def photo_template_complete(t: Optional[dict]) -> bool:
    """Usable for PHOTO/ALBUM replay once we have both photo-upload and photo publish mutation."""
    return bool(t) and bool(t.get("graphql_photo"))


def capture_stats(extension_id: str | None = None) -> dict:
    """Live capture activity."""
    window = float(os.getenv("FBEM_TAB_ACTIVE_WINDOW_S", "90"))
    seconds_since = (
        int(time.time() - _last_capture_at) if _last_capture_at is not None else None
    )
    return {
        "captures": _capture_count,
        "last_capture_at": int(_last_capture_at) if _last_capture_at is not None else None,
        "seconds_since_capture": seconds_since,
        "last_capture_url": _last_capture_url,
        "tab_active": seconds_since is not None and seconds_since <= window,
    }


def load_template(extension_id: str | None = None) -> Optional[dict]:
    """Return the current template.json contents, checking scoped dir then falling back to root or other complete profiles."""
    paths_to_check: list[Path] = []
    if extension_id and extension_id.strip():
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", extension_id.strip())
        if safe_id:
            paths_to_check.append(_CAPTURES_DIR / safe_id / "template.json")
    paths_to_check.append(_TEMPLATE_PATH)

    # First pass: check direct paths
    for p in paths_to_check:
        if p.exists():
            try:
                data: Any = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and template_complete(data):
                    return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("failed to read %s: %s", p, exc)

    # Second pass: check any subdirectory that has a complete template
    if _CAPTURES_DIR.exists():
        sub_templates = sorted(_CAPTURES_DIR.glob("*/template.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        for p in sub_templates:
            try:
                data: Any = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and (template_complete(data) or bool(data.get("graphql_ops"))):
                    return data
            except (json.JSONDecodeError, OSError):
                continue

    # Fallback to whatever exists in root even if incomplete
    if _TEMPLATE_PATH.exists():
        try:
            data = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None
