# actions/computer_settings.py
import json
import re
import sys
import time
import os
import subprocess
import platform
import keyboard
import urllib.parse 
from pathlib import Path
from core.utils import get_base_dir, get_api_key, get_os, is_windows, is_mac, is_linux, log
from core.intent_memory import remember_action

_OS = get_os()  

if is_windows():
    import win32gui
    import win32con
    import win32api
    import win32process
    import win32com.client
    import ctypes
    from pywinauto import Application

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False


def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0"


# ─────────────────────────────────────────────────────────────────────────────
# VOLUME HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_volume_value(value) -> int | None:
    """
    Robustly extract an integer 0-100 from any value:
      "50%"  "50 %"  "50percent"  "50"  50  "half"  "max"  "min"  etc.
    Returns None if unparseable.
    """
    if value is None:
        return None

    # Named levels
    named = {
        "max": 100, "maximum": 100, "full": 100,
        "min": 0,   "minimum": 0,   "mute": 0,   "silent": 0,
        "half": 50, "medium": 50,   "mid": 50,   "middle": 50,
        "low": 25,  "quiet": 25,
        "high": 75, "loud": 75,
    }
    s = str(value).strip().lower()
    if s in named:
        return named[s]

    # Extract first integer in the string (handles "50%", "50 %", "vol 50", etc.)
    m = re.search(r'\d+', s)
    if m:
        return max(0, min(100, int(m.group())))

    return None


def volume_up():
    if is_windows():
        import ctypes
        for _ in range(5):
            ctypes.windll.user32.keybd_event(0xAF, 0, 0, 0)
    elif _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e",
             "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True
        )
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
                       capture_output=True)


def volume_down():
    if is_windows():
        import ctypes
        for _ in range(5):
            ctypes.windll.user32.keybd_event(0xAE, 0, 0, 0)
    elif _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e",
             "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True
        )
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
                       capture_output=True)


def volume_mute():
    if is_windows():
        import ctypes
        ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
                       capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                       capture_output=True)


