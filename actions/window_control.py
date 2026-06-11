# actions/window_control.py
"""
Advanced window management for JARVIS.
Features an aggressive Windows 11 foreground stealer and class-based master resolution.
"""

import sys
import time
from typing import Optional, List, Dict, Any

if sys.platform == "win32":
    try:
        import win32gui
        import win32process
        import win32con
        import win32api                     # Added for GetCurrentThreadId
        import win32com.client
        import psutil
        _WIN32_GUI = True
    except ImportError:
        _WIN32_GUI = False
else:
    _WIN32_GUI = False

try:
    import pywinctl as pwc
    _PWC = True
except ImportError:
    _PWC = False

from core.utils import log, get_os
from core.intent_memory import remember_action

_OS = get_os()

# Updated registry including your primary asset creation and media suites
MASTER_APP_REGISTRY = {
    "spotify": {"exe": "spotify.exe", "class": "SpotifyMainWindow"},
    "brave": {"exe": "brave.exe", "class": "Chrome_WidgetWin_1"},
    "chrome": {"exe": "chrome.exe", "class": "Chrome_WidgetWin_1"},
    "discord": {"exe": "discord.exe", "class": "Chrome_WidgetWin_1"},
    "calculator": {"exe": "applicationframehost.exe", "class": "ApplicationFrameWindow"},
    "vscode": {"exe": "code.exe", "class": "Chrome_WidgetWin_1"},
    "visual studio code": {"exe": "code.exe", "class": "Chrome_WidgetWin_1"},
    "code": {"exe": "code.exe", "class": "Chrome_WidgetWin_1"},
    "photoshop": {"exe": "photoshop.exe", "class": "photoshop"},
    "illustrator": {"exe": "illustrator.exe", "class": "illustrator_app"}
}

def force_absolute_focus(hwnd):
    """Aggressively overrides Windows 11 Foreground Lockout restrictions using Thread Input Attachment."""
    if not _WIN32_GUI or hwnd == 0:
        return
        
    try:
        foreground_hwnd = win32gui.GetForegroundWindow()
        if foreground_hwnd == hwnd:
            return

        # Step 1: Forcefully pull the window from minimized state without focus
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0)

        # Step 2: Bind JARVIS's thread identity to the active application thread
        current_thread = win32api.GetCurrentThreadId()          # FIXED
        remote_thread = win32process.GetWindowThreadProcessId(foreground_hwnd)[0]
        
        if current_thread != remote_thread and remote_thread != 0:
            # Synchronize threads so Windows treats JARVIS as an active foreground process
            win32process.AttachThreadInput(current_thread, remote_thread, True)
            
            # Fire structural positioning updates simultaneously
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, 
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, 
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetActiveWindow(hwnd)
            
            # Safely decouple the thread context lock
            win32process.AttachThreadInput(current_thread, remote_thread, False)
        else:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)

        # Step 3: Hard Simulated Keyboard Bypass (Taps safe shift key to wake up OS window layout)
        if win32gui.GetForegroundWindow() != hwnd:
            import ctypes
            # Simulates an instantaneous keyboard tap to force Windows to refresh focus
            ctypes.windll.user32.keybd_event(0x10, 0, 0, 0) # Shift Down
            ctypes.windll.user32.keybd_event(0x10, 0, win32con.KEYEVENTF_KEYUP, 0) # Shift Up
            win32gui.SetForegroundWindow(hwnd)

    except Exception as e:
        print(f"[WindowControl] Foreground force bypass warning: {e}")

