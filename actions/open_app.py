from core.intent_memory import remember_action
# actions/open_app.py
"""
High-performance Windows Application Launcher for JARVIS.
Features:
- URL passthrough
- Spotify priority detection (by window class, not title)
- Registry App Paths scanning
- UWP/WindowsApps execution
- Already‑running window detection & focus
"""

import os
import sys
import subprocess
import shutil
import webbrowser
import time
import platform
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    import winreg
    import win32gui
    import win32con

_SYSTEM = platform.system()


def get_executable_from_registry(app_name: str) -> Optional[str]:
    """Return absolute path from Windows Registry (App Paths)."""
    if sys.platform != "win32":
        return None

    aliases = {
        "photoshop": "Photoshop.exe",
        "illustrator": "Illustrator.exe",
        "premiere": "Premiere.exe",
        "brave": "brave.exe",
        "spotify": "Spotify.exe",
        "chrome": "chrome.exe",
        "edge": "msedge.exe",
        "firefox": "firefox.exe",
        "whatsapp": "WhatsApp.exe",
        "telegram": "Telegram.exe",
        "discord": "Discord.exe",
        "slack": "Slack.exe",
        "vscode": "Code.exe",
        "code": "Code.exe",
    }
    target_exe = aliases.get(app_name.lower(), f"{app_name}.exe")

    registry_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
        r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\App Paths",
    ]

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for reg_sub in registry_paths:
            try:
                key_path = f"{reg_sub}\\{target_exe}"
                key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ)
                exe_path, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if exe_path:
                    clean = exe_path.strip('"')
                    if os.path.exists(clean):
                        return clean
            except Exception:
                continue
    return None


def _resume_spotify(delay: float = 1.5) -> None:
    """Force Spotify to start playing after launch or focus."""
    time.sleep(delay)
    try:
        from actions.media_coordinator import media_coordinator
        media_coordinator({"target": "spotify", "action": "play"})
    except Exception as e:
        print(f"[open_app] Could not resume Spotify: {e}")


def open_app(parameters=None, response=None, player=None, session_memory=None) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()
    if not app_name:
        return "No application name provided."
    try:
        remember_action(tool="open_app", action="open_app", app=app_name)
    except Exception:
        pass

    # ----- 1. URL handling (open in default browser) -----
    if app_name.startswith(("http://", "https://")) or ".com" in app_name or ".org" in app_name:
        webbrowser.open(app_name)
        return f"Opening link in your default browser, sir."

    print(f"[open_app] Launch request: '{app_name}'")

    # ----- 2. SPOTIFY – PRIORITY (by window class, no title confusion) -----
    if "spotify" in app_name.lower():
        # 2a. Detect by unique class name (never matches Brave)
        spotify_hwnd = None
        def enum_spotify(hwnd, hwnds):
            if win32gui.GetClassName(hwnd) == "SpotifyMainWindow":
                hwnds.append(hwnd)
            return True
        hwnds = []
        win32gui.EnumWindows(enum_spotify, hwnds)
        if hwnds:
            spotify_hwnd = hwnds[0]

        if spotify_hwnd:
            win32gui.ShowWindow(spotify_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(spotify_hwnd)
            _resume_spotify(delay=0.8)
            return "Spotify brought to foreground and resumed, sir."

        # 2b. Not running → launch executable
        candidates = [
            Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "Spotify.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Spotify" / "Spotify.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Spotify" / "Spotify.exe",
            Path.home() / "AppData" / "Roaming" / "Spotify" / "Spotify.exe",
            Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "Spotify.exe",
        ]
        spotify_exe = None
        for p in candidates:
            if p.exists():
                spotify_exe = str(p)
                break

        if spotify_exe:
            subprocess.Popen(f'"{spotify_exe}"', shell=True)
            _resume_spotify(delay=1.5)
            return "Spotify opened and now playing, sir."
        else:
            os.system("start spotify:")
            _resume_spotify(delay=1.5)
            return "Spotify triggered via system protocol, sir."

    # ----- 3. Generic window detection (for all other apps) -----
    if sys.platform == "win32":
        try:
            from actions.window_control import find_window_by_master_spec, force_absolute_focus
            target = find_window_by_master_spec(app_name)
            if target:
                print(f"[open_app] '{app_name}' already running → focusing.")
                force_absolute_focus(target.getHandle())
                return f"{app_name} was already open, sir. I have brought it to the foreground."
        except Exception as e:
            print(f"[open_app] Window focus check failed: {e}")

    # ----- 4. Registry lookup (Photoshop, Illustrator, Brave, etc.) -----
    reg_path = get_executable_from_registry(app_name)
    if reg_path:
        if any(b in app_name.lower() for b in ("brave", "chrome", "edge", "firefox")):
            webbrowser.open("about:blank")
            return f"{app_name.capitalize()} context opened, sir."
        subprocess.Popen(f'"{reg_path}"', shell=True)
        return f"{app_name} launched from system registry, sir."

    # ----- 5. Standard PATH lookup -----
    binary = shutil.which(app_name) or shutil.which(f"{app_name}.exe")
    if binary:
        subprocess.Popen(f'"{binary}"', shell=True)
        return f"Opened {app_name}."

    # ----- 6. Nothing found -----
    return f"Sir, I could not locate '{app_name}' anywhere on your system."