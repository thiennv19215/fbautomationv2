"""Persistent saved pages/channels store for FBEM."""
from __future__ import annotations

import json
import logging
from typing import Optional
from pathlib import Path

from .config import home_dir

logger = logging.getLogger(__name__)

_PAGES_PATH = home_dir() / "pages.json"


def _ensure_store() -> list[dict]:
    if not _PAGES_PATH.exists():
        _PAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PAGES_PATH.write_text("[]", encoding="utf-8")
        return []
    try:
        data = json.loads(_PAGES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("failed to load pages.json: %s", exc)
        return []


def _save_store(pages: list[dict]) -> None:
    try:
        _PAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PAGES_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pages, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_PAGES_PATH)
    except Exception as exc:
        logger.error("failed to save pages.json: %s", exc)


def list_pages() -> list[dict]:
    return _ensure_store()


def save_page(page_id: str, name: str, extension_id: Optional[str] = None, note: str = "") -> dict:
    pages = _ensure_store()
    page_id_str = str(page_id).strip()
    # Check if exists
    for p in pages:
        if p.get("id") == page_id_str:
            p["name"] = name.strip() or p["name"]
            if extension_id is not None:
                p["extensionId"] = extension_id.strip()
            p["note"] = note
            _save_store(pages)
            return p

    new_page = {
        "id": page_id_str,
        "name": name.strip() or f"Page {page_id_str}",
        "extensionId": (extension_id or "").strip(),
        "note": note,
    }
    pages.append(new_page)
    _save_store(pages)
    return new_page


def delete_page(page_id: str) -> bool:
    pages = _ensure_store()
    page_id_str = str(page_id).strip()
    new_pages = [p for p in pages if p.get("id") != page_id_str]
    _save_store(new_pages)
    return True