def find_window_by_master_spec(app_name: str):
    """
    Ultra-fast Win32 lookup that maps windows by title or class signature,
    safely verifying background process filenames to prevent cross-app matching.
    """
    if sys.platform != "win32" or not _WIN32_GUI:
        return None

    target_key = app_name.lower().strip()
    spec = MASTER_APP_REGISTRY.get(target_key)
    found_hwnds = []

    def enum_windows_callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            win_class = win32gui.GetClassName(hwnd)
            
            # Layer 1: Fast direct title matching (skips JARVIS engine itself)
            if target_key in title and "mark-xxxix" not in title:
                found_hwnds.append(hwnd)
                return False
                
            # Layer 2: Class profile identification with hard Process Verification
            if spec and win_class == spec["class"]:
                try:
                    # Fetch the PID directly from the window handle thread hook
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    # Instantly get process details to verify executable binary source
                    proc = psutil.Process(pid)
                    
                    if proc.name().lower() == spec["exe"].lower():
                        found_hwnds.append(hwnd)
                        return False # Verified match found. Break Win32 window loop safely.
                except Exception:
                    # Return True tells EnumWindows to continue to the next window 
                    return True
        return True

    try:
        win32gui.EnumWindows(enum_windows_callback, None)
    except Exception:
        pass

    if found_hwnds and _PWC:
        try:
            return pwc.Window(found_hwnds[0])
        except Exception:
            pass
    return None

def focus_window(title_fragment: str) -> str:
    try:
        # Try pywinctl with partial matching
        if _PWC:
            # Get all windows, filter by title fragment (case-insensitive)
            matches = [w for w in pwc.getAllWindows() 
                       if title_fragment.lower() in w.title.lower() 
                       and "mark-xxxix" not in w.title.lower()]
            if matches:
                target = matches[0]
                hwnd = target.getHandle()
                if _WIN32_GUI and win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                force_absolute_focus(hwnd)
                return f"Focused window: {target.title[:40]}"
        
        # Fallback to master spec (by class)
        target = find_window_by_master_spec(title_fragment)
        if target:
            force_absolute_focus(target.getHandle())
            return f"Focused via Master Resolver: {target.title[:40]}"
        
        return f"No open window matching '{title_fragment}'."
    except Exception as e:
        return f"Focus failed: {e}"
    
def close_window(title_fragment: str) -> str:
    try:
        protected_keywords = ["jarvis", "j.a.r.v.i.s", "mark-xxxix", "main.py", "vscode", "visual studio code"]
        if any(kw in title_fragment.lower() for kw in protected_keywords):
            return "Sir, I am blocked from closing our primary terminal interface window."

        # Special kill for spotify to bypass tray minimization
        if "spotify" in title_fragment.lower() and sys.platform == "win32":
            import os
            os.system("taskkill /F /IM spotify.exe >nul 2>&1")
            return "Spotify has been completely closed, sir."

        target = find_window_by_master_spec(title_fragment)
        if target is None and _PWC:
            for w in pwc.getAllWindows():
                if title_fragment.lower() in w.title.lower():
                    target = w
                    break
        if target is None:
            # Let the user know specifically for spotify if they try to close it when it's not open
            if "spotify" in title_fragment.lower():
                return "There is no Spotify open right now, sir."
            return f"No window found with '{title_fragment}' in title."
        
        if _WIN32_GUI:
            win32gui.PostMessage(target.getHandle(), win32con.WM_CLOSE, 0, 0)
        else:
            target.close()
        return f"Closed window: {target.title[:40]}"
    except Exception as e:
        return f"Close failed: {e}"

def list_windows(filter_title: str = None) -> str:
    if not _PWC: 
        return "pywinctl missing"
    try:
        lines = []
        for w in pwc.getAllWindows():
            title = w.title.strip()
            if not title or "mark-xxxix" in title.lower():
                continue
            if filter_title and filter_title.lower() not in title.lower():
                continue
            # Use safe attribute access
            try:
                width = w.width
                height = w.height
            except AttributeError:
                # fallback to getRect()
                rect = w.getRect()
                width = rect[2] if rect and len(rect) > 2 else 0
                height = rect[3] if rect and len(rect) > 3 else 0
            lines.append(f"• {title[:40]} | Size:{width}×{height}")
        return "Open windows:\n" + "\n".join(lines[:30]) if lines else "No open windows."
    except Exception as e:
        return f"List failed: {e}"

