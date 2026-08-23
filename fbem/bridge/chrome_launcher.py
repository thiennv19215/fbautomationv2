"""Chrome Profile Launcher — start Chrome profiles in silent background / minimized mode."""
from __future__ import annotations

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def find_chrome_executable() -> Optional[str]:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def get_chrome_profiles() -> list[str]:
    """Find available Chrome profile directory names."""
    user_data = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    if not user_data.exists():
        return ["Default"]
    
    profiles = []
    if (user_data / "Default").exists():
        profiles.append("Default")
    
    for p in user_data.iterdir():
        if p.is_dir() and p.name.startswith("Profile "):
            profiles.append(p.name)
            
    return profiles or ["Default"]


def is_profile_running(profile_dir: str) -> bool:
    """Check if Chrome is already running with the specified profile directory."""
    if os.name != "nt":
        return False
    try:
        cmd = f'powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"name=\'chrome.exe\'\\" | Select-Object -ExpandProperty CommandLine"'
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5, shell=True)
        if res.returncode == 0 and res.stdout:
            target_flag = f"--profile-directory={profile_dir}"
            return target_flag.lower() in res.stdout.lower()
    except Exception:
        pass
    return False


def launch_profile_background(profile_dir: str = "Default", url: str = "https://www.facebook.com/", is_secondary: bool = False) -> bool:
    """Launch a single Chrome profile in minimized background mode.
    
    Skips if profile is already running to avoid duplicate tabs/processes.
    """
    if is_profile_running(profile_dir):
        logger.info("chrome profile '%s' is already running, skipping duplicate launch", profile_dir)
        return True

    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        logger.warning("chrome.exe not found")
        return False
        
    cmd = [
        chrome_exe,
        f"--profile-directory={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--disk-cache-size=10485760",
        "--media-cache-size=10485760",
    ]
    if is_secondary:
        cmd.extend(["--window-position=-32000,-32000", "--window-size=600,600", "--mute-audio"])
    
    cmd.append(url)
    
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 6  # SW_MINIMIZE
        subprocess.Popen(cmd, startupinfo=startupinfo, close_fds=True)
        logger.info("launched chrome profile '%s' (secondary=%s) in background", profile_dir, is_secondary)
        return True
    except Exception as exc:
        logger.error("failed to launch chrome profile '%s': %s", profile_dir, exc)
        return False


def launch_all_profiles_background(url: str = "https://www.facebook.com/", skip_main: bool = False, max_profiles: int = 5) -> list[str]:
    """Launch detected Chrome profiles in background up to max_profiles.
    
    Skips any profile that is already running.
    """
    profiles = get_chrome_profiles()[:max_profiles]
    launched = []
    for i, p in enumerate(profiles):
        if skip_main and (p == "Default" or i == 0):
            continue
        is_sub = (p != "Default" and i > 0)
        if launch_profile_background(p, url, is_secondary=is_sub):
            launched.append(p)
    return launched

