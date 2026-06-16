# actions/spotify_control.py
from __future__ import annotations

import os
import time
import urllib.parse
from core.intent_memory import remember_action

try:
    import pyautogui
    import keyboard
    import win32api
    import win32con
    import win32gui
    import win32process
    import ctypes
except ImportError:
    os.system("pip install pyautogui keyboard pywin32")
    import pyautogui
    import keyboard
    import win32api
    import win32con
    import win32gui
    import win32process
    import ctypes

# ─────────────────────────────────────────────────────────────────────────────
# CORE SPOTIFY INTERFACE HARDWARE BYPASS (CRITICAL FIX LAYER)
# ─────────────────────────────────────────────────────────────────────────────

def _enforce_spotify_priority():
    """Aggressively pauses all background media in Brave (Netflix/YouTube) natively via CDP layer."""
    try:
        from actions.browser_control import get_browser_session
        # connect_only=True prevents spawning a new Brave session/window.
        sess = get_browser_session("brave", connect_only=True)

        script = "document.querySelectorAll('video').forEach(v => v.pause());"
        sess.run(sess.evaluate_background_media("netflix.com", script))
        sess.run(sess.evaluate_background_media("youtube.com", script))
        print("[Spotify] Single Playback Enforcer: Browser media blanket pause completed.")
    except Exception as e:
        print(f"[Spotify] Browser auto-pause bridge exception: {e}")


def _get_spotify_hwnd_and_title():
    """Locates the running Spotify desktop application window frame handle and title string."""
    hwnd = win32gui.FindWindow("SpotifyMainWindow", None)
    if hwnd == 0:
        def enum_cb(hwnd_enum, extra):
            if win32gui.GetClassName(hwnd_enum) == "SpotifyMainWindow":
                extra.append(hwnd_enum)
            return True
        hwnds = []
        win32gui.EnumWindows(enum_cb, hwnds)
        if hwnds:
            hwnd = hwnds[0]
    if hwnd != 0:
        return hwnd, win32gui.GetWindowText(hwnd).strip()
    return 0, ""

def _focus_spotify_window(main_hwnd) -> bool:
    """Forces Spotify to foreground on Windows 11 by bypassing the OS Foreground Lock."""
    if main_hwnd == 0:
        return False
    try:
        fore_hwnd = win32gui.GetForegroundWindow()
        if fore_hwnd != main_hwnd:
            # Attach thread execution context to foreground frame to bypass security blocks
            fore_thread, _ = win32process.GetWindowThreadProcessId(fore_hwnd)
            curr_thread = win32api.GetCurrentThreadId()
            
            win32process.AttachThreadInput(curr_thread, fore_thread, True)
            win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(main_hwnd)
            win32process.AttachThreadInput(curr_thread, fore_thread, False)
        else:
            win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
        time.sleep(0.15)
        return True
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# PLAYBACK METHODS (Your WM_COMMAND Injections)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_spotify_started_and_focused(wait_s: float = 0.8) -> tuple[int, str]:
    """
    Ensures Spotify desktop app is started, then forces it to foreground.
    Critical after Netflix/YouTube because focus/audio sessions can get "stolen".
    """
    _enforce_spotify_priority()

    try:
        # Bring the app up first (matches working liked/new/made methods)
        os.system("start spotify:")
    except Exception:
        pass

    time.sleep(wait_s)

    hwnd, title = _get_spotify_hwnd_and_title()
    if hwnd != 0:
        _focus_spotify_window(hwnd)
        return hwnd, title

    return 0, ""


def _spotify_play():
    hwnd, _ = _ensure_spotify_started_and_focused()
    if hwnd != 0:
        # Do not rely on window title (it may become the current song name after playback).
        # Always trigger the existing play/pause macro used elsewhere in this module.
        pyautogui.hotkey('alt', 'shift', 'b')
        return "Spotify play/resume triggered (title-independent)."

    # Fallback
    win32api.keybd_event(0xB3, 0, 0, 0)
    win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
    return "Spotify play toggle fired via virtual key layout fallback."

def _spotify_pause():
    hwnd, _ = _ensure_spotify_started_and_focused()
    if hwnd != 0:
        # Do not rely on window title to detect playback state; just issue the same macro.
        pyautogui.hotkey('alt', 'shift', 'b')
        return "Spotify pause toggled (title-independent)."

    # Fallback
    win32api.keybd_event(0xB3, 0, 0, 0)
    win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
    return "Spotify pause toggle fired via virtual key layout fallback."

def _spotify_next():
    hwnd, _ = _ensure_spotify_started_and_focused()
    if hwnd != 0:
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 115, 0)
        return "Skipping to next Spotify track in the background, sir."
    keyboard.send("next track")
    return "Skipping track via global input stream fallback."