def volume_set(value: int) -> str:
    """
    Set Windows master volume to an exact percentage without ever resetting to 0%.

    Method 1 — pycaw      : direct COM call, instant, exact
    Method 2 — ctypes COM : pure stdlib, no pip packages required, exact
    Method 3 — PowerShell : inline C# COM call, exact
    Method 4 — relative   : reads current volume first, adjusts by delta only
    """
    value = max(0, min(100, int(value)))

    if is_windows():

        # ── Method 1: pycaw ──────────────────────────────────────────────────
        # Requires: pip install pycaw comtypes
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            speakers  = AudioUtilities.GetSpeakers()
            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol_iface = cast(interface, POINTER(IAudioEndpointVolume))
            vol_iface.SetMute(0, None)
            vol_iface.SetMasterVolumeLevelScalar(value / 100.0, None)
            print(f"[Settings] ✅ Volume → {value}%  (pycaw)")
            return f"Volume set to {value}%."
        except Exception as e:
            print(f"[Settings] pycaw failed: {e}")

        # ── Method 2: pure ctypes COM — no external packages ─────────────────
        # Uses Windows Core Audio API (IAudioEndpointVolume) via ctypes only.
        # Works on Windows Vista and later.  Never touches key presses.
        try:
            import ctypes
            import ctypes.wintypes

            # ---- minimal COM scaffolding ----
            CLSCTX_ALL    = 0x17
            CLSID_str     = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
            IID_enum_str  = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
            IID_dev_str   = "{D666063F-1587-4E43-81F1-B948E807363F}"
            IID_vol_str   = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)

            # StringToGUID helper
            def _guid(s: str):
                g = ctypes.c_buffer(16)
                ole32.CLSIDFromString(ctypes.c_wchar_p(s), g)
                return g

            clsid  = _guid(CLSID_str)
            iid_e  = _guid(IID_enum_str)
            iid_d  = _guid(IID_dev_str)
            iid_v  = _guid(IID_vol_str)

            # Create MMDeviceEnumerator
            p_enum = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(clsid, None, CLSCTX_ALL, iid_e,
                                        ctypes.byref(p_enum))
            if hr:
                raise OSError(f"CoCreateInstance hr={hr:#010x}")

            # GetDefaultAudioEndpoint(eRender=0, eConsole=1)
            # vtable slot 4 (0-indexed): IMMDeviceEnumerator::GetDefaultAudioEndpoint
            GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p,   # this
                ctypes.c_uint,     # dataFlow
                ctypes.c_uint,     # role
                ctypes.POINTER(ctypes.c_void_p),  # ppDevice
            )(ctypes.cast(p_enum, ctypes.POINTER(ctypes.c_void_p * 10)).contents[4])

            p_dev = ctypes.c_void_p()
            hr = GetDefaultAudioEndpoint(p_enum, 0, 1, ctypes.byref(p_dev))
            if hr:
                raise OSError(f"GetDefaultAudioEndpoint hr={hr:#010x}")

            # IMMDevice::Activate → IAudioEndpointVolume
            # vtable slot 3: IMMDevice::Activate
            Activate = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p,   # this
                ctypes.c_char_p,   # iid
                ctypes.c_uint,     # clsCtx
                ctypes.c_void_p,   # pActivationParams
                ctypes.POINTER(ctypes.c_void_p),  # ppInterface
            )(ctypes.cast(p_dev, ctypes.POINTER(ctypes.c_void_p * 10)).contents[3])

            p_vol = ctypes.c_void_p()
            hr = Activate(p_dev, iid_v, CLSCTX_ALL, None, ctypes.byref(p_vol))
            if hr:
                raise OSError(f"IMMDevice::Activate hr={hr:#010x}")

            # IAudioEndpointVolume::SetMasterVolumeLevelScalar — vtable slot 7
            SetVolume = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p,  # this
                ctypes.c_float,   # fLevel
                ctypes.c_void_p,  # pguidEventContext (NULL)
            )(ctypes.cast(p_vol, ctypes.POINTER(ctypes.c_void_p * 20)).contents[7])

            # IAudioEndpointVolume::SetMute — vtable slot 13
            SetMute = ctypes.WINFUNCTYPE(
                ctypes.HRESULT,
                ctypes.c_void_p,  # this
                ctypes.c_bool,    # bMute
                ctypes.c_void_p,  # pguidEventContext
            )(ctypes.cast(p_vol, ctypes.POINTER(ctypes.c_void_p * 20)).contents[13])

            SetMute(p_vol, False, None)
            SetVolume(p_vol, ctypes.c_float(value / 100.0), None)

            print(f"[Settings] ✅ Volume → {value}%  (ctypes COM)")
            return f"Volume set to {value}%."
        except Exception as e:
            print(f"[Settings] ctypes COM failed: {e}")

        # ── Method 3: PowerShell inline C# ───────────────────────────────────
        # Requires PowerShell 5+ (standard on Windows 10/11).
        # Compiles a tiny C# shim at runtime, calls the same COM API.
        try:
            scalar     = value / 100.0
            # Use a unique class name based on target value to avoid
            # "type already exists" errors on repeated calls.
            cls_name   = f"VC{value}x{abs(hash(str(value))) % 9999}"
            ps_script  = f"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume{{int a();int b();int c();int d();
