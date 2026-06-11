# actions/youtube_video.py
"""
JARVIS YouTube Controller — tab-aware, browser-integrated.

All playback uses the active Playwright browser tab (Brave by default).
No subprocess.Popen / OS URL opens — videos always play in the same tab
JARVIS already has open, just like a real user would navigate it.
"""

from __future__ import annotations

import json
import re
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus
from typing import Optional

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:
    _TRANSCRIPT_OK = False

from core.utils import get_api_key, get_base_dir, get_os, is_windows, is_mac, is_linux, log
from core.intent_memory import remember_action

# ── Import browser session bridge ────────────────────────────────────────────
# This is how YouTube navigates IN the existing Playwright tab instead of
# opening a new OS window.
try:
    from actions.browser_control import get_browser_session
    _BROWSER_BRIDGE = True
except ImportError:
    _BROWSER_BRIDGE = False

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

# YouTube scraping constants
_YT_VIDEO_FILTER = "EgIQAQ%3D%3D"   # filter: videos only (no Shorts, no playlists)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Default browser for YouTube playback (override via parameters: browser=<name>)
_DEFAULT_BROWSER = "brave"


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_first_video_url(query: str) -> Optional[str]:
    """
    Scrape YouTube search results and return the URL of the first
    non-Shorts video. Returns None if requests is unavailable or fails.
    """
    if not _REQUESTS_OK:
        return None
    search_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}&sp={_YT_VIDEO_FILTER}"
    )
    try:
        r    = requests.get(search_url, headers=_HEADERS, timeout=10)
        html = r.text
        seen: set[str] = set()
        for vid in re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html):
            if vid in seen:
                continue
            seen.add(vid)
            if f"/shorts/{vid}" in html:
                continue
            return f"https://www.youtube.com/watch?v={vid}"
    except Exception as e:
        print(f"[YouTube] ⚠️ Scrape failed: {e}")
    return None


def _extract_video_id(url: str) -> Optional[str]:
    match = re.search(
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url
    )
    return match.group(1) if match else None