def _spotify_previous():
    _ensure_spotify_started_and_focused()
    VK_MEDIA_PREV_TRACK = 0xB1
    for _ in range(2):
        win32api.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
        win32api.keybd_event(VK_MEDIA_PREV_TRACK, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.35)
    return "Previous track."

# ─────────────────────────────────────────────────────────────────────────────
# SEARCH, LAYOUT RATIOS AND VOLUMES (Your Advanced UI Macros)
# ─────────────────────────────────────────────────────────────────────────────

def _spotify_search_and_play(query: str):
    if not query: return "No song name provided, sir."
    # Same "start+focus+priority" pattern as the working liked/new/made methods.
    _ensure_spotify_started_and_focused(wait_s=0.4)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()
    os.system(f"start spotify:search:{urllib.parse.quote(query)}")
    time.sleep(1.5)  

    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            if (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    if "spotify.exe" in win32process.GetModuleFileNameEx(handle, 0).lower():
                        main_hwnd = hwnd_enum
                        return False
                except Exception: pass
        return True
    win32gui.EnumWindows(enum_cb, None)

    if main_hwnd != 0:
        _focus_spotify_window(main_hwnd)
        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            x_ratio, y_ratio = 0.6870, 0.1775
        else:
            x_ratio, y_ratio = 0.5441, 0.1734

        pyautogui.click(left + int(width * x_ratio), top + int(height * y_ratio))
        time.sleep(0.06)
        pyautogui.moveTo(orig_x, orig_y)
        if original_hwnd != 0:
            try: win32gui.SetForegroundWindow(original_hwnd)
            except Exception: pass
        return f"Playing '{query}', sir."
    return "Spotify window verification failed."

def _spotify_play_liked_songs():
    _enforce_spotify_priority()
    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()
    os.system("start spotify:")
    time.sleep(0.6)

    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            if (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    if "spotify.exe" in win32process.GetModuleFileNameEx(handle, 0).lower():
                        main_hwnd = hwnd_enum
                        return False
                except Exception: pass
        return True
    win32gui.EnumWindows(enum_cb, None)

    if main_hwnd != 0:
        _focus_spotify_window(main_hwnd)
        pyautogui.hotkey('alt', 'shift', 's')
        time.sleep(1.0)  

        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            x_ratio, y_ratio = 0.0754, 0.4084
        else:
            x_ratio, y_ratio = 0.1581, 0.2742

        pyautogui.click(left + int(width * x_ratio), top + int(height * y_ratio))
        time.sleep(0.06)
        pyautogui.moveTo(orig_x, orig_y)
        if original_hwnd != 0:
            try: win32gui.SetForegroundWindow(original_hwnd)
            except Exception: pass
        return "Playing your Liked Songs, sir."
    return "Spotify window validation failed."

def _spotify_play_new_releases():
    _enforce_spotify_priority()
    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()
    os.system("start spotify:")
    time.sleep(0.6)

    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            if (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    if "spotify.exe" in win32process.GetModuleFileNameEx(handle, 0).lower():
                        main_hwnd = hwnd_enum
                        return False
                except Exception: pass
        return True
    win32gui.EnumWindows(enum_cb, None)

    if main_hwnd != 0:
        _focus_spotify_window(main_hwnd)
        pyautogui.hotkey('alt', 'shift', 'n')
        time.sleep(1.2)  

        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            x_ratio, y_ratio = 0.2350, 0.6021
        else:
            x_ratio, y_ratio = 0.5551, 0.4952

        pyautogui.click(left + int(width * x_ratio), top + int(height * y_ratio))
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)
        if original_hwnd != 0: win32gui.SetForegroundWindow(original_hwnd)
        return "Playing New Releases, sir."
    return "Spotify focus failed."

def _spotify_play_made_for_you():
    _enforce_spotify_priority()
    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()
    os.system("start spotify:")
    time.sleep(0.6)

    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            if (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    if "spotify.exe" in win32process.GetModuleFileNameEx(handle, 0).lower():
                        main_hwnd = hwnd_enum
                        return False
                except Exception: pass
        return True
    win32gui.EnumWindows(enum_cb, None)

    if main_hwnd != 0:
        _focus_spotify_window(main_hwnd)
        pyautogui.hotkey('alt', 'shift', 'm')
        time.sleep(1.2)  

        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            x_ratio, y_ratio = 0.1338, 0.6021
        else:
            x_ratio, y_ratio = 0.3076, 0.4952

        pyautogui.click(left + int(width * x_ratio), top + int(height * y_ratio))
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)
        if original_hwnd != 0: win32gui.SetForegroundWindow(original_hwnd)
        return "Playing Made For You mixes, sir."
    return "Spotify focus failed."

def _spotify_volume_up():
    """
    Brings Spotify to the front safely (even if minimized), and simulates a 
    double-tap of Ctrl + Up Arrow to turn up Spotify's internal volume.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os

    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    # Force wake up protocol layer
    os.system("start spotify:")
    time.sleep(0.4)

    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            if (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    if "spotify.exe" in win32process.GetModuleFileNameEx(handle, 0).lower():
                        main_hwnd = hwnd_enum
                        return False
                except Exception:
                    pass
        return True

    win32gui.EnumWindows(enum_cb, None)

    if main_hwnd != 0:
        win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
        time.sleep(0.05)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.15)

        # ── DOUBLE TAP EXECUTION ─────────────────────────────────────────────
        pyautogui.hotkey('ctrl', 'up')
        time.sleep(0.1)  # Precise mechanical gap delay between taps
        pyautogui.hotkey('ctrl', 'up')
        time.sleep(0.05)
        # ─────────────────────────────────────────────────────────────────────

        pyautogui.moveTo(orig_x, orig_y)
        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass
        return "Volume increased."
    return "Spotify window could not be verified."


def _spotify_volume_down():
    """
    Brings Spotify to the front safely (even if minimized), and simulates a 
    double-tap of Ctrl + Down Arrow to turn down Spotify's internal volume.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os

    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    os.system("start spotify:")
    time.sleep(0.4)

    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            if (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    if "spotify.exe" in win32process.GetModuleFileNameEx(handle, 0).lower():
                        main_hwnd = hwnd_enum
                        return False
                except Exception:
                    pass
        return True

    win32gui.EnumWindows(enum_cb, None)

    if main_hwnd != 0:
        win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
        time.sleep(0.05)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.15)

        # ── DOUBLE TAP EXECUTION ─────────────────────────────────────────────
        pyautogui.hotkey('ctrl', 'down')
        time.sleep(0.1)  
        pyautogui.hotkey('ctrl', 'down')
        time.sleep(0.05)
        # ─────────────────────────────────────────────────────────────────────

        pyautogui.moveTo(orig_x, orig_y)
        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass
        return "Volume decreased."
    return "Spotify window could not be verified."

def _spotify_like():
    """
    Brings Spotify out of background state securely, explicitly binds keyboard 
    focus to its layout thread using Win32 API calls, fires the native 
    'Like' shortcut, and restores your workspace focus immediately.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import ctypes
    import time

    # Low-level Windows Virtual Key Hardware constants
    VK_MENU = 0x12       # Alt key
    VK_SHIFT = 0x10      # Shift key
    VK_B = 0x42          # 'B' key
    KEYEVENTF_KEYUP = 0x0002

    # Save your exact current work window focus to restore it later
    original_hwnd = win32gui.GetForegroundWindow()
    main_hwnd = 0

    # 1. Enumerate and capture Spotify's true UI window container
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        # Stop searching if we already found it
        if main_hwnd != 0:
            return False
            
        try:
            window_class = win32gui.GetClassName(hwnd_enum)
            title = win32gui.GetWindowText(hwnd_enum)
            
            # Spotify uses Chromium Embedded Framework containers (0 or 1)
            if "Chrome_WidgetWin" in window_class and title:
                _, pid = win32process.GetWindowThreadProcessId(hwnd_enum)
                
                process_name = ""
                try:
                    # PROCESS_QUERY_LIMITED_INFORMATION
                    handle = win32api.OpenProcess(0x1000, False, pid)
                    process_name = win32process.GetModuleFileNameEx(handle, 0).lower()
                    win32api.CloseHandle(handle)
                except Exception:
                    pass # Prevent loop from breaking on secure processes
                
                # Check process path or fallback to a strict Title heuristic
                if "spotify.exe" in process_name or title in ["Spotify Premium", "Spotify Free", "Spotify"]:
                    main_hwnd = hwnd_enum
                    return False
        except Exception:
            pass
        return True

    win32gui.EnumWindows(enum_cb, None)

    # 2. Execute target window keystroke injection pipeline
    if main_hwnd != 0:
        try:
            placement = win32gui.GetWindowPlacement(main_hwnd)
            is_minimized = placement[1] == win32con.SW_SHOWMINIMIZED

            # Attach thread inputs to bypass Windows anti-focus-stealing mechanics
            foreground_thread = win32process.GetWindowThreadProcessId(original_hwnd)[0] if original_hwnd else 0
            spotify_thread = win32process.GetWindowThreadProcessId(main_hwnd)[0]

            if foreground_thread and foreground_thread != spotify_thread:
                ctypes.windll.user32.AttachThreadInput(foreground_thread, spotify_thread, True)

            # Trick Windows Focus Restrictions by simulating an introductory Alt press
            ctypes.windll.user32.keybd_event(VK_MENU, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

            # Pull window out of background sleep mode instantly if needed
            if is_minimized:
                win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
                
            win32gui.SetForegroundWindow(main_hwnd)
            time.sleep(0.05) # Give the Electron UI thread a split second to catch the focus state

            # 🚀 ACTION: Fires the native internal 'Like Track' shortcut combination
            ctypes.windll.user32.keybd_event(VK_MENU, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_B, 0, 0, 0)
            time.sleep(0.05)
            
            # Cleanly release keyboard inputs down the pipeline
            ctypes.windll.user32.keybd_event(VK_B, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            
            time.sleep(0.05)

            # 3. Seamlessly return focus and state to your workspace window
            if is_minimized:
                win32gui.ShowWindow(main_hwnd, win32con.SW_MINIMIZE)

            if original_hwnd and original_hwnd != main_hwnd:
                win32gui.SetForegroundWindow(original_hwnd)
                
            if foreground_thread and foreground_thread != spotify_thread:
                ctypes.windll.user32.AttachThreadInput(foreground_thread, spotify_thread, False)

            return "Track liked successfully, sir."
        except Exception as e:
            return f"Background keystroke delivery failed: {e}"

    return "Spotify execution thread could not be isolated in the system background."
# ─────────────────────────────────────────────────────────────────────────────
# MAIN ROUTER ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def _spotify_pause_if_running_only() -> str:
    """
    Pause Spotify ONLY if it's already running, without starting it.

    Fixes toggle/name issues by:
    - verifying Spotify window exists via class lookup (no dependency on track title)
    - focusing Spotify window before sending pause macro
    - sending the pause macro twice to reduce "toggle went the wrong way" risk
    """
    # Verify Spotify is running by process AND window presence (window-class is title-independent)
    try:
        import psutil
        spotify_running = any(
            p.info.get("name") and p.info["name"].lower() == "spotify.exe"
            for p in psutil.process_iter(['name'])
        )
    except Exception:
        return "Spotify running-state unknown; skipped pause_if_running_only."

    if not spotify_running:
        return "Spotify not running; skipped pause_if_running_only."

    hwnd, _title = _get_spotify_hwnd_and_title()
    if hwnd == 0:
        return "Spotify window not found; skipped pause_if_running_only."

    # Focus Spotify so hotkey is delivered to the right UI
    try:
        _focus_spotify_window(hwnd)
    except Exception:
        pass

    # Send pause macro with a small redundancy buffer
    try:
        pyautogui.hotkey('alt', 'shift', 'b')
        time.sleep(0.10)
        pyautogui.hotkey('alt', 'shift', 'b')
        return "Spotify paused (running-only, focused + double-tap)."
    except Exception:
        # Fallback: media key (may be toggle; double-tap reduces wrong-way toggles)
        try:
            win32api.keybd_event(0xB3, 0, 0, 0)
            win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.08)
            win32api.keybd_event(0xB3, 0, 0, 0)
            win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
            return "Spotify pause toggle fired via virtual key fallback (running-only, double-tap)."
        except Exception:
            return "pause_if_running_only failed to toggle pause."

def spotify_control(parameters: dict | None = None, player=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()
    print(f"[Spotify] Executing dedicated manual control token: {action!r}")

    if player:
        player.write_log(f"[Spotify] {action}")

    try:
        remember_action(
            tool="spotify_control",
            action=action,
            app="spotify"
        )
    except Exception:
        pass

    if action in ("play_pause", "toggle", "resume"):
        action = "play"
    elif action in ("skip", "forward"):
        action = "next"
    elif action in ("back", "rewind"):
        action = "previous"

    try:
        if action == "play":
            return _spotify_play()
        elif action == "pause":
            return _spotify_pause()
        elif action == "pause_if_running_only":
            return _spotify_pause_if_running_only()
        elif action == "next":
            return _spotify_next()
        elif action == "previous":
            return _spotify_previous()
        elif action == "search":
            return _spotify_search_and_play(params.get("value", ""))
        elif action == "liked_songs":
            return _spotify_play_liked_songs()
        elif action == "new_releases":
            return _spotify_play_new_releases()
        elif action == "made_for_you":
            return _spotify_play_made_for_you()
        elif action == "volume_up":
            return _spotify_volume_up()
        elif action == "volume_down":
            return _spotify_volume_down()
        elif action == "like_track":
            return _spotify_like()
        else:
            return f"Unknown Spotify manual action: '{action}'."
    except Exception as e:
        return f"Spotify routing failed: {e}"