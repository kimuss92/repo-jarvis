# computer_settings.py
# computer_settings.py
import json
import re
import sys
import time
import os
import subprocess
import platform
import keyboard
from core.utils import get_base_dir, get_api_key, get_os, is_windows, is_mac, is_linux, log
from core.intent_memory import remember_action
_OS = get_os()  
from pathlib import Path
import win32gui
import win32con
import win32api
import win32com.client
import urllib.parse 
    
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
# BRIGHTNESS
# ─────────────────────────────────────────────────────────────────────────────

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 144'],
            capture_output=True
        )
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"], capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed: {e}")


def brightness_down():
    if _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 145'],
            capture_output=True
        )
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"], capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# WINDOW / APP MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

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
    if _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "f" using {control down, command down}'],
            capture_output=True
        )
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                           capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if is_windows():  pyautogui.hotkey("win", "left")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"], capture_output=True)
        except Exception:
            pass

def snap_right():
    if is_windows():  pyautogui.hotkey("win", "right")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"], capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":    pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if is_windows():
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER / NAVIGATION
# ─────────────────────────────────────────────────────────────────────────────

def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

def pause_video():      pyautogui.press("space")

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


# ─────────────────────────────────────────────────────────────────────────────
# SCROLL
# ─────────────────────────────────────────────────────────────────────────────

def scroll_up(amount: int = 500):   pyautogui.scroll(amount)
def scroll_down(amount: int = 500): pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


# ─────────────────────────────────────────────────────────────────────────────
# CLIPBOARD / EDITING
# ─────────────────────────────────────────────────────────────────────────────

def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
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
# SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

def take_screenshot():
    if is_windows():
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if is_windows():
        pyautogui.hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if is_windows():
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if is_windows():
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if is_windows():
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if is_windows():
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(
            ["osascript", "-e",
             'tell app "System Events" to tell appearance preferences to set dark mode to not dark mode'],
            capture_output=True
        )
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme",   0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            new_scheme = "'default'" if "dark" in result.stdout.strip() else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface  = _get_macos_wifi_interface()
        result = subprocess.run(["networksetup", "-getairportpower", iface],
                                capture_output=True, text=True)
        state  = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state], capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if is_windows():
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", 'tell application "System Events" to restart'],
                       capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if is_windows():
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", 'tell application "System Events" to shut down'],
                       capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)

def _send_spotify_app_command(app_command_id: int) -> bool:
    """
    Sends a direct WinProc Media AppCommand injection to the background 
    Spotify core loop without changing focus or bringing up the window.
    """
    import win32gui
    import win32con

    # Find the kernel window by native class name context
    hwnd = win32gui.FindWindow("SpotifyMainWindow", None)
    if hwnd == 0:
        # Fallback enumeration lookup layer if running inside container wrappers
        def enum_cb(hwnd_enum, extra):
            if win32gui.GetClassName(hwnd_enum) == "SpotifyMainWindow":
                extra.append(hwnd_enum)
            return True
        hwnds = []
        win32gui.EnumWindows(enum_cb, hwnds)
        if hwnds:
            hwnd = hwnds[0]
            
    if hwnd != 0:
        WM_APPCOMMAND = 0x0319
        # Shift command bits directly into high-word signature allocation
        win32gui.SendMessage(hwnd, WM_APPCOMMAND, 0, app_command_id << 16)
        return True
    return False

import urllib.parse

import urllib.parse

