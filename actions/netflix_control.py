# actions/netflix_control.py
from __future__ import annotations

from actions.browser_control import get_browser_session
from core.intent_memory import remember_action

# ── Action normalisation map ───────────────────────────────────────────────────
# Maps raw / ASR action strings to the canonical internal action name.
_ACTION_ALIASES: dict[str, str] = {
    # play variants
    "resume":           "play",
    # pause/toggle variants
    "play_pause":       "toggle_playback",
    "toggle":           "toggle_playback",
    # skip variants
    "skip":             "skip_intro",
    "skip_recap":       "skip_intro",
    # forward / rewind
    "fast_forward":     "forward",
    "back_10s":         "rewind",
    "back":             "rewind",
    # mute variants
    "unmute":           "mute",   # toggle logic inside JS
}

# Actions that are considered "play" events and must trigger the enforcer.
_PLAY_ACTIONS = frozenset({"play", "grid_play", "toggle_playback"})

# JS scripts for each standard action
_JS_SCRIPTS: dict[str, str] = {
    "play": (
        "const v = document.querySelector('video'); "
        "if(v) return v.play().then(() => 'Netflix resumed').catch(e => 'Netflix resume blocked: ' + e.message); "
        "const billboard = document.querySelector('.billboard a.playLink, .billboard button.color-primary'); "
        "if(billboard) { billboard.click(); return 'Started the featured Netflix show.'; } "
        "return 'No active Netflix video element found';"
    ),
    "pause": (
        "const v = document.querySelector('video'); "
        "if(v) { v.pause(); return 'Netflix paused'; } "
        "return 'No active Netflix video element found';"
    ),
    "toggle_playback": (
        "const v = document.querySelector('video'); "
        "if(!v) return 'No active Netflix video element found'; "
        "if(v.paused) { return v.play().then(() => 'Netflix resumed').catch(e => 'Netflix resume blocked: ' + e.message); } "
        "v.pause(); return 'Netflix paused';"
    ),
    "skip_intro": (
        "const btn = document.querySelector("
        "  '.watch-video--skip-content-button, .skip-credits, "
        "   button[data-uia=\"player-skip-intro\"], button[data-uia=\"player-skip-recap\"]'"
        "); "
        "if(btn) { btn.click(); return 'Intro sequence bypassed successfully'; } "
        "return 'Skip button element not found on canvas frame';"
    ),
    "forward": (
        "const v = document.querySelector('video'); "
        "if(v) { v.currentTime += 10; return 'Fast forwarded 10 seconds'; } "
        "return 'No active media context';"
    ),
    "rewind": (
        "const v = document.querySelector('video'); "
        "if(v) { v.currentTime -= 10; return 'Rewound 10 seconds'; } "
        "return 'No active media context';"
    ),
    "fullscreen": (
        "const btn = document.querySelector("
        "  'button[data-uia=\"control-fullscreen-enter\"], "
        "   button[data-uia=\"control-fullscreen-exit\"], "
        "   button[data-uia*=\"fullscreen\"]'"
        "); "
        "if (btn) { btn.click(); return 'Fullscreen mode toggled via UI element'; } "
        "const v = document.querySelector('video'); "
        "if (v) { "
        "  const ev = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'F11', code: 'F11', keyCode: 122 }); "
        "  v.dispatchEvent(ev); return 'Fullscreen toggled via simulated F11 key pipeline'; "
        "} "
        "return 'Unable to locate fullscreen node or video context';"
    ),
    "mute": (
        "const v = document.querySelector('video'); "
        "if(v) { v.muted = !v.muted; return 'Audio mute status toggled'; } "
        "return 'No video context';"
    ),
}


def _enforce_single_playback(params: dict) -> None:
    """
    SINGLE PLAYBACK ENFORCER — called only when Netflix is about to PLAY.

    Silences Spotify and YouTube so Netflix has exclusive audio/video.
    Each import is guarded so a missing module never blocks Netflix playback.
    """
    # Explicitly read the safety flag or check natively if called via the LLM function call pipeline
    if "spotify_running" in params:
        is_spotify_active = params["spotify_running"]
    else:
        try:
            import psutil
            is_spotify_active = any(p.info['name'] and p.info['name'].lower() == 'spotify.exe' for p in psutil.process_iter(['name']))
        except Exception:
            is_spotify_active = True
    
    if is_spotify_active:
        try:
            from actions.spotify_control import spotify_control
            result = spotify_control({"action": "pause"})
            print(f"[Netflix Enforcer] Spotify silenced → {result}")
        except Exception as exc:
            print(f"[Netflix Enforcer] Spotify silence skipped: {exc}")
    else:
        print("[Netflix Enforcer] Spotify is confirmed closed. Skipping pause call to prevent launch.")

    # 2. Pause YouTube
    try:
        from actions.youtube_video import youtube_video
        result = youtube_video({"action": "pause"})
        print(f"[Netflix Enforcer] YouTube silenced → {result}")
    except Exception as exc:
        print(f"[Netflix Enforcer] YouTube silence skipped: {exc}")


