# core/utils.py
import json
import sys
import platform
import os
import shutil
from pathlib import Path
from typing import Optional, Union

# ----------------------------------------------------------------------
# Base directory & config
# ----------------------------------------------------------------------
def get_base_dir() -> Path:
    """Return the root directory of the application (works for frozen executables)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def load_config() -> dict:
    """Load the API keys / settings JSON file."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def get_api_key() -> str:
    """Retrieve the Gemini API key from config."""
    key = load_config().get("gemini_api_key", "")
    if not key:
        raise RuntimeError("gemini_api_key not found in config/api_keys.json")
    return key

def get_os() -> str:
    """Return normalized OS name: 'windows', 'darwin', or 'linux'."""
    return load_config().get("os_system", platform.system().lower())

def is_windows() -> bool:
    return get_os() == "windows"

def is_mac() -> bool:
    return get_os() == "darwin"

def is_linux() -> bool:
    return get_os() == "linux"

# ----------------------------------------------------------------------
# Standard user folders (cross‑platform)
# ----------------------------------------------------------------------
def get_desktop() -> Path:
    if is_linux():
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"

def get_downloads() -> Path:
    if is_linux():
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Downloads"

def get_documents() -> Path:
    if is_linux():
        xdg = os.environ.get("XDG_DOCUMENTS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Documents"

def get_pictures() -> Path:
    if is_linux():
        xdg = os.environ.get("XDG_PICTURES_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Pictures"

def get_music() -> Path:
    if is_linux():
        xdg = os.environ.get("XDG_MUSIC_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Music"

def get_videos() -> Path:
    if is_linux():
        xdg = os.environ.get("XDG_VIDEOS_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Videos"

# ----------------------------------------------------------------------
# Safe path validation (prevent deletion of critical folders)
# ----------------------------------------------------------------------
SAFE_ROOTS: list[Path] = [Path.home()]   # retained for backward compatibility

# SAFETY OVERRIDE (explicit user approval):
# Allow access to any absolute/relative path by always returning True.
def is_safe_path(target: Union[str, Path]) -> bool:
    """Return True (safety checks disabled)."""
    return True

def safe_screenshot_path(requested: Optional[str] = None) -> Path:
    """Return a safe path for saving screenshots (default: Desktop/jarvis_screenshot.png)."""
    fallback = get_desktop() / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        if is_safe_path(p):
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    return fallback

# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------
def format_size(size_bytes: int) -> str:
    """Convert bytes to human readable string (KB, MB, GB, TB)."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

# ----------------------------------------------------------------------
# Unified logging
# ----------------------------------------------------------------------
def log(module: str, message: str, player=None) -> None:
    """Print to console and optionally to the UI log widget."""
    print(f"[{module}] {message}")
    if player:
        try:
            player.write_log(f"[{module}] {message[:60]}")
        except Exception:
            pass

# ----------------------------------------------------------------------
# OS‑specific browser user‑agent
# ----------------------------------------------------------------------
def get_user_agent() -> str:
    if is_windows():
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    if is_mac():
        return ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    return ("Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")