int SetMasterVolumeLevelScalar(float f,Guid g);int e();
int GetMasterVolumeLevelScalar(out float f);int f2();int g2();int h();int i();
int SetMute([MarshalAs(UnmanagedType.Bool)]bool m,Guid g);int GetMute(out bool m);}}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice{{int Activate(ref Guid g,uint c,IntPtr p,[MarshalAs(UnmanagedType.IUnknown)]out object o);}}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator{{int x();int GetDefaultAudioEndpoint(uint d,uint r,out IMMDevice e);}}
[ComImport,Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]class MMDECom{{}}
public class {cls_name}{{public static void Set(float v){{
var e=(IMMDeviceEnumerator)new MMDECom();
IMMDevice d;e.GetDefaultAudioEndpoint(0,1,out d);
var g=typeof(IAudioEndpointVolume).GUID;object o;d.Activate(ref g,23,IntPtr.Zero,out o);
var a=(IAudioEndpointVolume)o;a.SetMute(false,Guid.Empty);
a.SetMasterVolumeLevelScalar(v,Guid.Empty);}}}}
'@ -Language CSharp -ErrorAction Stop
[{cls_name}]::Set({scalar}f)
"""
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True, timeout=15
            )
            if res.returncode == 0:
                print(f"[Settings] ✅ Volume → {value}%  (PowerShell C#)")
                return f"Volume set to {value}%."
            raise RuntimeError(res.stderr.strip()[:300])
        except Exception as e:
            print(f"[Settings] PowerShell C# failed: {e}")

        # ── Method 4: relative keystroke adjustment — NEVER goes to 0% ───────
        # Reads the current volume level via PowerShell first,
        # then presses UP or DOWN only by the delta needed.
        # Less precise (±2%) but completely safe — volume never resets.
        try:
            import ctypes

            # Read current volume from Windows registry (reliable, no deps)
            ps_read = (
                "(Get-ItemProperty "
                "'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
                "MMDevices\\Audio\\Render\\*\\Properties' "
                "-ErrorAction SilentlyContinue | "
                "Where-Object {$_.'(default)' -eq $null} | "
                "Select-Object -First 1 | "
                "ForEach-Object { [math]::Round($_.'{9b212089-bac5-4f39-978d-2aced44a22af},0' * 100) })"
            )
            # Simpler: use the master volume level directly from the audio stack
            ps_simple = "[math]::Round((Get-AudioDevice -Playback | Select-Object -Expand Volume))"

            # Most reliable single-line approach on Win10/11
            ps_vol = (
                "$vol = 50; "
                "try { "
                "  Add-Type -AssemblyName System.Windows.Forms; "
                "  $shell = New-Object -ComObject WScript.Shell; "
                "} catch {}; "
                "try { "
                "  [System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "} catch {}; "
                "Write-Output 50"    # fallback: assume 50 if we can't read
            )

            # Best approach: use the same C# but only to READ the volume
            ps_get = r"""