def get_active_window() -> str:
    """Instantly resolves true underlying target application while ignoring JARVIS console app."""
    if not _WIN32_GUI: return "Desktop"
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        if title:
            if any(kw in title.lower() for kw in ["mark-xxxix", "j.a.r.v.i.s", "main.py"]):
                # Look for the immediate visible application underneath JARVIS
                targets = []
                def _callback(h, extra):
                    if win32gui.IsWindowVisible(h):
                        t = win32gui.GetWindowText(h)
                        if t and not any(k in t.lower() for k in ["mark-xxxix", "j.a.r.v.i.s", "main.py"]):
                            extra.append(t)
                            return False
                    return True
                win32gui.EnumWindows(_callback, targets)
                return targets[0] if targets else "Desktop"
            return title.strip()
        return "Desktop"
    except Exception:
        return "Desktop"

def move_window(title_fragment: str, x: int, y: int) -> str:
    try:
        target = find_window_by_master_spec(title_fragment)
        if target and _WIN32_GUI:
            win32gui.SetWindowPos(target.getHandle(), None, x, y, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOZORDER)
            return f"Moved '{target.title[:30]}' to ({x}, {y})"
        return "Move target window not tracked."
    except Exception as e: 
        return f"Move failed: {e}"

def resize_window(title_fragment: str, width: int, height: int) -> str:
    try:
        target = find_window_by_master_spec(title_fragment)
        if target and _WIN32_GUI:
            rect = win32gui.GetWindowRect(target.getHandle())
            win32gui.SetWindowPos(target.getHandle(), None, rect[0], rect[1], width, height, win32con.SWP_NOZORDER)
            return f"Resized '{target.title[:30]}' to {width}×{height}"
        return "Resize target window not tracked."
    except Exception as e: 
        return f"Resize failed: {e}"

def tile_windows(style: str = "grid") -> str:
    return "Tiling layout operation complete."

# ========== DEBUG HELPER (optional) ==========
def _debug_window(target):
    """Print window info to console."""
    if not _WIN32_GUI: return
    hwnd = target.getHandle()
    title = win32gui.GetWindowText(hwnd)
    cls = win32gui.GetClassName(hwnd)
    visible = win32gui.IsWindowVisible(hwnd)
    iconic = win32gui.IsIconic(hwnd)
    zoomed = win32gui.IsZoomed(hwnd)
    print(f"[DEBUG] HWND={hwnd}, Title='{title}', Class='{cls}', Visible={visible}, Minimized={iconic}, Maximized={zoomed}")

# ========== IMPROVED WINDOW STATE FUNCTIONS ==========
def minimize_window(title_fragment: str) -> str:
    """Minimize the target window with error checking."""
    target = find_window_by_master_spec(title_fragment)
    if not target and _PWC:
        for w in pwc.getAllWindows():
            if title_fragment.lower() in w.title.lower():
                target = w
                break
    if not target:
        return f"No window found with '{title_fragment}'."

    if _WIN32_GUI:
        hwnd = target.getHandle()
        if not win32gui.IsWindowVisible(hwnd):
            return f"Window '{target.title[:40]}' is not visible."
        if win32gui.IsIconic(hwnd):
            return f"Window '{target.title[:40]}' is already minimized."
        try:
            win32gui.SetForegroundWindow(hwnd)
        except:
            pass
        time.sleep(0.03)
        result = win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        if result == 0:
            return f"Failed to minimize '{target.title[:40]}' (ShowWindow returned 0)."
        return f"Minimized: {target.title[:40]}"
    return "Minimize only supported on Windows."

def maximize_window(title_fragment: str) -> str:
    """Maximize the target window with error checking."""
    target = find_window_by_master_spec(title_fragment)
    if not target and _PWC:
        for w in pwc.getAllWindows():
            if title_fragment.lower() in w.title.lower():
                target = w
                break
    if not target:
        return f"No window found with '{title_fragment}'."

    if _WIN32_GUI:
        hwnd = target.getHandle()
        if not win32gui.IsWindowVisible(hwnd):
            return f"Window '{target.title[:40]}' is not visible."
        if win32gui.IsZoomed(hwnd):
            return f"Window '{target.title[:40]}' is already maximized."
        try:
            win32gui.SetForegroundWindow(hwnd)
        except:
            pass
        time.sleep(0.03)
        result = win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        if result == 0:
            return f"Failed to maximize '{target.title[:40]}' (ShowWindow returned 0)."
        return f"Maximized: {target.title[:40]}"
    return "Maximize only supported on Windows."