def spotify_search_and_play(query: str):
    """
    Searches for a song on Spotify via URI protocols, determines if the window
    is maximized or snapped, and applies the corresponding mouse profile.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os
    import urllib.parse
    import ctypes

    if not query:
        return "No song name provided, sir."

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # Save tracking environments
    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    # 1. Fire system shortcut search sequence
    safe_query = urllib.parse.quote(query)
    os.system(f"start spotify:search:{safe_query}")
    time.sleep(1.5)  

    # 2. Extract valid active window handle process boundary
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
        # 3. Pull interface safely to the front to register clicks
        win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
        time.sleep(0.1)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.2)

        # 4. Measure physical geometry boundaries
        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        # ── DUAL PROFILE RESOLUTION SWITCH ────────────────────────────────────
        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            # 🖥️ PASTE YOUR MAXIMIZED RATIOS HERE
            x_ratio = 0.6870
            y_ratio = 0.1775
        else:
            # 📱 PASTE YOUR SNAPPED RATIOS HERE
            x_ratio = 0.5441
            y_ratio = 0.1734
        # ──────────────────────────────────────────────────────────────────────

        click_x = left + int(width * x_ratio)
        click_y = top + int(height * y_ratio)

        # 5. Perform click and instantly restore mouse coordinates
        pyautogui.click(click_x, click_y)
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)

        # 6. Return context safely to working terminal
        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass

        return f"Playing '{query}' using calculated window layout parameters, sir."

    return "Spotify application window could not be verified."

def spotify_play_liked_songs():
    """
    Brings Spotify to the front (even if minimized), triggers the Alt+Shift+S shortcut to go to Liked Songs,
    determines if the window is maximized or snapped, and clicks the main playlist play button.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # Save original foreground window window handle and mouse positions
    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    # 🚀 FIX: Force Windows to un-minimize/wake up Spotify via URI protocol BEFORE scanning handles
    os.system("start spotify:")
    time.sleep(0.6)  # Give Windows a brief moment to restore the graphical engine frame

    # 1. Identify the true running visible Spotify process window frame
    main_hwnd = 0
    def enum_cb(hwnd_enum, extra):
        nonlocal main_hwnd
        if win32gui.IsWindowVisible(hwnd_enum):
            rect = win32gui.GetWindowRect(hwnd_enum)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w > 300 and h > 300:
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
        # 2. Force window restoration and focus context
        win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
        time.sleep(0.1)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.2)

        # 3. Send the native Liked Songs shortcut navigation command
        pyautogui.hotkey('alt', 'shift', 's')
        time.sleep(1.0)  # Wait for the main playlist view content stream to load

        # 4. Measure geometric workspace dimension bounds
        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        # 5. Apply layout ratio tracking depending on window layout state
        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            # Calibrated maximized view coordinates
            x_ratio = 0.0754
            y_ratio = 0.4084
        else:
            # Calibrated snapped split-screen view coordinates
            x_ratio = 0.1581
            y_ratio = 0.2742

        click_x = left + int(width * x_ratio)
        click_y = top + int(height * y_ratio)

        # 6. Execute play activation click and reset background cursor tracks
        pyautogui.click(click_x, click_y)
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)

        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass

        return "Enjoy your music, sir."

    return "Spotify application layout window could not be verified."


def spotify_play_new_releases():
    """
    Brings Spotify to the front (even if minimized), triggers Alt+Shift+N to go to New Releases,
    determines if the window is maximized or snapped, and clicks the play button.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    # 🚀 FIX: Force Windows to un-minimize/wake up Spotify via URI protocol BEFORE scanning handles
    os.system("start spotify:")
    time.sleep(0.6)  # Give Windows a brief moment to restore the graphical engine frame

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
        time.sleep(0.1)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.2)

        # Trigger shortcut for New Releases
        pyautogui.hotkey('alt', 'shift', 'n')
        time.sleep(1.2)  # Wait for page layout content rendering

        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            # Calibrated maximized view coordinates
            x_ratio = 0.2350
            y_ratio = 0.6021
        else:
            # Calibrated snapped split-screen view coordinates
            x_ratio = 0.5551
            y_ratio = 0.4952

        click_x = left + int(width * x_ratio)
        click_y = top + int(height * y_ratio)

        pyautogui.click(click_x, click_y)
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)

        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass

        return "Enjoy your music, sir."
    return "Spotify window could not be verified."


def spotify_play_made_for_you():
    """
    Brings Spotify to the front (even if minimized), triggers Alt+Shift+M to go to Made For You,
    determines if the window is maximized or snapped, and clicks the play button.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    # 🚀 FIX: Force Windows to un-minimize/wake up Spotify via URI protocol BEFORE scanning handles
    os.system("start spotify:")
    time.sleep(0.6)  # Give Windows a brief moment to restore the graphical engine frame

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
        time.sleep(0.1)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.2)

        # Trigger shortcut for Made For You
        pyautogui.hotkey('alt', 'shift', 'm')
        time.sleep(1.2)  

        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            # Calibrated maximized view coordinates
            x_ratio = 0.1338
            y_ratio = 0.6021
        else:
            # Calibrated snapped split-screen view coordinates
            x_ratio = 0.3076
            y_ratio = 0.4952

        click_x = left + int(width * x_ratio)
        click_y = top + int(height * y_ratio)

        pyautogui.click(click_x, click_y)
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)

        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass

        return "Enjoy your music, sir."
    return "Spotify window could not be verified."

def spotify_volume_up():
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


def spotify_volume_down():
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

def _get_spotify_hwnd_and_title():
    """Locates the running Spotify desktop application window frame handle and title string."""
    import win32gui
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

