"""Small helpers for browser strings that were decoded as latin-1 mojibake."""
from __future__ import annotations

from typing import Any


def normalize_browser_text(value: Any) -> Any:
    if isinstance(value, str) and any(marker in value for marker in ("Ã", "Â", "á»", "áº")):
        try:
            return value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
    if isinstance(value, dict):
        return {key: normalize_browser_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_browser_text(item) for item in value]
    return value
