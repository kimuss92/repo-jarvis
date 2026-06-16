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
try:
    from actions.browser_control import detect_default_browser_name, get_browser_session
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

_DEFAULT_BROWSER = None

def _default_youtube_browser() -> str | None:
    if not _BROWSER_BRIDGE:
        return None
    try:
        return detect_default_browser_name()
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_first_video_url(query: str) -> Optional[str]:
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

def _navigate_in_browser(url: str, browser_name: str | None = _DEFAULT_BROWSER) -> str:
    browser_name = browser_name or _default_youtube_browser()
    if _BROWSER_BRIDGE:
        try:
            sess   = get_browser_session(browser_name)
            result = sess.run(sess.new_tab(url))
            print(f"[YouTube] 🎬 In-tab navigation → {url}")
            return result
        except Exception as e:
            print(f"[YouTube] ⚠️ Browser bridge failed ({e}), falling back to OS open")

    _os_open_url(url)
    return f"Opened (OS): {url}"

def _os_open_url(url: str) -> None:
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
# PLAYBACK CONTROL  (Background execution)
# ─────────────────────────────────────────────────────────────────────────────

def _handle_playback_control(action: str, browser_name: str | None = _DEFAULT_BROWSER) -> str:
    browser_name = browser_name or _default_youtube_browser()
    try:
        remember_action(tool="youtube_video", action=f"youtube_{action}", app="browser", media_app="youtube", browser=browser_name)
    except Exception:
        pass
        
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
        sess = get_browser_session(browser_name)
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
    query = params.get("query", "").strip()
    if not query:
        return "Please tell me what you'd like to watch, sir."

    # Running-only Spotify pause: prevents unwanted Spotify startup during YouTube navigation.
    try:
        from actions.spotify_control import spotify_control
        spotify_control({"action": "pause_if_running_only"})
    except Exception:
        pass

    if player:
        player.write_log(f"[YouTube] Searching: {query}")
    print(f"[YouTube] 🔍 Searching for: {query}")

    video_url = _scrape_first_video_url(query)
    target    = video_url or (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}&sp={_YT_VIDEO_FILTER}"
    )
    _navigate_in_browser(target, browser_name)

    # Post-navigation double-playback guard.
    try:
        from actions.spotify_control import spotify_control
        spotify_control({"action": "pause_if_running_only"})
    except Exception:
        pass

    action_desc = f"Playing: {query}" if video_url else f"Opened YouTube search for: {query}"
    print(f"[YouTube] {action_desc}")
    return action_desc

def _handle_summarize(params: dict, player, speak, browser_name: str) -> str:
    if not _TRANSCRIPT_OK:
        return "youtube-transcript-api not installed. Run: pip install youtube-transcript-api"
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