def spotify_play():
    """Resume playback on Spotify completely in the background using direct WM_COMMAND injection."""
    hwnd, title = _get_spotify_hwnd_and_title()
    if hwnd != 0:
        # If title is exactly "Spotify", it means music is currently paused or stopped.
        if title == "Spotify":
            import win32con
            # Try WM_APPCOMMAND (0x0319) with APPCOMMAND_MEDIA_PLAY_PAUSE (14)
            win32gui.SendMessage(hwnd, 0x0319, 0, 14 << 16)
            return "Spotify music playback initiated in the background, sir."
        else:
            return "Spotify is already playing active audio streams, sir."
            
    import win32api, win32con
    win32api.keybd_event(0xB3, 0, 0, 0)
    win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
    return "Spotify play toggle fired via virtual key layout fallback."

def spotify_pause():
    """Pause playback on Spotify completely in the background using direct WM_COMMAND injection."""
    hwnd, title = _get_spotify_hwnd_and_title()
    if hwnd != 0:
        # If title contains a hyphen, it means a track title is actively scaling the title bar.
        if " - " in title or title != "Spotify":
            import win32con
            win32gui.SendMessage(hwnd, 0x0319, 0, 14 << 16)
            return "Spotify music stream paused cleanly in the background, sir."
        else:
            return "Spotify is already in a paused state, sir."
            
    import win32api, win32con
    win32api.keybd_event(0xB3, 0, 0, 0)
    win32api.keybd_event(0xB3, 0, win32con.KEYEVENTF_KEYUP, 0)
    return "Spotify pause toggle fired via virtual key layout fallback."

def spotify_next():
    """Skip to the next track on Spotify entirely in the background."""
    hwnd, _ = _get_spotify_hwnd_and_title()
    if hwnd != 0:
        import win32con
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 115, 0)
        return "Skipping to next Spotify track in the background, sir."
    import keyboard
    keyboard.send("next track")
    return "Skipping track via global input stream fallback."

def spotify_previous():
    """Go to the real previous Spotify track by sending Previous twice."""
    import time

    try:
        import win32api
        import win32con

        VK_MEDIA_PREV_TRACK = 0xB1

        for _ in range(2):
            win32api.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
            win32api.keybd_event(VK_MEDIA_PREV_TRACK, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.35)

        return "Previous track."
    except Exception as e:
        return f"Spotify previous failed: {e}"

def _click_spotify_element(automation_id):
    """Helper function to find the minimized/background Spotify window and click an element."""
    try:
        # Connect to the running Spotify process
        app = Application(backend="uia").connect(title_re=".*Spotify.*")
        spotify_window = app.window(title_re=".*Spotify.*")
        
        # Locate the button using its accessible name/automation ID and click it
        button = spotify_window.child_window(title=automation_id, control_type="Button")
        button.click_input()
        return True
    except Exception:
        return False

def _send_spotify_command(command_id):
    """Finds Spotify, quickly wakes it up if minimized, sends command, and hides it back."""
    hwnd = win32gui.FindWindow("SpotifyMainWindow", None)
    if hwnd == 0:
        def enum_cb(hwnd_enum, extra):
            if "Spotify" in win32gui.GetWindowText(hwnd_enum):
                extra.append(hwnd_enum)
            return True
        hwnds = []
        win32gui.EnumWindows(enum_cb, hwnds)
        if hwnds:
            hwnd = hwnds[0]
            
    if hwnd != 0:
        # Check if Spotify is minimized
        is_minimized = win32gui.IsIconic(hwnd)
        
        if is_minimized:
            # Force restore it without stealing focus from VS Code
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.05)  # Tiny 50ms pause to let the window engine wake up
            
        # Send the media command directly
        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, command_id, 0)
        
        if is_minimized:
            # Instantly push it back down to the taskbar
            time.sleep(0.02)
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            
        return True
    return False


def _click_spotify_relative(x_ratio, y_ratio):
    """Finds Spotify window, calculates button position, clicks it background-style, and returns focus."""
    hwnd = win32gui.FindWindow("SpotifyMainWindow", None)
    if hwnd == 0:
        return False
        
    # Get the window size rect [left, top, right, bottom]
    rect = win32gui.GetWindowRect(hwnd)
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    
    # Calculate exact point based on window layout proportions
    click_x = int(width * x_ratio)
    click_y = int(height * y_ratio)
    
    # Pack coordinates into a single win32 long-parameter
    l_param = win32api.MAKELONG(click_x, click_y)
    
    # Send a background click signal sequence directly to the window kernel
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_param)
    time.sleep(0.01)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, l_param)
    return True