def _is_valid_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url or ""))


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> Optional[str]:
    """Popup dialog asking the user to paste a URL."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk._default_root or tk.Tk()
        root.withdraw()
        url = simpledialog.askstring("J.A.R.V.I.S", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as e:
        print(f"[YouTube] ⚠️ UI Dialog failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER NAVIGATION  (in-tab, no OS window)
# ─────────────────────────────────────────────────────────────────────────────

def _navigate_in_browser(url: str, browser_name: str = _DEFAULT_BROWSER) -> str:
    """
    Navigate the active Playwright tab to *url*.

    Priority:
      1. Use the browser_control bridge (Playwright, in-tab navigation).
      2. Fall back to OS open only when Playwright is unavailable.
    """
    if _BROWSER_BRIDGE:
        try:
            sess   = get_browser_session(browser_name)
            result = sess.run(sess.go_to(url))
            print(f"[YouTube] 🎬 In-tab navigation → {url}")
            return result
        except Exception as e:
            print(f"[YouTube] ⚠️ Browser bridge failed ({e}), falling back to OS open")

    # OS fallback (last resort — bypasses tab awareness)
    _os_open_url(url)
    return f"Opened (OS): {url}"


def _os_open_url(url: str) -> None:
    """Open a URL via the OS (fallback only, loses tab tracking)."""
    try:
        if is_mac():
            subprocess.Popen(["open", url])
        elif is_linux():
            subprocess.Popen(["xdg-open", url])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception as e:
        print(f"[YouTube] ⚠️ OS open_url failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PLAYBACK CONTROL  (Win32 media keys → background browser)
# ─────────────────────────────────────────────────────────────────────────────

# actions/youtube_video.py (Playback control section update)
# actions/youtube_video.py (Playback control section update)
from actions.browser_control import get_browser_session

def _handle_playback_control(action: str) -> str:
    try:
        remember_action(tool="youtube_video", action=f"youtube_{action}", app="browser", media_app="youtube", browser="brave")
    except Exception:
        pass
    """
    Targets open YouTube tabs in the background using JavaScript evaluation.
    Bypasses focus requirements and title-string tracking limitations.
    """
    print(f"[YouTube] Intercepting play/pause instruction for background execution: {action}")
    
    js_scripts = {
        "pause": (
            "const v = document.querySelector('video'); "
            "if(v) { v.pause(); return 'YouTube video paused cleanly'; } "
            "return 'No HTML5 video element found on YouTube page';"
        ),
        "resume": (
            "const v = document.querySelector('video'); "
            "if(v) { v.play(); return 'YouTube video resumed cleanly'; } "
            "return 'No HTML5 video element found on YouTube page';"
        ),
        "play_pause": (
            "const v = document.querySelector('video'); "
            "if(v) { if(v.paused) { v.play(); return 'YouTube video resumed'; } else { v.pause(); return 'YouTube video paused'; } } "
            "return 'No HTML5 video target discovered';"
        )
    }

    script = js_scripts.get(action if action in js_scripts else "play_pause")
    
    try:
        sess = get_browser_session("brave")
        # Run script invocation concurrently across background threads
        result = sess.run(sess.evaluate_background_media("youtube.com", script))
        print(f"[YouTube] Native CDP Pipeline Response: {result}")
        return result
    except Exception as e:
        return f"YouTube background processing domain exception: {e}"    
# ─────────────────────────────────────────────────────────────────────────────
# TRANSCRIPT + SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

_LANG_PRIORITY = ["en", "tr", "de", "fr", "es", "it", "pt", "ru", "ja", "ko", "ar", "zh"]


def _get_transcript(video_id: str) -> Optional[str]:
    if not _TRANSCRIPT_OK:
        return None
    try:
        tlist = YouTubeTranscriptApi.list_transcripts(video_id)

        transcript = None
        # Prefer manually created transcripts (higher quality)
        try:
            transcript = tlist.find_manually_created_transcript(_LANG_PRIORITY)
        except Exception:
            pass

        if transcript is None:
            try:
                transcript = tlist.find_generated_transcript(_LANG_PRIORITY)
            except Exception:
                for t in tlist:
                    transcript = t
                    break

        if transcript is None:
            return None

        return " ".join(entry["text"] for entry in transcript.fetch())

    except Exception as e:
        print(f"[YouTube] ⚠️ Transcript retrieval failed: {e}")
        return None


def _summarize_with_gemini(transcript: str, video_url: str) -> str:
    from google import genai
    genai.configure(api_key=get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=(
            "You are JARVIS, an AI assistant. "
            "Summarize YouTube video transcripts clearly and concisely. "
            "Structure: 1-sentence overview, then 3-5 key bullet points. "
            "Be direct. Address the user as 'sir'. "
            "Match the language of the transcript."
        ),
    )
    max_chars  = 80_000
    truncated  = transcript[:max_chars] + ("…" if len(transcript) > max_chars else "")
    response   = model.generate_content(
        f"Summarize this YouTube video transcript:\n\n{truncated}"
    )
    return response.text.strip()


def _save_summary(content: str, video_url: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    desktop  = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / f"youtube_summary_{ts}.txt"

    header = (
        f"JARVIS — YouTube Summary\n"
        f"{'─' * 50}\n"
        f"URL    : {video_url}\n"
        f"Date   : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'─' * 50}\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")

    if is_windows():
        subprocess.Popen(["notepad.exe", str(filepath)])
    elif is_mac():
        subprocess.Popen(["open", "-t", str(filepath)])
    else:
        subprocess.Popen(["xdg-open", str(filepath)])

    return str(filepath)


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO INFO + TRENDING SCRAPERS
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_video_info(video_id: str) -> dict:
    if not _REQUESTS_OK:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r    = requests.get(url, headers=_HEADERS, timeout=12)
        html = r.text
        info: dict = {}
        for key, pattern in [
            ("title",    r'"title":\{"runs":\[\{"text":"([^"]+)"'),
            ("channel",  r'"ownerChannelName":"([^"]+)"'),
            ("views",    r'"viewCount":"(\d+)"'),
            ("duration", r'"lengthSeconds":"(\d+)"'),
            ("likes",    r'"label":"([0-9,]+ likes)"'),
        ]:
            m = re.search(pattern, html)
            if not m:
                continue
            raw = m.group(1)
            if key == "views":
                info[key] = f"{int(raw):,}"
            elif key == "duration":
                s = int(raw)
                info[key] = f"{s // 60}:{s % 60:02d}"
            else:
                info[key] = raw
        return info
    except Exception as e:
        print(f"[YouTube] ⚠️ Info scrape failed: {e}")
        return {}


def _scrape_trending(region: str = "US", max_results: int = 8) -> list[dict]:
    if not _REQUESTS_OK:
        return []
    url = f"https://www.youtube.com/feed/trending?gl={region.upper()}"
    try:
        r       = requests.get(url, headers=_HEADERS, timeout=12)
        html    = r.text
        titles  = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', html)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)
        results: list[dict] = []
        seen:    set[str]   = set()
        for i, title in enumerate(titles):
            if title in seen or len(title) < 5:
                continue
            seen.add(title)
            channel = channels[i] if i < len(channels) else "Unknown"
            results.append({"rank": len(results) + 1, "title": title, "channel": channel})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print(f"[YouTube] ⚠️ Trending scrape failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# ACTION HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _handle_play(params: dict, player, browser_name: str) -> str:
    """
    Search YouTube and navigate the current Brave/browser tab to the video.
    Never spawns a new OS window.
    """
    query = params.get("query", "").strip()
    if not query:
        return "Please tell me what you'd like to watch, sir."

    if player:
        player.write_log(f"[YouTube] Searching: {query}")

    print(f"[YouTube] 🔍 Searching for: {query}")

    video_url = _scrape_first_video_url(query)
    target    = video_url or (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}&sp={_YT_VIDEO_FILTER}"
    )
    result = _navigate_in_browser(target, browser_name)
    action_desc = f"Playing: {query}" if video_url else f"Opened YouTube search for: {query}"
    print(f"[YouTube] {action_desc}")
    return action_desc


def _handle_summarize(params: dict, player, speak, browser_name: str) -> str:
    if not _TRANSCRIPT_OK:
        return "youtube-transcript-api not installed. Run: pip install youtube-transcript-api"

    # Try to get URL from the active browser tab first
    url = params.get("url", "").strip()
    if not url and _BROWSER_BRIDGE:
        try:
            sess     = get_browser_session(browser_name)
            live_url = sess.run(sess.get_url())
            if _is_valid_youtube_url(live_url):
                url = live_url
                print(f"[YouTube] 📋 Using active tab URL: {url}")
        except Exception:
            pass

    # Fall back to popup dialog
    if not url:
        url = _ask_for_url("Paste the YouTube video URL:")

    if not url or not _is_valid_youtube_url(url):
        return "A valid YouTube URL is required, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID from that URL, sir."

    if player:
        player.write_log(f"[YouTube] Summarizing: {url}")
    if speak:
        speak("Extracting transcript now, sir. One moment.")

    transcript = _get_transcript(video_id)
    if not transcript:
        return "No transcript was available for this video, sir."

    if speak:
        speak("Transcript captured. Running summarization.")

    try:
        summary = _summarize_with_gemini(transcript, url)
    except Exception as e:
        return f"Summarization failed, sir: {e}"

    if speak:
        speak(summary)

    if params.get("save", False):
        saved_path = _save_summary(summary, url)
        return f"Summary saved to Desktop: {saved_path}"

    return summary


def _handle_get_info(params: dict, player, speak, browser_name: str) -> str:
    url = params.get("url", "").strip()

    # Try active tab URL if none provided
    if not url and _BROWSER_BRIDGE:
        try:
            sess     = get_browser_session(browser_name)
            live_url = sess.run(sess.get_url())
            if _is_valid_youtube_url(live_url):
                url = live_url
        except Exception:
            pass

    if not url:
        url = _ask_for_url("Paste the YouTube video URL:")

    if not url or not _is_valid_youtube_url(url):
        return "Please provide a valid YouTube URL, sir."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID from that URL, sir."

    if player:
        player.write_log(f"[YouTube] Fetching info: {url}")

    info = _scrape_video_info(video_id)
    if not info:
        return "Could not retrieve video info, sir."

    lines  = [
        f"{k.capitalize()}: {info[k]}"
        for k in ("title", "channel", "views", "duration", "likes")
        if k in info
    ]
    result = "\n".join(lines)

    if speak:
        speak(f"Video info, sir. {result.replace(chr(10), '. ')}")

    return result


def _handle_trending(params: dict, player, speak) -> str:
    region = params.get("region", "US").upper()
    if player:
        player.write_log(f"[YouTube] Trending: {region}")

    trending = _scrape_trending(region=region, max_results=8)
    if not trending:
        return f"Could not retrieve trending videos for {region}, sir."

    lines  = [f"Top trending in {region}:"] + [
        f"  {v['rank']}. {v['title']} — {v['channel']}"
        for v in trending
    ]
    result = "\n".join(lines)

    if speak:
        top3   = trending[:3]
        spoken = (
            f"Here are the top trending videos in {region}, sir. "
            + ". ".join(
                f"Rank {v['rank']}: {v['title']} by {v['channel']}"
                for v in top3
            )
        )
        speak(spoken)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def youtube_video(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    JARVIS YouTube controller.

    Parameters
    ──────────
    action   : play | pause | resume | play_pause | summarize | get_info | trending
    query    : search string (for action=play)
    url      : explicit video URL (for summarize / get_info; auto-detected from active tab if omitted)
    save     : bool  — save summary to Desktop (action=summarize)
    region   : country code for trending, e.g. "US", "TR"  (action=trending)
    browser  : browser to use for playback (default: "brave")
    """
    # === PASTE THE SNIPPET HERE (REPLACING THE OLD PARAMS EXTRACTION) ===
    params       = parameters or {}
    raw_action   = params.get("action", "play").lower().strip()
    browser_name = params.get("browser", _DEFAULT_BROWSER).lower().strip()

    # Normalize input strings to stay backward compatible with internal functions
    action = raw_action.replace("youtube_", "")
    # ====================================================================

    if player:
        player.write_log(f"[YouTube] Action: {action}")
    print(f"[YouTube] ▶️  Action: {action} | Browser: {browser_name} | Params: {params}")

    # ── Playback state controls ───────────────────────────────────────────────
    if action in ("pause", "resume", "play_pause"):
        return _handle_playback_control(action)

    # ── Routed actions ────────────────────────────────────────────────────────
    try:
        if action == "play":
            return _handle_play(params, player, browser_name) or "Done."

        if action == "summarize":
            return _handle_summarize(params, player, speak, browser_name) or "Done."

        if action == "get_info":
            return _handle_get_info(params, player, speak, browser_name) or "Done."

        if action == "trending":
            return _handle_trending(params, player, speak) or "Done."

        return f"Unknown YouTube action: '{action}'."

    except Exception as e:
        print(f"[YouTube] ❌ Error in action '{action}': {e}")
        return f"YouTube action failed, sir: {e}"