def netflix_control(parameters: dict | None = None, player=None) -> str:
    """
    Netflix media controller.

    Supported actions
    -----------------
    play / resume         — resume or start playback
    pause                 — pause playback
    toggle_playback       — play if paused, pause if playing
    skip_intro / skip     — click the skip-intro / skip-recap button
    forward               — seek +10 s
    rewind / back_10s     — seek −10 s
    fullscreen            — toggle fullscreen
    mute                  — toggle mute
    grid_play             — click a title card by position on the browse grid

    All aliases are listed in _ACTION_ALIASES above.
    """
    params = parameters or {}
    raw_action = str(params.get("action", "")).lower().strip()

    # ── Normalise action ───────────────────────────────────────────────────────
    action = _ACTION_ALIASES.get(raw_action, raw_action)

    print(f"[Netflix] Action received: {raw_action!r} → normalised: {action!r}")

    if player:
        player.write_log(f"[Netflix] {action}")

    # ── Intent memory ──────────────────────────────────────────────────────────
    try:
        remember_action(
            tool="netflix_control",
            action=action,
            app="browser",
            media_app="netflix",
            browser="brave",
        )
    except Exception:
        pass

    # ── SINGLE PLAYBACK ENFORCER ───────────────────────────────────────────────
    # Only fires for genuine Netflix play actions.
    # Silences Spotify and YouTube BEFORE Netflix resumes.
    # Dynamically delegates Spotify commands depending on live process status handles.
    if action in _PLAY_ACTIONS:
        _enforce_single_playback(params)

    # ── Grid Play (dynamic thumbnail selector) ─────────────────────────────────
    if action == "grid_play":
        position_text = params.get("position", "").strip().lower().replace("_", " ")
        
        # Spatial Mapper Parsing: converts natural language or digits to 1-based index
        mapping = {
            "top left": 1, "first": 1,
            "top center": 2, "top middle": 2, "center": 2, "middle": 2,
            "top right": 3, "third": 3,
            "bottom left": 4, "middle left": 4, "lower left": 4,
            "bottom center": 5, "bottom middle": 5, "lower center": 5,
            "bottom right": 6, "middle right": 6, "lower right": 6
        }
        
        if position_text in mapping:
            target_index = mapping[position_text]
        else:
            try:
                target_index = int(position_text)
            except ValueError:
                target_index = 1  # Default fallback index
                
        # Pure JavaScript logic without double-IIFE wrappers
        script = f"""
        // Auto-select profile if still on profile chooser page
        const profile = document.querySelector('.profile-link, [data-uia="action-select-profile"]');
        if (profile) {{ 
            profile.click(); 
            return 'Profile selection node intercepted and clicked successfully.'; 
        }}

        // Geographical DOM Math & Programmatic Row Sorting 
        const targetIndex = {target_index};
        const selectors = '[data-uia="title-card"] a, .title-card a, .slider-item a';
        const elements = Array.from(document.querySelectorAll(selectors));
        
        const validElements = [];
        const seenHrefs = new Set();

        for (let el of elements) {{
            const rect = el.getBoundingClientRect();
            // Filter out hidden frames, sleeping layout carousels, or unrendered layout grids
            if (rect.width > 20 && rect.height > 20 && el.href) {{
                // Deduplicate links using clean url paths to eliminate slider overlay shadow clones
                const cleanUrl = el.href.split('?')[0];
                if (!seenHrefs.has(cleanUrl)) {{
                    seenHrefs.add(cleanUrl);
                    validElements.push({{ element: el, x: rect.left, y: rect.top, href: el.href }});
                }}
            }}
        }}
        
        if (validElements.length === 0) {{
            return 'No interactive layout cards discovered inside the visual DOM workspace.';
        }}

        // Sort remaining elements Top-to-Bottom
        validElements.sort((a, b) => a.y - b.y);
        
        // Group elements into rows by using a vertical pixel layout tolerance threshold of 50px
        const rows = [];
        let currentRow = [];
        const tolerance = 50;
        
        for (let item of validElements) {{
            if (currentRow.length === 0) {{
                currentRow.push(item);
            }} else {{
                if (Math.abs(item.y - currentRow[0].y) <= tolerance) {{
                    currentRow.push(item);
                }} else {{
                    rows.push(currentRow);
                    currentRow = [item];
                }}
            }}
        }}
        if (currentRow.length > 0) {{
            rows.push(currentRow);
        }}
        
        // Sort each row Left-to-Right
        const sortedElements = [];
        for (let row of rows) {{
            row.sort((a, b) => a.x - b.x);
            sortedElements.push(...row);
        }}
        
        // Seamless Execution
        if (targetIndex >= 1 && targetIndex <= sortedElements.length) {{
            const target = sortedElements[targetIndex - 1];
            if (target.href) {{
                window.location.href = target.href;
                return `Navigating directly to visual track at layout index: ${{targetIndex}}`;
            }} else {{
                target.element.click();
                return `Fired fallback UI click sequence on track at layout index: ${{targetIndex}}`;
            }}
        }}
        return `Grid click request index [${{targetIndex}}] falls out of layout boundary bounds (Found ${{sortedElements.length}} visible unique elements).`;
        """
        try:
            sess = get_browser_session("brave")
            result = sess.run(sess.evaluate_background_media("netflix.com", script))
            print(f"[Netflix] CDP response: {result}")
            return str(result)
        except Exception as exc:
            return f"Netflix grid_play exception: {exc}"

    # ── Standard Playback Controls ─────────────────────────────────────────────
    script = _JS_SCRIPTS.get(action)
    if not script:
        return (
            f"[Netflix] Unknown action: '{action}'. "
            f"Valid actions: {', '.join(sorted(_JS_SCRIPTS.keys()) + ['grid_play'])}"
        )

    try:
        sess = get_browser_session("brave")
        result = sess.run(sess.evaluate_background_media("netflix.com", script))
        print(f"[Netflix] CDP response: {result}")
        return str(result)
    except Exception as exc:
        return f"Netflix background communication exception: {exc}"