def _trigger_spotify_hotkey(keys_callback):
    """Finds the active Spotify window, brings it to focus safely, triggers key press, and hands focus back."""
    # Find current active window so we can return back to it
    original_hwnd = win32gui.GetForegroundWindow()
    
    hwnd = win32gui.FindWindow("SpotifyMainWindow", None)
    if hwnd != 0:
        # Force the window to become active for a split second
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.05) # Brief pause for focus change
        
        # Execute hotkey simulation
        keys_callback()
        time.sleep(0.05)
        
        # Instantly restore your previous window focus (VS Code / Jarvis)
        if original_hwnd != 0:
            win32gui.SetForegroundWindow(original_hwnd)
        return True
    return False



# Update this path to match exactly where you saved your desktop AHK script file!
# Looks for spotify_control.ahk right in your main Jarvis folder structure


def _force_wake_with_protocol(hotkey_func):
    """Uses the Windows URI protocol handler to force Spotify into focus legally, then fires keys."""
    original_hwnd = win32gui.GetForegroundWindow()
    
    # Force Windows to bring Spotify to the absolute front via its system URI handle
    os.system("start spotify:")
    time.sleep(0.15)  # Give the UI 150ms to jump to the front and accept keyboard focus
    
    # Execute the key combo now that it has absolute focus
    hotkey_func()
    time.sleep(0.05)
    
    # Instantly minimize it back down out of your way
    hwnd = win32gui.FindWindow("SpotifyMainWindow", None)
    if hwnd != 0:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        
    # Return focus back to your code workspace
    if original_hwnd != 0:
        try:
            win32gui.SetForegroundWindow(original_hwnd)
        except:
            pass
    return True

def spotify_like():
    """
    Brings Spotify into physical background context execution, determines layout profiles,
    and moves mouse directly to click the bottom player bar like '+' activation target.
    """
    import win32gui
    import win32con
    import win32process
    import win32api
    import pyautogui
    import time
    import os
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # Save tracking environments to minimize focus disruption
    original_hwnd = win32gui.GetForegroundWindow()
    orig_x, orig_y = pyautogui.position()

    # 🚀 Wake Spotify protocol context frame to enable input parsing
    os.system("start spotify:")
    time.sleep(0.4)

    # 1. Isolate the operational window handle
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
        # 2. Safety window focus restore invocation
        win32gui.ShowWindow(main_hwnd, win32con.SW_RESTORE)
        time.sleep(0.05)
        try:
            win32gui.SetForegroundWindow(main_hwnd)
        except Exception:
            rect = win32gui.GetWindowRect(main_hwnd)
            pyautogui.click(rect[0] + 10, rect[1] + 10)
        time.sleep(0.15)

        # 3. Calculate dimension vectors
        rect = win32gui.GetWindowRect(main_hwnd)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top

        # ── PLAYER BAR DUAL PROFILE RATIO SWITCH ──────────────────────────────
        placement = win32gui.GetWindowPlacement(main_hwnd)
        if placement[1] == win32con.SW_SHOWMAXIMIZED:
            # 🖥️ PASTE YOUR CALIBRATED MAXIMIZED RATIOS HERE
            x_ratio = 0.1539
            y_ratio = 0.9494
        else:
            # 📱 PASTE YOUR CALIBRATED SNAPPED RATIOS HERE
            x_ratio = 0.2880
            y_ratio = 0.9612  
        # ──────────────────────────────────────────────────────────────────────

        click_x = left + int(width * x_ratio)
        click_y = top + int(height * y_ratio)

        # 4. Trigger target click execution and recover mouse trajectory
        pyautogui.click(click_x, click_y)
        time.sleep(0.05)
        pyautogui.moveTo(orig_x, orig_y)

        # 5. Hand interface context cleanly back to user window pipeline
        if original_hwnd != 0:
            try:
                win32gui.SetForegroundWindow(original_hwnd)
            except Exception:
                pass

        return "Track updated, sir."

    return "Spotify window could not be verified."


# ─────────────────────────────────────────────────────────────────────────────
# ACTION MAP
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
    "spotify_play": spotify_play,
    "spotify_pause": spotify_pause,
    "spotify_next": spotify_next,
    "spotify_previous": spotify_previous,
    "spotify_search": spotify_play,
    "spotify_like": spotify_like,
    "spotify_search": spotify_search_and_play,
    "spotify_liked_songs": spotify_play_liked_songs,
    "spotify_new_releases": spotify_play_new_releases,  
    "spotify_made_for_you": spotify_play_made_for_you,
    "spotify_volume_up": spotify_volume_up,    
    "spotify_volume_down": spotify_volume_down,

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
}