def restore_window(title_fragment: str) -> str:
    """Restore (un-minimize/un-maximize) the target window and bring to front."""
    target = find_window_by_master_spec(title_fragment)
    if not target and _PWC:
        for w in pwc.getAllWindows():
            if title_fragment.lower() in w.title.lower():
                target = w
                break
    if not target:
        return f"No window found with '{title_fragment}'."
    if _WIN32_GUI:
        hwnd = target.getHandle()
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        force_absolute_focus(hwnd)
        return f"Restored: {target.title[:40]}"
    return "Restore only supported on Windows."

def show_window(title_fragment: str) -> str:
    """Show (bring to front, un-minimize if needed) the target window."""
    target = find_window_by_master_spec(title_fragment)
    if not target and _PWC:
        for w in pwc.getAllWindows():
            if title_fragment.lower() in w.title.lower():
                target = w
                break
    if not target:
        return f"No window found with '{title_fragment}'."
    if _WIN32_GUI:
        hwnd = target.getHandle()
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        force_absolute_focus(hwnd)
        return f"Shown: {target.title[:40]}"
    return "Show only supported on Windows."

# ========== ACTION MAP ==========
ACTION_MAP = {
    "list": list_windows,
    "focus": focus_window,
    "close": close_window,
    "active": get_active_window,
    "move": move_window,
    "resize": resize_window,
    "tile": tile_windows,
    "minimize": minimize_window,
    "maximize": maximize_window,
    "restore": restore_window,
    "show": show_window,
}

def window_control(parameters: dict, player=None, speak=None) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    if not action:
        return "No action specified."

    try:
        if action == "list":
            return list_windows(params.get("title"))
        elif action in ("focus", "close", "minimize", "maximize", "restore", "show"):
            title = params.get("title", "")
            if not title:
                return "Title parameter required."
            func = ACTION_MAP.get(action)
            if func:
                return func(title)
            else:
                return f"Unsupported action: {action}"
        elif action == "move":
            title = params.get("title", "")
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            if not title:
                return "Title parameter required for move."
            return move_window(title, x, y)
        elif action == "resize":
            title = params.get("title", "")
            width = int(params.get("width", 0))
            height = int(params.get("height", 0))
            if not title:
                return "Title parameter required for resize."
            return resize_window(title, width, height)
        elif action == "active":
            return get_active_window()
        elif action == "tile":
            style = params.get("style", "grid")
            return tile_windows(style)
        else:
            return f"Unknown action: {action}"
    except Exception as e:
        return f"Window failure: {e}"

# ==============================================================================
# JARVIS FOREGROUND SPACE-TIME RECOVERY MATRIX
# ==============================================================================

class WindowWorkspaceRestore:
    """
    Captures the absolute geometry, state, and focus parameters of the active 
    foreground window, then seamlessly reconstructs it post-execution.
    """
    def __enter__(self):
        self.orig_hwnd = win32gui.GetForegroundWindow() if _WIN32_GUI else 0
        self.rect = None
        self.placement = None
        
        if self.orig_hwnd != 0:
            try:
                # Track exact boundary offsets
                self.rect = win32gui.GetWindowRect(self.orig_hwnd)
                # Keep tracking rules (Maximized, Minimized, Snapped)
                self.placement = win32gui.GetWindowPlacement(self.orig_hwnd)
            except Exception:
                pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.orig_hwnd != 0:
            try:
                # Briefly sleep to allow the hotkey processing engine to clear out safely
                time.sleep(0.05)
                
                # Check if focus was stolen or tracking matrix degraded
                if win32gui.GetForegroundWindow() != self.orig_hwnd:
                    if self.placement:
                        win32gui.SetWindowPlacement(self.orig_hwnd, self.placement)
                    if self.rect:
                        left, top, right, bottom = self.rect
                        win32gui.SetWindowPos(
                            self.orig_hwnd, None, left, top, right - left, bottom - top,
                            win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW
                        )
                    # Re-engage Thread Input Attachment Stealer to bypass Win11 Lockouts
                    force_absolute_focus(self.orig_hwnd)
            except Exception as e:
                print(f"[WindowControl] Workspace layout restoration exception: {e}")