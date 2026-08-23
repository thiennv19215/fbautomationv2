import os
import sys
import time
import threading
import subprocess
import urllib.request
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
    try:
        from fbem.bridge.server import app
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=HTTP_PORT,
            log_level="warning",
            loop="asyncio",
            http="h11",
            ws="websockets",
            lifespan="on",
        )
        server = uvicorn.Server(config)
        server.run()
    except Exception as exc:
        print(f"[!] Uvicorn Server error: {exc}")
        import traceback
        traceback.print_exc()


def wait_for_server_ready(url: str, timeout_seconds: float = 12.0) -> bool:
    """Poll the health check URL until the backend server is ready."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def free_busy_ports(ports: tuple[int, ...] = (47102, 9224)) -> None:
    """Find and terminate any existing processes occupying FBEM ports on Windows."""
    if sys.platform != "win32":
        return
    for port in ports:
        try:
            cmd = f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"'
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0, shell=True)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    pid = line.strip()
                    if pid and pid != "0" and pid != str(os.getpid()):
                        subprocess.run(f"taskkill /pid {pid} /F /T", capture_output=True, shell=True)
        except Exception:
            pass


def main() -> None:
    # 0. Free old hanging ports
    free_busy_ports((HTTP_PORT, 9224))

    # 1. Start Uvicorn server thread
    server_thread = threading.Thread(target=run_uvicorn_server, daemon=True)
    server_thread.start()

    dashboard_url = f"http://127.0.0.1:{HTTP_PORT}/"
    health_url = f"http://127.0.0.1:{HTTP_PORT}/api/health"

    print("[*] Waiting for FBEM backend server to start...")
    is_ready = wait_for_server_ready(health_url, timeout_seconds=15.0)
    if is_ready:
        print(f"[+] FBEM Server is ready at {dashboard_url}")
    else:
        print("[!] Warning: Server is taking longer to start, launching UI anyway...")

    # 3. Open native desktop window using pywebview (Edge WebView2)
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
            import webbrowser
            webbrowser.open(dashboard_url)
        except Exception:
            subprocess.Popen(f'start "" "{dashboard_url}"', shell=True)

        # Keep server running in console mode
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