# ─────────────────────────────────────────────────────────────────────────────
# AI INTENT DETECTION (fallback for natural language descriptions)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_action(description: str) -> dict:
    from google import genai
    genai.configure(api_key=get_api_key())
    model = genai.GenerativeModel("gemini-2.5-flash-lite")

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, type_text, press_key, reload_n"

    prompt = f"""You are an intent detector for a computer control assistant.

The user issued a command (possibly in any language): "{description}"

Available actions: {available}

Return ONLY a valid JSON object:
{{"action": "action_name", "value": null_or_value}}

Rules:
- Pick the single best matching action from the available list.
- For volume_set: value is an integer 0-100.
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer (number of times to reload).
- If no clear match, pick the closest action.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        resp = model.generate_content(prompt)
        text = re.sub(r"```(?:json)?", "", resp.text).strip().rstrip("`").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = str(params.get("action", "")).strip()
    description = str(params.get("description", "")).strip()
    value       = params.get("value", None)

    # ── Use AI intent detection for natural language descriptions ─────────────
    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    # ── Normalise action string ───────────────────────────────────────────────
    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")
    # Phase-1 routing aliases
    if action == "volume" and value is not None:
        action = "volume_set"
    elif action in ("toggle_mute", "volume_mute"):
        action = "mute"

    # ── Parse value robustly (handles "50%", "50 %", "half", 50, etc.) ────────
    parsed_int = _parse_volume_value(value)

    print(f"[Settings] Action: {action!r} | Raw value: {value!r} | Parsed int: {parsed_int} | OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    # ── Safety gate for destructive actions ──────────────────────────────────
    DANGEROUS_ACTIONS = ["shutdown", "restart", "logoff"]
    if action in DANGEROUS_ACTIONS:
        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ["yes", "true", "1", "confirm"]:
            return (
                f"This will {action} the computer. "
                "Please confirm by calling again with confirmed=yes."
            )

    # ── VOLUME SET (exact percentage) ─────────────────────────────────────────
    # Matches: "volume_set", "volume" with a numeric value, "set_volume"
    if action in ("volume_set", "set_volume") or (
        action in ("volume",) and parsed_int is not None
    ):
        vol = parsed_int if parsed_int is not None else 50
        return volume_set(vol)

    # ── VOLUME MUTE ────────────────────────────────────────────────────────────
    if action in ("volume_mute", "mute", "unmute", "toggle_mute"):
        volume_mute()
        return "Volume mute state toggled."

    # ── VOLUME UP / DOWN ──────────────────────────────────────────────────────
    if action == "volume_up":
        volume_up()
        return "Volume increased."

    if action == "volume_down":
        volume_down()
        return "Volume decreased."

    # ── TYPE TEXT ─────────────────────────────────────────────────────────────
    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    # ── PRESS KEY ─────────────────────────────────────────────────────────────
    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    # ── RELOAD N TIMES ───────────────────────────────────────────────────────
    if action in ("reload_n", "refresh_n", "reload_page_n"):
        n = parsed_int if parsed_int is not None else 1
        reload_page_n(n)
        return f"Reloaded {n} time(s)."

    # ── SCROLL WITH AMOUNT ───────────────────────────────────────────────────
    if action == "scroll_up":
        amount = parsed_int if parsed_int is not None else 500
        scroll_up(amount)
        return "Scrolled up."

    if action == "scroll_down":
        amount = parsed_int if parsed_int is not None else 500
        scroll_down(amount)
        return "Scrolled down."

    # ── SPOTIFY LIKED SONGS ROUTING ───────────────────────────────────────────
    if action == "spotify_liked_songs":
        return spotify_play_liked_songs()
    
    # ── SPOTIFY NEW RELEASES ROUTING ──────────────────────────────────────────
    if action == "spotify_new_releases":
        return spotify_play_new_releases()

    # ── SPOTIFY MADE FOR YOU ROUTING ──────────────────────────────────────────
    if action == "spotify_made_for_you":
        return spotify_play_made_for_you()
     
    # ── SPOTIFY SEARCH & PLAY ROUTING ─────────────────────────────────────────
    if action == "spotify_search":
        song_query = params.get("value") or params.get("description")
        return spotify_search_and_play(song_query)
    
    # ── SPOTIFY INTERNAL VOLUME ROUTING ──────────────────────────────────────
    if action == "spotify_volume_up":
        return spotify_volume_up()

    if action == "spotify_volume_down":
        return spotify_volume_down()

    # ── GENERIC ACTION MAP ───────────────────────────────────────────────────
    func = ACTION_MAP.get(action)
    if not func:
        return f"Unknown action: '{raw_action}'."

    try:
        func()
        return f"Done: {action}."
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"