try {
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume2{int a();int b();int c();int d();int e();int f();
int GetMasterVolumeLevelScalar(out float v);int g();int h();int i();int j();int k();int l();}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice2{int Activate(ref Guid g,uint c,IntPtr p,[MarshalAs(UnmanagedType.IUnknown)]out object o);}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator2{int x();int GetDefaultAudioEndpoint(uint d,uint r,out IMMDevice2 e);}
[ComImport,Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]class MMDECom2{}
public class VCReader{public static float Get(){
var e=(IMMDeviceEnumerator2)new MMDECom2();
IMMDevice2 d;e.GetDefaultAudioEndpoint(0,1,out d);
var g=typeof(IAudioEndpointVolume2).GUID;object o;d.Activate(ref g,23,IntPtr.Zero,out o);
var a=(IAudioEndpointVolume2)o;float v;a.GetMasterVolumeLevelScalar(out v);return v;}}
'@ -Language CSharp -ErrorAction Stop
[math]::Round([VCReader]::Get() * 100)
} catch { Write-Output 50 }
"""
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_get],
                capture_output=True, text=True, timeout=10
            )
            current = 50  # safe default
            if res.returncode == 0:
                try:
                    current = int(res.stdout.strip().splitlines()[-1])
                except Exception:
                    pass

            print(f"[Settings] Current volume: {current}% → target: {value}%")

            # Press only the delta — never resets to 0
            KEYDOWN, KEYUP  = 0, 2
            VK_VOL_DOWN     = 0xAE
            VK_VOL_UP       = 0xAF

            def _press(vk: int):
                ctypes.windll.user32.keybd_event(vk, 0, KEYDOWN, 0)
                time.sleep(0.015)
                ctypes.windll.user32.keybd_event(vk, 0, KEYUP, 0)
                time.sleep(0.015)

            delta = value - current          # e.g. current=70, target=40 → delta=-30
            steps = abs(round(delta / 2))    # each key press ≈ 2%
            vk    = VK_VOL_UP if delta > 0 else VK_VOL_DOWN

            for _ in range(steps):
                _press(vk)

            print(f"[Settings] ✅ Volume adjusted by {delta:+d}%  (relative keystrokes)")
            return f"Volume set to approximately {value}%."
        except Exception as e:
            return (
                f"All volume methods failed. "
                f"Run: pip install pycaw comtypes  (last error: {e})"
            )

    elif _OS == "Darwin":
        try:
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {value}"],
                capture_output=True, check=True
            )
            return f"Volume set to {value}%."
        except Exception as e:
            return f"Failed to set volume on macOS: {e}"

    else:  # Linux
        try:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
                capture_output=True, check=True
            )
            return f"Volume set to {value}%."
        except Exception as e:
            return f"Failed to set volume on Linux: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# BRIGHTNESS & WINDOWS LAYOUTS
# ─────────────────────────────────────────────────────────────────────────────

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 144'], capture_output=True)
    elif _OS == "Linux":
        subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
    else:
        subprocess.run(["powershell", "-Command", "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, [math]::Min(100, (Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"], capture_output=True)

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 145'], capture_output=True)
    elif _OS == "Linux":
        subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
    else:
        subprocess.run(["powershell", "-Command", "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, [math]::Max(0, (Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"], capture_output=True)

def close_app():
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Windows":   pyautogui.hotkey("win", "up")
    elif _OS == "Darwin":  subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "f" using {control down, command down}'], capture_output=True)

def snap_left():      pyautogui.hotkey("win", "left") if is_windows() else None
def snap_right():     pyautogui.hotkey("win", "right") if is_windows() else None
def switch_window():  pyautogui.hotkey("command", "tab") if _OS == "Darwin" else pyautogui.hotkey("alt", "tab")
def show_desktop():   pyautogui.hotkey("win", "d") if is_windows() else None
def open_task_manager(): subprocess.Popen(["open", "-a", "Activity Monitor"]) if _OS == "Darwin" else pyautogui.hotkey("ctrl", "shift", "esc")

# ─────────────────────────────────────────────────────────────────────────────
# BROWSER & NAVIGATION
# ─────────────────────────────────────────────────────────────────────────────

def focus_search():   pyautogui.hotkey("command", "l") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "l")
def pause_video():    pyautogui.press("space")
def refresh_page():   pyautogui.hotkey("command", "r") if _OS == "Darwin" else pyautogui.press("f5")
def close_tab():      pyautogui.hotkey("command", "w") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "w")
def new_tab():        pyautogui.hotkey("command", "t") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "t")
def next_tab():       pyautogui.hotkey("command", "shift", "bracketright") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "tab")
def prev_tab():       pyautogui.hotkey("command", "shift", "bracketleft") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "shift", "tab")
def go_back():        pyautogui.hotkey("command", "left") if _OS == "Darwin" else pyautogui.hotkey("alt", "left")
def go_forward():     pyautogui.hotkey("command", "right") if _OS == "Darwin" else pyautogui.hotkey("alt", "right")
def zoom_in():        pyautogui.hotkey("command", "equal") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "equal")
def zoom_out():       pyautogui.hotkey("command", "minus") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "minus")
def zoom_reset():     pyautogui.hotkey("command", "0") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "0")
def find_on_page():   pyautogui.hotkey("command", "f") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "f")
def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)

def _ensure_active_window_focus_preflight(target_keywords: tuple[str, ...] = ()) -> bool:
    """Windows-only: verifies that JARVIS simulated input should be routed to the
    current active window. If focus seems to be lost, attempts to restore it.

    This is intentionally best-effort and must never crash automation.
    """
    if not (is_windows() and _PYAUTOGUI):
        return True

    try:
        import win32gui
        import win32process
        import psutil  # optional but usually available

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False

        title = (win32gui.GetWindowText(hwnd) or "").lower()

        if target_keywords:
            if not any(kw.lower() in title for kw in target_keywords):
                # If keywords are specified, treat mismatch as a weak failure.
                # Still try to restore focus to the foreground window.
                return False

        # Touch the active window to reduce race where OS focus changes.
        try:
            win32gui.ShowWindow(hwnd, 5)  # SW_SHOW
        except Exception:
            pass

        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

        # Small throttle to prevent message queue drops.
        time.sleep(0.02)
        return True
    except Exception:
        return True


def _sleep_micro():
    # Micro-sleep to prevent Windows ignoring rapid macro loops.
    time.sleep(0.02)


def scroll_up(amount: int = 500):
    # Pre-flight: keep input on focused window (best-effort).
    _ensure_active_window_focus_preflight()
    pyautogui.scroll(amount)
    _sleep_micro()


def scroll_down(amount: int = 500):
    _ensure_active_window_focus_preflight()
    pyautogui.scroll(-amount)
    _sleep_micro()


def scroll_top():
    _ensure_active_window_focus_preflight()
    pyautogui.hotkey("command", "up") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "home")
    _sleep_micro()


def scroll_bottom():
    _ensure_active_window_focus_preflight()
    pyautogui.hotkey("command", "down") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "end")
    _sleep_micro()

def page_up():        pyautogui.press("pageup")
def page_down():      pyautogui.press("pagedown")

# ─────────────────────────────────────────────────────────────────────────────
# CLIPBOARD / EDITING
# ─────────────────────────────────────────────────────────────────────────────

def copy():           pyautogui.hotkey("command", "c") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "c")
def paste():          pyautogui.hotkey("command", "v") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "v")
def cut():            pyautogui.hotkey("command", "x") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "x")
def undo():           pyautogui.hotkey("command", "z") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "z")
def redo():           pyautogui.hotkey("command", "shift", "z") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "y")
def select_all():     pyautogui.hotkey("command", "a") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "a")
def save_file():      pyautogui.hotkey("command", "s") if _OS == "Darwin" else pyautogui.hotkey("ctrl", "s")
def press_enter():    pyautogui.press("enter")
def press_escape():   pyautogui.press("escape")
def _hotkey_preflight(*args, **kwargs):
    _ensure_active_window_focus_preflight()
    _sleep_micro()


def hotkey(*keys):
    """Windows-focused hotkey with message-queue friendly pacing."""
    _ensure_active_window_focus_preflight()
    try:
        pyautogui.hotkey(*keys)
    finally:
        _sleep_micro()


def press_key(key: str):
    _ensure_active_window_focus_preflight()
    pyautogui.press(key)
    _sleep_micro()

def type_text(text: str, press_enter_after: bool = False):
    if not text: return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM INFRASTRUCTURE CONTROL
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot(): pyautogui.hotkey("win", "shift", "s") if is_windows() else pyautogui.hotkey("command", "shift", "3")
def lock_screen():     pyautogui.hotkey("win", "l") if is_windows() else subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
def open_system_settings(): pyautogui.hotkey("win", "i") if is_windows() else subprocess.Popen(["open", "-a", "System Preferences"])
def open_file_explorer(): pyautogui.hotkey("win", "e") if is_windows() else subprocess.Popen(["open", str(Path.home())])
def open_run():        pyautogui.hotkey("win", "r") if is_windows() else None

def sleep_display():
    if is_windows():
        import ctypes
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)

def dark_mode():
    if is_windows():
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize", 0, winreg.KEY_ALL_ACCESS)
        current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.SetValueEx(key, "AppsUseLightTheme",   0, winreg.REG_DWORD, 1 - current)
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
        winreg.CloseKey(key)

def toggle_wifi():
    if is_windows():
        subprocess.run(["powershell", "-Command", "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'}; if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false } else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"], capture_output=True)

def toggle_bluetooth() -> str:
    if is_windows():
        ps_script = (
            "[Void][Type]::GetType('Windows.Devices.Radios.Radio, Windows, ContentType=WindowsRuntime'); "
            "$radios = [Windows.Devices.Radios.Radio]::GetRadiosAsync(); "
            "while (-not $radios.IsCompleted) { Start-Sleep -Milliseconds 10 }; "
            "$bth = $radios.GetResults() | Where-Object { $_.Kind -eq 'Bluetooth' } | Select-Object -First 1; "
            "if ($bth) { "
            "    $newState = if ($bth.State -eq 'On') { 'Off' } else { 'On' }; "
            "    $set = $bth.SetStateAsync($newState); "
            "    while (-not $set.IsCompleted) { Start-Sleep -Milliseconds 10 }; "
            "    Write-Output \"Bluetooth turned $newState\" "
            "} else { Write-Output 'No Bluetooth hardware detected' }"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script], capture_output=True, text=True)
        return res.stdout.strip()
    return "Optimized for Windows only."

def connect_bluetooth_device(device_name: str) -> str:
    # Method 1: btcom (if installed)
    try:
        result = subprocess.run(["btcom", "-c", "-n", device_name], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return f"Audio routed to {device_name}, sir."
    except FileNotFoundError:
        pass
    
    # Fallback: open Bluetooth settings
    try:
        subprocess.run(["powershell", "-Command", "Start-Process ms-settings:bluetooth"], capture_output=True, timeout=5)
        return f"Opened Bluetooth settings. Please connect to {device_name} manually."
    except Exception as e:
        return f"Could not connect: {e}"

def open_bluetooth_settings() -> str:
    os.system("start ms-settings:bluetooth") if is_windows() else None
    return "Opened Bluetooth settings window, sir."

def restart_computer(): subprocess.run(["shutdown", "/r", "/t", "10"]) if is_windows() else None
def shutdown_computer(): subprocess.run(["shutdown", "/s", "/t", "10"]) if is_windows() else None





# ─────────────────────────────────────────────────────────────────────────────
# ACTION MAP REGISTER
# ─────────────────────────────────────────────────────────────────────────────

ACTION_MAP: dict[str, callable] = {
    "volume_up": volume_up,
    "volume_down": volume_down,
    "decrease_volume": volume_down,
    "increase_volume": volume_up,
    "mute":           volume_mute,
    "unmute":         volume_mute,
    "toggle_mute":    volume_mute,
    "volume_mute":    volume_mute,
    "brightness_up":  brightness_up,
    "brightness_down":brightness_down,
    "sleep_display":  sleep_display,
    "screen_off":     sleep_display,
    "pause_video":    pause_video,
    "play_pause":     pause_video,
    "close_app":      close_app,
    "close_window":   close_window,
    "full_screen":    full_screen,
    "fullscreen":     full_screen,
    "minimize":       minimize_window,
    "maximize":       maximize_window,
    "snap_left":      snap_left,
    "snap_right":     snap_right,
    "switch_window":  switch_window,
    "show_desktop":   show_desktop,
    "task_manager":   open_task_manager,
    "focus_search":   focus_search,
    "refresh_page":   refresh_page,
    "reload":         refresh_page,
    "close_tab":      close_tab,
    "new_tab":        new_tab,
    "next_tab":       next_tab,
    "prev_tab":       prev_tab,
    "go_back":        go_back,
    "go_forward":     go_forward,
    "zoom_in":        zoom_in,
    "zoom_out":       zoom_out,
    "zoom_reset":     zoom_reset,
    "find_on_page":   find_on_page,
    "scroll_up":      scroll_up,
    "scroll_down":    scroll_down,
    "scroll_top":     scroll_top,
    "scroll_bottom":  scroll_bottom,
    "page_up":        page_up,
    "page_down":      page_down,
    "copy":           copy,
    "paste":          paste,
    "cut":            cut,
    "undo":           undo,
    "redo":           redo,
    "select_all":     select_all,
    "save":           save_file,
    "enter":          press_enter,
    "escape":         press_escape,
    "screenshot":     take_screenshot,
    "lock_screen":    lock_screen,
    "open_settings":  open_system_settings,
    "file_explorer":  open_file_explorer,
    "open_run":       open_run,
    "dark_mode":      dark_mode,
    "toggle_wifi":    toggle_wifi,
    "restart":        restart_computer,
    "shutdown":       shutdown_computer,
    "toggle_bluetooth": toggle_bluetooth,
    "bluetooth_settings": open_bluetooth_settings,
}


def _detect_action(description: str) -> dict:
    from google import genai
    try:
        # Initializing via the upgraded standard client layout
        client = genai.Client(api_key=get_api_key())
        available = ", ".join(sorted(ACTION_MAP.keys())) + ", volume_set, type_text, press_key, reload_n"
        prompt = f"User instruction: \"{description}\"\nAvailable actions: {available}\nReturn JSON string ONLY: {{\"action\": \"name\", \"value\": null}}"
        
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt
        )
        text = re.sub(r"```(?:json)?", "", resp.text).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception:
        return {"action": description.lower().replace(" ", "_"), "value": None}


def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI: return "pyautogui missing."
    params      = parameters or {}
    raw_action  = str(params.get("action", "")).strip()
    description = str(params.get("description", "")).strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None: value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")
    if action == "volume" and value is not None: action = "volume_set"
    elif action in ("toggle_mute", "volume_mute"): action = "mute"

    parsed_int = _parse_volume_value(value)

    if action in ("volume_set", "set_volume") or (action in ("volume",) and parsed_int is not None):
        vol = parsed_int if parsed_int is not None else 50
        return volume_set(vol)

    if action in ("volume_mute", "mute", "unmute", "toggle_mute"):
        volume_mute()
        return "Volume mute state toggled."

    if action == "volume_up":
        volume_up()
        return "Volume increased."

    if action == "volume_down":
        volume_down()
        return "Volume decreased."

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text: return "No text provided."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed successfully, sir."

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key: return "No key specified."
        press_key(key)
        return f"Pressed: {key}"


    if action == "connect_device":
        target = str(value or params.get("description", "")).strip()
        return connect_bluetooth_device(target) if target else "No device target verified."

    func = ACTION_MAP.get(action)
    if not func: return f"Unknown action: '{raw_action}'."
    func()
    return f"Done: {action}."

# Backward compatibility alias
computer_control = computer_settings


def connect_bluetooth_device(device_name: str) -> str:
    # Method 1: btcom (if installed)
    try:
        result = subprocess.run(["btcom", "-c", "-n", device_name], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return f"Audio routed to {device_name}, sir."
    except FileNotFoundError:
        pass
    
    # Method 2: PowerShell - check paired devices and connect
    ps_check = r"powershell -Command \"Get-PairedDevices | Where-Object {$_.Name -like '*{device_name}*}%{$_.ConnectionStatus}\""
    try:
        result = subprocess.run(ps_check, shell=True, capture_output=True, text=True, timeout=15)
        if 'Connected' in result.stdout:
            return f"Audio routed to {device_name}, sir."
        # Try to connect - open settings for manual if auto fails
        subprocess.run(["powershell", "-Command", "Start-Process ms-settings:bluetooth"], capture_output=True, timeout=5)
        return f"Opened settings. Please connect to {device_name}."
    except:
        pass
    
    # Fallback
    try:
        subprocess.run(["powershell", "-Command", "Start-Process ms-settings:bluetooth"], capture_output=True, timeout=5)
        return f"Opened Bluetooth settings. Please connect to {device_name} manually."
    except Exception as e:
        return f"Could not connect: {e}"
