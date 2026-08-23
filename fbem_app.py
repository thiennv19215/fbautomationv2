"""Desktop Application Entrypoint for FBEM Studio (Windows Native Window & System Fallback)."""
from __future__ import annotations

import os
import sys
import time
import threading
import subprocess
from pathlib import Path

# Ensure package root is in sys.path when running frozen or direct
if getattr(sys, "frozen", False):
    bundle_dir = Path(sys._MEIPASS)
    sys.path.insert(0, str(bundle_dir))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fbem.bridge.config import HTTP_PORT


def run_uvicorn_server() -> None:
    """Run the FastAPI backend server in a dedicated thread."""
    from fbem.bridge.server import app
    config = uvicorn.Config(app, host="127.0.0.1", port=HTTP_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main() -> None:
    # 1. Launch background Chrome profiles if any
    try:
        from fbem.bridge.chrome_launcher import launch_all_profiles_background
        launch_all_profiles_background()
    except Exception as e:
        print(f"[!] Chrome profile launcher note: {e}")

    # 2. Start Uvicorn server thread
    server_thread = threading.Thread(target=run_uvicorn_server, daemon=True)
    server_thread.start()

    # 3. Give server a moment to bind
    time.sleep(1.2)

    dashboard_url = f"http://127.0.0.1:{HTTP_PORT}/"

    # 4. Open native desktop window using pywebview (Edge WebView2)
    #    If not supported or fails, fallback to opening system default browser.
    try:
        import webview
        window = webview.create_window(
            title="⚡ FBEM Studio — Facebook Automation Dashboard",
            url=dashboard_url,
            width=1360,
            height=880,
            min_size=(1020, 680),
        )
        webview.start()
    except Exception as exc:
        print(f"[!] PyWebView window unavailable ({exc}), opening default browser...")
        try:
            subprocess.Popen(f'start "" "{dashboard_url}"', shell=True)
        except Exception:
            import webbrowser
            webbrowser.open(dashboard_url)

        # Keep server running in console mode
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
