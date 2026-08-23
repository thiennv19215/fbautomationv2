"""Desktop Application Entrypoint for FBEM Studio (Windows .exe Packaging)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
import webbrowser
import threading
from pathlib import Path

# Ensure package root is in sys.path when running frozen or direct
if getattr(sys, "frozen", False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fbem.bridge.config import HTTP_PORT
from fbem.bridge.chrome_launcher import launch_all_profiles


def _open_dashboard_delayed() -> None:
    """Open Dashboard in default browser once the server is listening."""
    time.sleep(1.8)
    try:
        webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}/")
    except Exception:
        pass


def main() -> None:
    print("=" * 60)
    print("      ⚡ FBEM STUDIO — FACEBOOK AUTOMATION BRIDGE")
    print("=" * 60)
    print()
    print(f"[*] Starting local server on http://127.0.0.1:{HTTP_PORT}/")
    print("[*] Telegram Bot and Queue Dispatcher initialized.")
    print("[*] Chrome Extension WebSocket on ws://127.0.0.1:9224")
    print()

    # Launch Chrome profiles in background if Chrome is installed
    try:
        profs = launch_all_profiles()
        if profs:
            print(f"[+] Launched {len(profs)} background Chrome profile(s).")
    except Exception as err:
        print(f"[!] Note: Chrome auto-launch skipped ({err})")

    # Start browser opener thread
    threading.Thread(target=_open_dashboard_delayed, daemon=True).start()

    print("[+] Opening Dashboard at http://127.0.0.1:47102/ ...")
    print("=" * 60)
    print("Running... (Close this window or press CTRL+C to stop)")
    print("=" * 60)
    print()

    # Run Uvicorn directly with app object to prevent multiprocessing reload issues in frozen .exe
    from fbem.bridge.server import app
    uvicorn.run(app, host="127.0.0.1", port=HTTP_PORT, log_level="info")


if __name__ == "__main__":
    main()
