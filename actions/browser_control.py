# actions/browser_control.py
"""
JARVIS Browser Controller — Playwright-based, tab-aware, Brave-first.

Key capabilities:
  • Connects directly to your manual live browser via Remote Debugging CDP
  • Smart Tab Reuse: Recycles open media tabs to prevent duplicate window tabs
  • Direct Thumbnail Player: Clicks show thumbnails directly to bypass player modals
  • TV-Exclusive App Casting: Streams directly to 'Salle TV' while auto-playing backends
  • Single registry singleton across the whole JARVIS process
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)

from core.utils import get_os, log
from core.intent_memory import remember_action

_OS = get_os()   # "windows" | "darwin" | "linux"


def _normalize_url(url: str) -> str:
    """'youtube' → 'https://youtube.com', 'google.com' → 'https://google.com'"""
    url = url.strip()
    if not url:
        return "about:blank"
    if "://" in url:
        return url
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")
    for p in [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]:
        if p.exists():
            return str(p)
    try:
        import winreg
        for key in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
        ]:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    pass
    except Exception:
        pass
    return shutil.which("opera")


def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        for key in [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    pass
    except Exception:
        pass
    return shutil.which(prog_name)


_BROWSER_SPECS: dict[str, dict] = {
    "windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
    },
    "darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,      "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi-stable", "vivaldi"]},
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name    = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map  = _BROWSER_SPECS.get(_OS, {})
    spec    = os_map.get(name)
    if spec is None:
        return None

    engine, channel = spec["engine"], spec.get("channel")
    bins            = spec.get("bins", [])
    exe             = None

    if spec.get("special") == "opera_windows":
        return {"engine": engine, "exe": _find_opera_windows(), "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "windows":
            import winreg
            k       = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure", "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


class _BrowserSession:
    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread           | None = None
        self._ready    = threading.Event()

        self._pw:          Playwright | None = None
        self._browser_obj: any = None
        self._context:     BrowserContext | None = None

        self._pages:  list[Page] = []   
        self._active: int        = 0    

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop:
            raise RuntimeError(f"Session '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser_obj:
            try:
                await self._browser_obj.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = None
        self._browser_obj = None
        self._pages   = []
        self._active  = 0

    async def _launch(self):
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(f"'{self.browser_name}' is not supported on {_OS}.")

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "chromium":
            try:
                self._browser_obj = await self._pw.chromium.connect_over_cdp("http://localhost:9222")
                self._context = self._browser_obj.contexts[0]
                self._pages = list(self._context.pages)
                self._active = 0
                
                self._context.on("page", self._on_new_page)
                for p in self._pages:
                    p.on("close", self._on_page_close)
                
                log("Browser", "🔗 CDP Link active on port 9222.")
                return
            except Exception:
                log("Browser", "⚠️ Port 9222 window missing. Initializing fallback automated sandbox profile...")

        isolated_profile = str(Path.home() / ".jarvis_profiles" / self.browser_name)
        Path(isolated_profile).mkdir(parents=True, exist_ok=True)

        base_kwargs: dict = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
        }

        if engine_name == "firefox":
            if exe:
                base_kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(isolated_profile, **base_kwargs)
            except Exception:
                fb = str(Path.home() / ".jarvis_profiles" / "firefox_fallback")
                Path(fb).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(fb, **base_kwargs)
        elif engine_name == "webkit":
            self._context = await engine_obj.launch_persistent_context(isolated_profile, **base_kwargs)
        else:
            base_kwargs["args"] = [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ]
            if exe:
                base_kwargs["executable_path"] = exe
            elif channel:
                base_kwargs["channel"] = channel
            try:
                self._context = await engine_obj.launch_persistent_context(isolated_profile, **base_kwargs)
            except Exception:
                fb = str(Path.home() / ".jarvis_profiles" / f"{self.browser_name}_fallback")
                Path(fb).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(fb, **base_kwargs)

        await asyncio.sleep(0.5)
        self._context.on("page", self._on_new_page)
        existing = self._context.pages
        if existing:
            self._pages  = list(existing)
            self._active = 0
            for p in self._pages:
                p.on("close", self._on_page_close)
        else:
            first = await self._context.new_page()
            self._pages  = [first]
            self._active = 0

    def _on_new_page(self, page: Page):
        if page not in self._pages:
            self._pages.append(page)
            page.on("close", self._on_page_close)

    def _on_page_close(self, page: Page):
        if page in self._pages:
            self._pages.remove(page)
            if self._active >= len(self._pages) and self._pages:
                self._active = len(self._pages) - 1
            elif not self._pages:
                self._active = 0

    async def _current_page(self) -> Page:
        await self._launch()
        self._pages = [p for p in self._pages if not p.is_closed()]

        if self._context:
            for p in self._context.pages:
                if p not in self._pages:
                    self._pages.append(p)
                    p.on("close", self._on_page_close)

        if not self._pages:
            blank = await self._context.new_page()
            self._pages  = [blank]
            self._active = 0
            return blank

        if self._active >= len(self._pages):
            self._active = len(self._pages) - 1

        return self._pages[self._active]

    # ─────────────────────────────────────────────────────────────────────────
    # TWO-STEP AUTO-PLAY ENGINE (ROBUST MATCHING FIX)
    # ─────────────────────────────────────────────────────────────────────────
    async def netflix_search_and_play(self, title: str) -> str:
        """
        Navigates to the search grid and clicks the closest matching media thumbnail.
        If an exact name match isn't found, it hits the first available result card.
        """
        await self._launch()
        self._pages = [p for p in self._pages if not p.is_closed()]
        
        if self._context:
            for p in self._context.pages:
                if p not in self._pages:
                    self._pages.append(p)
                    p.on("close", self._on_page_close)

        page = await self._current_page()
        for i, p in enumerate(self._pages):
            if "netflix.com" in p.url.lower():
                self._active = i
                page = p
                await page.bring_to_front()
                break

        # Strip numbers from title for cleaner fallbacks (e.g. "Gladiator 2" -> "Gladiator")
        clean_title = ''.join([i for i in title if not i.isdigit()]).strip()

        search_url = f"https://www.netflix.com/search?q={title.replace(' ', '%20')}"
        try:
            log("Browser", f"Navigating directly to Netflix matrix: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
            
            await asyncio.sleep(2.5)
            
            thumbnail_selectors = [
                f'a[aria-label*="{title}" i]',             
                f'a:has(img[alt*="{title}" i])',           
                f'a[aria-label*="{clean_title}" i]',       # Fallback to base name match (fixes Gladiator issue)
                f'a:has(img[alt*="{clean_title}" i])',     
                '[data-uia="title-card"] a',               # Native Netflix card selector
                '.title-card a',                           
                '.slider-item a'                           
            ]
            
            thumbnail_clicked = False
            for selector in thumbnail_selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0:
                        await locator.click(timeout=4000)
                        thumbnail_clicked = True
                        break
                except Exception:
                    continue

            if not thumbnail_clicked:
                return f"Could not resolve a visible thumbnail link locator for '{title}'."

            await asyncio.sleep(1.5)
            
            if "watch" in page.url.lower():
                return f"Direct player stream initiated for '{title}'."

            # Modal Handling Override (Clicks 'Play' or 'Ép. suivant' / 'Reprendre')
            modal_play_selectors = [
                '[data-uia="play-button"]',                
                'a.playLink',                              
                'button.playLink',                          
                'a:has-text("Ép. suivant")',               
                'button:has-text("Ép. suivant")',          
                'a:has-text("Reprendre")',                 
                'button:has-text("Reprendre")',            
                '[aria-label="Play"]',                     
                '[aria-label="Lecture"]'                   
            ]

            for modal_selector in modal_play_selectors:
                try:
                    modal_locator = page.locator(modal_selector).first
                    if await modal_locator.count() > 0 and await modal_locator.is_visible():
                        await modal_locator.click(timeout=3000)
                        return f"Modal anchor bypassed. Streaming initialized for '{title}'."
                except Exception:
                    continue

            return f"Thumbnail clicked, but secondary modal play button could not be resolved programmatically."
        except Exception as e:
            return f"Netflix dynamic pipeline error: {e}"

# ─────────────────────────────────────────────────────────────────────────
    # ADVANCED CHROMIUM NATIVE CDP CAST ENGINE (100% BACKGROUND COMPATIBLE)
    # ─────────────────────────────────────────────────────────────────────────
    async def cast_screen(self) -> str:
        """
        Establishes a direct Chrome DevTools Protocol (CDP) session to force 
        Brave's engine to stream directly to 'Salle TV'. Operates completely 
        in the background without window focus or OS hardware macros.
        """
        page = await self._current_page()
        
        try:
            log("Browser", "Initializing core Chromium DevTools Protocol Cast layer...")
            
            # 1. Establish a direct background connection to the browser context engine
            cdp = await page.context.new_cdp_session(page)
            
            # 2. Wake up the native wireless discovery service inside Chromium
            await cdp.send("Cast.enable")
            
            # 3. Intercept browser sink queries and force auto-selection of 'Salle TV'
            # This completely bypasses the physical rendering of the top-right pop-up
            await cdp.send("Cast.setSinkToUse", {"sinkName": "Salle TV"})
            
            # 4. Programmatically initialize tab mirroring directly to the device
            await cdp.send("Cast.startTabMirroring", {"sinkName": "Salle TV"})
            
            # 5. Background DOM interaction: Click the internal Netflix HTML5 player cast icon
            # This ensures the stream registers as an application link rather than flat mirroring
            netflix_cast_button = page.locator('button[data-uia="control-cast"], button[aria-label*="ast" i]').first
            if await netflix_cast_button.count() > 0:
                log("Browser", "Activating inline video player cast node inside Netflix container DOM...")
                await netflix_cast_button.click(timeout=2000)
                
            return "Casting session established natively via CDP to 'Salle TV', sir."
            
        except Exception as e:
            log("Browser", f"CDP Cast domain exception: {e}. Executing fallback stream matrix...")
            try:
                # Direct stream routing fallback if the presentation layer intercepts the click command
                await cdp.send("Cast.startTabMirroring", {"sinkName": "Salle TV"})
                return "Tab mirrored directly to 'Salle TV' via backend fallback sequence, sir."
            except Exception as ex:
                return f"Native casting domain pipeline failure: {ex}"    # ─────────────────────────────────────────────────────────────────────────
    # NATIVE CDP STOP CASTING DISCONNECT PIPELINE
    # ─────────────────────────────────────────────────────────────────────────
    async def stop_casting(self) -> str:
        """
        Directly commands the Chromium core process to disconnect the wireless
        session running on 'Salle TV' completely in the background.
        """
        page = await self._current_page()
        try:
            log("Browser", "Sending teardown signal to active Chromium Cast session...")
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Cast.enable")
            await cdp.send("Cast.stopCasting", {"sinkName": "Salle TV"})
            return "Casting session terminated safely, sir."
        except Exception as e:
            return f"Failed to gracefully close the casting pipeline: {e}"

    # ─────────────────────────────────────────────────────────────────────────
    # CORE INTERACTION ROUTERS
    # ─────────────────────────────────────────────────────────────────────────

    async def list_tabs(self) -> str:
        await self._launch()
        self._pages = [p for p in self._pages if not p.is_closed()]
        if not self._pages:
            return "No open tabs."
        lines = []
        for i, page in enumerate(self._pages):
            url   = page.url or "about:blank"
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            active_marker = " ◀ active" if i == self._active else ""
            display       = title if title else url[:70]
            lines.append(f"  [{i}] {display}{active_marker}")
        return f"Tabs ({len(self._pages)}):\n" + "\n".join(lines)

    async def switch_tab(self, index: int) -> str:
        self._pages = [p for p in self._pages if not p.is_closed()]
        if not self._pages:
            return "No open tabs."
        if index < 0 or index >= len(self._pages):
            return f"Index {index} is out of range (0–{len(self._pages)-1})."
        self._active = index
        page         = self._pages[self._active]
        try:
            await page.bring_to_front()
        except Exception:
            pass
        title = ""
        try:
            title = await page.title()
        except Exception:
            pass
        return f"Switched to tab [{self._active}]: {title or page.url}"

    async def focus_tab_by_url(self, fragment: str) -> str:
        frag_lc = fragment.lower()
        self._pages = [p for p in self._pages if not p.is_closed()]
        for i, page in enumerate(self._pages):
            if frag_lc in page.url.lower():
                return await self.switch_tab(i)
            try:
                t = await page.title()
                if frag_lc in t.lower():
                    return await self.switch_tab(i)
            except Exception:
                pass
        return f"No tab found containing '{fragment}'."

    async def get_active_tab_info(self) -> str:
        page  = await self._current_page()
        url   = page.url
        title = ""
        try:
            title = await page.title()
        except Exception:
            pass
        return f"Active tab [{self._active}] — {title or url}"

    async def new_tab(self, url: str = "") -> str:
        """Tab initialization wrapper with built-in smart Netflix redirect routing hooks."""
        await self._launch()
        if "netflix.com/search" in url.lower() or "netflix.com" in url.lower():
            extracted_title = url.split("?q=")[-1].replace("%20", " ") if "?q=" in url else "Netflix"
            return await self.netflix_search_and_play(extracted_title)
            
        page = await self._context.new_page()
        if page not in self._pages:
            self._pages.append(page)
            page.on("close", self._on_page_close)
        self._active = self._pages.index(page)
        if url:
            return await self.go_to(url)
        return f"New tab opened at [{self._active}]."

    async def close_tab(self) -> str:
        self._pages = [p for p in self._pages if not p.is_closed()]
        if not self._pages:
            return "No open tabs."
        page = self._pages[self._active]
        await page.close()
        return f"Tab closed. Active tab is now [{self._active}]."

    async def next_tab(self) -> str:
        self._pages = [p for p in self._pages if not p.is_closed()]
        if len(self._pages) <= 1:
            return "Only one tab open."
        self._active = (self._active + 1) % len(self._pages)
        return await self.switch_tab(self._active)

    async def prev_tab(self) -> str:
        self._pages = [p for p in self._pages if not p.is_closed()]
        if len(self._pages) <= 1:
            return "Only one tab open."
        self._active = (self._active - 1) % len(self._pages)
        return await self.switch_tab(self._active)

    async def go_to(self, url: str) -> str:
        if "netflix.com/search" in url.lower():
            extracted_title = url.split("?q=")[-1].replace("%20", " ")
            return await self.netflix_search_and_play(extracted_title)
            
        url  = _normalize_url(url)
        page = await self._current_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(0.3)
        except PlaywrightTimeout:
            pass
        except Exception as e:
            return f"Navigation error: {e}"
        return f"Navigated to: {page.url}"

    async def search(self, query: str, engine: str = "google") -> str:
        _engines = {
            "google":     "https://www.google.com/search?q=",
            "bing":       "https://www.bing.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
            "yandex":     "https://yandex.com/search/?text=",
        }
        base = _engines.get(engine.lower(), _engines["google"])
        return await self.go_to(base + query.replace(" ", "+"))

    async def reload(self) -> str:
        page = await self._current_page()
        try:
            await page.reload(timeout=15_000)
            return f"Reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    async def back(self) -> str:
        page = await self._current_page()
        try:
            await page.go_back(timeout=10_000)
            return f"Back → {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._current_page()
        try:
            await page.go_forward(timeout=10_000)
            return f"Forward → {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._current_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8_000)
                return f"Clicked text: '{text}'"
            if selector:
                await page.click(selector, timeout=8_000)
                return f"Clicked: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found (timeout)."
        except Exception as e:
            return f"Click error: {e}"

    async def smart_click(self, description: str) -> str:
        page = await self._current_page()
        for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=description)
                if await loc.count() > 0:
                    await loc.first.click(timeout=5_000)
                    return f"Clicked ({role}): '{description}'"
            except Exception:
                pass
        return f"Could not find element: '{description}'"

    async def type_text(self, selector: str = None, text: str = "", clear_first: bool = True) -> str:
        page = await self._current_page()
        try:
            el = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await el.clear()
            await el.type(text, delay=50)
            return f"Typed into {selector or 'focused element'}."
        except Exception as e:
            return f"Type error: {e}"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._current_page()
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox", name=description)),
        ]
        for method, loc in candidates:
            try:
                el = loc.first
                if await el.count() == 0:
                    continue
                await el.clear()
                await el.type(text, delay=50)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue
        return f"Could not find input: '{description}'"

    async def fill_form(self, fields: dict) -> str:
        page    = await self._current_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form: " + ", ".join(results)

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._current_page()
        try:
            if direction in ("down", "up"):
                y = amount if direction == "down" else -amount
                await page.mouse.wheel(0, y)
            else:
                x = amount if direction == "right" else -amount
                await page.mouse.wheel(x, 0)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def press(self, key: str) -> str:
        page = await self._current_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._current_page()
        try:
            return (await page.inner_text("body"))[:4_000]
        except Exception as e:
            return f"Could not get text: {e}"

    async def get_url(self) -> str:
        page = await self._current_page()
        return page.url

    async def screenshot(self, path: str = None) -> str:
        page      = await self._current_page()
        save_path = path or str(Path.home() / "Desktop" / "jarvis_screenshot.png")
        try:
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"
        
# ─────────────────────────────────────────────────────────────────────────
    # BACKGROUND HTML5 DOM MEDIA EVALUATION ENGINE (UPDATED FIX)
    # ─────────────────────────────────────────────────────────────────────────
    async def evaluate_background_media(self, domain_keyword: str, js_script: str) -> str:
        """
        Scans all open pages in the active browser context tree via CDP hooks.
        Injects native JS control parameters into target domains without shifting focus.
        """
        await self._launch()
        self._pages = [p for p in self._pages if not p.is_closed()]
        
        if self._context:
            for p in self._context.pages:
                if p not in self._pages:
                    self._pages.append(p)
                    p.on("close", self._on_page_close)

        # Scan for target web domain keyword markers
        target_page = None
        for page in self._pages:
            if domain_keyword.lower() in page.url.lower():
                target_page = page
                break
                
        if not target_page:
            return f"Target browser tab matching '{domain_keyword}' was not found."
            
        try:
            # FIX: Wrap the script block inside an Immediately Invoked Function Expression (IIFE)
            # This safely handles statement containment and validates top-level returns cleanly
            iife_script = f"(() => {{ {js_script} }})()"
            execution_result = await target_page.evaluate(iife_script)
            return str(execution_result)
        except Exception as e:
            return f"DOM Media Injection Failure: {e}"

# ─────────────────────────────────────────────────────────────────────────────
# MODULE REGISTRY INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class _SessionRegistry:
    def __init__(self):
        self._sessions:       dict[str, _BrowserSession] = {}
        self._active_browser: str                        = ""
        self._lock            = threading.Lock()

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name         = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess                 = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name         = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed."

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                mark = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{mark}")
        return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

def get_browser_session(browser_name: str | None = None) -> _BrowserSession:
    return _registry.get(browser_name)


def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    if action == "switch_browser":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Specify a browser name."
        log("Browser", result, player)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        log("Browser", result, player)
        return result

    if action == "close_all":
        result = _registry.close_all()
        log("Browser", result, player)
        return result

    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        log("Browser", result, player)
        return result

    try:
        match action:
            case "list_tabs":
                result = sess.run(sess.list_tabs())
            case "active_tab":
                result = sess.run(sess.get_active_tab_info())
            case "switch_tab":
                result = sess.run(sess.switch_tab(int(params.get("index", 0))))
            case "focus_tab":
                fragment = params.get("url") or params.get("fragment", "")
                result   = sess.run(sess.focus_tab_by_url(fragment))
            case "next_tab":
                result = sess.run(sess.next_tab())
            case "prev_tab":
                result = sess.run(sess.prev_tab())
            case "new_tab":
                result = sess.run(sess.new_tab(params.get("url", "")))
            case "netflix_search_and_play":
                result = sess.run(sess.netflix_search_and_play(params.get("url", "")))
            case "close_tab":
                result = sess.run(sess.close_tab())
            case "go_to":
                result = sess.run(sess.go_to(params.get("url", "")))
            case "search":
                result = sess.run(sess.search(params.get("query", ""), params.get("engine", "google")))
            case "reload":
                result = sess.run(sess.reload())
            case "back":
                result = sess.run(sess.back())
            case "forward":
                result = sess.run(sess.forward())
            case "click":
                result = sess.run(sess.click(params.get("selector"), params.get("text")))
            case "smart_click":
                result = sess.run(sess.smart_click(params.get("description", "")))
            case "type":
                result = sess.run(sess.type_text(params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
            case "smart_type":
                result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
            case "fill_form":
                result = sess.run(sess.fill_form(params.get("fields", {})))
            case "scroll":
                result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
            case "bg_media_eval":
                result = sess.run(sess.evaluate_background_media(params.get("domain", ""), params.get("script", "")))
            case "press":
                result = sess.run(sess.press(params.get("key", "Enter")))
            case "get_text":
                result = sess.run(sess.get_text())
            case "get_url":
                result = sess.run(sess.get_url())
            case "screenshot":
                result = sess.run(sess.screenshot(params.get("path")))
            case "cast_screen" | "move_to_screen" | "cast_video":
                result = sess.run(sess.cast_screen())
            case "stop_casting" | "disconnect_tv":
                result = sess.run(sess.stop_casting())
            case "close" | "close_browser":
                target = browser or _registry._active_browser
                result = _registry.close_one(target) if target else "No browser specified."
            case _:
                result = f"Unknown browser action: '{action}'"
    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (60 s)."
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    log("Browser", result, player)
    return result