def _handle_home_play(params: dict, browser_name: str) -> str:
    position = params.get("position", "").strip().lower().replace("_", " ")
    title = params.get("title", "").strip()

    print(f"[YouTube] Grid Selection - Position: '{position}', Title: '{title}'")

    if title:
        script = f"""
        const links = document.querySelectorAll('a#video-title-link, a#video-title, #video-title');
        for (const link of links) {{
            if (link.textContent.toLowerCase().includes(`{title.lower()}`)) {{
                const thumb = link.closest('ytd-rich-item-renderer, ytd-video-renderer, ytd-compact-video-renderer')?.querySelector('a#thumbnail, a[href^="/watch"]');
                if (thumb) {{ thumb.click(); }} else {{ link.click(); }}
                return 'Playing requested video: {title}';
            }}
        }}
        return 'Could not find a video titled "{title}" on the grid.';
        """
    else:
        # True Spatial Grid Selection based on X/Y Coordinates
        script = f"""
        let pos = "{position}";
        let items = Array.from(document.querySelectorAll('ytd-rich-item-renderer, ytd-video-renderer, ytd-compact-video-renderer'))
            .filter(el => el.getBoundingClientRect().width > 0 && el.querySelector('a[href^="/watch"]'));
        
        if (items.length === 0) return 'No videos found on the YouTube grid.';
        
        // Sort items by Y (top) then X (left) to ensure strict visual layout order
        items.sort((a, b) => {{
            let rA = a.getBoundingClientRect(); let rB = b.getBoundingClientRect();
            if (Math.abs(rA.top - rB.top) < 50) return rA.left - rB.left;
            return rA.top - rB.top;
        }});
        
        // Calculate how many videos are currently in the first row
        let row1 = items.filter(i => Math.abs(i.getBoundingClientRect().top - items[0].getBoundingClientRect().top) < 50);
        let targetIndex = 0;
        
        if (pos.includes("second") || (pos.includes("top") && pos.includes("middle"))) targetIndex = 1;
        else if (pos.includes("third")) targetIndex = 2;
        else if (pos.includes("top right") || pos.includes("right")) targetIndex = row1.length > 0 ? row1.length - 1 : 0;
        else if (pos.includes("bottom left")) targetIndex = row1.length;
        else if (pos.includes("bottom right")) targetIndex = (row1.length > 0 ? (row1.length * 2) - 1 : 0);
        
        if (targetIndex >= items.length) targetIndex = items.length - 1;
        
        let thumb = items[targetIndex].querySelector('a#thumbnail, a[href^="/watch"]');
        if(thumb) {{ thumb.click(); return 'Playing video at calculated position: ' + pos; }}
        return 'Failed to click thumbnail at position.';
        """

    try:
        sess = get_browser_session(browser_name)
        result = sess.run(sess.evaluate_background_media("youtube.com", script))
        return str(result)
    except Exception as e:
        return f"Failed to click grid item: {e}"
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
    """JARVIS YouTube controller."""
    params       = parameters or {}
    raw_action   = params.get("action", "play").lower().strip()

    # Backward-compatible normalization: some older prompt/tooling uses these tokens
    if raw_action in {"youtube_play", "youtube_pause", "youtube_resume"}:
        raw_action = raw_action.replace("youtube_", "")

    # Ensure spatial actions use expected naming if older callers still pass home_play
    # (keeps existing behavior)


    browser_value = params.get("browser", _DEFAULT_BROWSER)
    browser_name = str(browser_value).lower().strip() if browser_value else _default_youtube_browser()

    # Normalize input strings
    action = raw_action.replace("youtube_", "")

    if player:
        player.write_log(f"[YouTube] Action: {action}")
    print(f"[YouTube] ▶️  Action: {action} | Browser: {browser_name} | Params: {params}")

    def _pause_spotify_running_only():
        """
        Pause Spotify only if it's already running. Prevents YouTube actions
        from accidentally launching Spotify, but still removes double playback.
        """
        try:
            from actions.spotify_control import spotify_control
            return spotify_control({"action": "pause_if_running_only"})
        except Exception:
            return None

    # ── NEW: SINGLE PLAYBACK ENFORCER ────────────────────────────────────────
    # Treat all YouTube play-like navigation/actions as "exclusive media play" events.
    # IMPORTANT: include grid_play; this path previously didn't silence Spotify.
    if action in ("resume", "play_pause", "play", "grid_play", "youtube_home_play", "home_play"):
        # Running-only pause prevents Spotify launch during YouTube navigation.
        _pause_spotify_running_only()

        # 2. Kill Netflix Video (use normal pause; it shouldn't start Netflix)
        try:
            from actions.netflix_control import netflix_control
            netflix_control({"action": "pause"})
            print("[YouTube] Netflix auto-silenced for video priority.")
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────────────
    
    # ── Playback state controls ───────────────────────────────────────────────
    if action in ("pause", "resume", "play_pause"):
        result = _handle_playback_control(action, browser_name)

        # Post-action double-playback guard:
        # If Spotify starts/resumes due to late media focus/audio routing,
        # run a second running-only pause check.
        if action in ("resume", "play_pause", "play"):
            try:
                from actions.spotify_control import spotify_control
                spotify_control({"action": "pause_if_running_only"})
            except Exception:
                pass

        return result

    # ── Routed actions ────────────────────────────────────────────────────────
    try:
        if action == "play":
            return _handle_play(params, player, browser_name) or "Done."

        # Unified routing track to guarantee advanced visual mapping
        if action in ("home_play", "grid_play"):
            # If coming from home_play, use 'position' as the 'target' mapper key safely
            target = params.get("target", None) or params.get("position", None)

            # Running-only Spotify pause before grid navigation
            try:
                from actions.spotify_control import spotify_control
                spotify_control({"action": "pause_if_running_only"})
            except Exception:
                pass

            spatial = {
                "top left": 1, "first": 1,
                "top center": 2, "top middle": 2, "center": 2, "middle": 2,
                "top right": 3, "third": 3,
                "bottom left": 4, "middle left": 4, "lower left": 4,
                "bottom center": 5, "bottom middle": 5, "lower center": 5,
                "bottom right": 6, "middle right": 6, "lower right": 6,
            }

            def parse_target_to_index(t) -> int:
                if t is None: return 1
                if isinstance(t, (int, float)):
                    try: return max(1, int(t))
                    except Exception: return 1
                s = str(t).strip().lower()
                if s in spatial: return spatial[s]
                m = re.search(r"(\d+)", s)
                if m: return max(1, int(m.group(1)))
                return 1

            index = parse_target_to_index(target)
            sess = get_browser_session(browser_name)
            
            # Pure raw JS string statement payload without double wrappers
            click_script = f"""
                const allLinks = Array.from(document.querySelectorAll('a[href*="/watch?v="]'));
                const visibleLinks = allLinks.filter(el => {{
                    const rect = el.getBoundingClientRect();
                    return rect.width > 20 && rect.height > 20; 
                }});

                const uniqueVideos = [];
                const seenIds = new Set();
                
                for (const link of visibleLinks) {{
                    try {{
                        const urlObj = new URL(link.href, window.location.origin);
                        const vid = urlObj.searchParams.get('v');
                        if (vid && !seenIds.has(vid)) {{
                            seenIds.add(vid);
                            uniqueVideos.push({{
                                href: link.href,
                                rect: link.getBoundingClientRect()
                            }});
                        }}
                    }} catch(e) {{}}
                }}
                
                uniqueVideos.sort((a, b) => {{
                    if (Math.abs(a.rect.top - b.rect.top) > 40) {{
                        return a.rect.top - b.rect.top;
                    }}
                    return a.rect.left - b.rect.left;
                }});

                const targetIndex = {index} - 1;
                const targetVideo = uniqueVideos[targetIndex];
                
                if (!targetVideo) {{
                    return `Grid click failed: Video #${{targetIndex + 1}} not found. (Visible count: ${{uniqueVideos.length}}).`;
                }}
                
                window.location.href = targetVideo.href;
                return `Grid click OK: Navigating to video #${{targetIndex + 1}}.`;
            """
            result = str(sess.run(sess.evaluate_background_media("youtube.com", click_script)))

            # Post-grid double-playback guard.
            try:
                from actions.spotify_control import spotify_control
                spotify_control({"action": "pause_if_running_only"})
            except Exception:
                pass

            return result

        if action == "volume_up":
            amount = params.get("amount", None)
            try:
                vol_change = float(amount) if amount is not None else 10.0
            except Exception:
                vol_change = 10.0
            
            sess = get_browser_session(browser_name)
            # Use YouTube's internal '#movie_player' API to sync the visual UI slider with the system
            script = f"""
                const player = document.querySelector('#movie_player');
                if (player && typeof player.setVolume === 'function') {{
                    let current = player.getVolume();
                    if (player.isMuted()) {{ player.unMute(); current = 0; }}
                    let newVol = Math.min(100, current + {vol_change});
                    player.setVolume(newVol);
                    return `Volume increased by {vol_change}%`;
                }}
                // Fallback if the native API is blocked
                const v = document.querySelector('video');
                if (v) {{
                    v.volume = Math.min(1.0, v.volume + ({vol_change} / 100.0));
                    return `Volume increased by {vol_change}% (Fallback mode)`;
                }}
                return 'No video element found to adjust volume.';
            """
            return str(sess.run(sess.evaluate_background_media("youtube.com", script)))

        if action == "volume_down":
            amount = params.get("amount", None)
            try:
                vol_change = float(amount) if amount is not None else 10.0
            except Exception:
                vol_change = 10.0
            
            sess = get_browser_session(browser_name)
            script = f"""
                const player = document.querySelector('#movie_player');
                if (player && typeof player.setVolume === 'function') {{
                    let current = player.getVolume();
                    let newVol = Math.max(0, current - {vol_change});
                    player.setVolume(newVol);
                    return `Volume decreased by {vol_change}%`;
                }}
                const v = document.querySelector('video');
                if (v) {{
                    v.volume = Math.max(0.0, v.volume - ({vol_change} / 100.0));
                    return `Volume decreased by {vol_change}% (Fallback mode)`;
                }}
                return 'No video element found to adjust volume.';
            """
            return str(sess.run(sess.evaluate_background_media("youtube.com", script)))

        if action == "next_video":
            sess = get_browser_session(browser_name)
            script = """
                const nextBtn = document.querySelector('.ytp-next-button');
                if (nextBtn) { nextBtn.click(); return 'Next video button clicked successfully'; }
                return 'Next video controller button link node was not found on this frame layout';
            """
            return str(sess.run(sess.evaluate_background_media("youtube.com", script)))

        if action == "fullscreen":
            sess = get_browser_session(browser_name)
            script = """
                const fsBtn = document.querySelector('.ytp-fullscreen-button');
                if (fsBtn) { fsBtn.click(); return 'Fullscreen mode toggled via video canvas buttons'; }
                const v = document.querySelector('video');
                if (v) {
                    if (document.fullscreenElement) { document.exitFullscreen(); }
                    else { v.requestFullscreen(); }
                    return 'Native layout canvas fullscreen adjusted via fallback API';
                }
                return 'No clickable fullscreen nodes found inside DOM layer';
            """
            return str(sess.run(sess.evaluate_background_media("youtube.com", script)))

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

