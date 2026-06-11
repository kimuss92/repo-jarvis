# core/self_healing_router.py
"""Self-healing tool router for JARVIS.

This layer does not fake success. It only normalizes obvious routing mistakes,
adds runtime context, records outcomes, and provides safer fallbacks.
"""

from __future__ import annotations

import re
from typing import Any

from core.action_registry import canonicalize_tool_call
from core.intent_memory import load_state, remember_action
from core.process_awareness import is_running
from core.window_awareness import get_active_title


def _norm(s: str | None) -> str:
    return (s or "").lower().strip()


def _media_target_from_text(text: str) -> str | None:
    t = _norm(text)
    if "spotify" in t or "music" in t or "song" in t or "track" in t:
        return "spotify"
    if "youtube" in t or "video" in t or "clip" in t:
        return "youtube"
    if "netflix" in t or "movie" in t or "show" in t or "series" in t:
        return "netflix"
    return None


def route_tool_call(tool: str, parameters: dict | None, user_text: str = "") -> tuple[str, dict]:
    params = dict(parameters or {})
    tool, params = canonicalize_tool_call(tool, params)

    action = _norm(params.get("action"))
    desc = " ".join(str(params.get(k, "")) for k in ("description", "query", "text", "value", "url"))
    text = f"{user_text} {desc}".strip()
    target = _media_target_from_text(text)
    state = load_state()

    # Resolve bare play/pause using explicit text first, then intent memory, then active/running context.
    if tool in ("computer_settings", "youtube_video", "netflix_control") and action in ("play", "pause", "resume", "stop", "play_pause", "toggle_playback", ""):
        target = target or state.get("last_media_app")
        active_title = get_active_title("").lower()

        if not target:
            if "youtube" in active_title:
                target = "youtube"
            elif "netflix" in active_title:
                target = "netflix"
            elif is_running("spotify"):
                target = "spotify"

        if target == "youtube":
            tool = "youtube_video"
            params["action"] = "youtube_pause" if action in ("pause", "stop") else "youtube_resume"
        elif target == "netflix":
            tool = "netflix_control"
            params["action"] = "pause" if action in ("pause", "stop") else "play"
        elif target == "spotify":
            tool = "computer_settings"
            params["action"] = "spotify_pause" if action in ("pause", "stop") else "spotify_play"

    # "close this" / "minimize this" get active title if title missing.
    if tool == "window_control" and params.get("action") in ("close", "minimize", "maximize", "restore", "focus"):
        if not params.get("title"):
            params["title"] = get_active_title("")

    # Browser defaults.
    if tool in ("browser_control", "youtube_video", "netflix_control") and not params.get("browser"):
        last_browser = state.get("last_browser") or "brave"
        if tool == "browser_control":
            params.setdefault("browser", last_browser)

    tool, params = canonicalize_tool_call(tool, params)
    return tool, params


def record_tool_result(tool: str, parameters: dict | None, result: Any = None, error: Any = None) -> None:
    params = parameters or {}
    action = str(params.get("action", "") or "")
    media_app = None
    app = None
    browser = None

    if tool == "computer_settings" and action.startswith("spotify_"):
        media_app = app = "spotify"
    elif tool == "youtube_video":
        media_app = "youtube"
        app = "browser"
        browser = params.get("browser") or "brave"
    elif tool == "netflix_control":
        media_app = "netflix"
        app = "browser"
        browser = params.get("browser") or "brave"
    elif tool == "open_app":
        app = str(params.get("app_name", "") or "").lower() or None
    elif tool == "browser_control":
        app = "browser"
        browser = params.get("browser") or "brave"
    elif tool == "window_control":
        app = "window"

    remember_action(
        tool=tool,
        action=action or None,
        app=app,
        media_app=media_app,
        browser=browser,
        result=str(result) if result is not None else None,
        error=str(error) if error is not None else None,
    )


def is_failure(result: Any) -> bool:
    text = _norm(str(result))
    return any(x in text for x in (
        "failed", "error", "exception", "not found", "unknown", "cannot", "permission", "denied"
    ))


def fallback_suggestions(tool: str, parameters: dict | None, result: Any) -> list[tuple[str, dict]]:
    """Return safe alternate routes. Caller can choose whether to execute them."""
    params = dict(parameters or {})
    action = _norm(params.get("action"))
    suggestions: list[tuple[str, dict]] = []

    if tool == "computer_settings" and action.startswith("spotify_"):
        # Generic media-key fallback is inside computer_settings for most actions;
        # this suggestion gives executor/main one more safe route if needed.
        if action in ("spotify_play", "spotify_pause"):
            suggestions.append(("computer_control", {"action": "press", "key": "playpause"}))
        elif action == "spotify_next":
            suggestions.append(("computer_control", {"action": "press", "key": "nexttrack"}))
        elif action == "spotify_previous":
            suggestions.append(("computer_control", {"action": "press", "key": "prevtrack"}))

    if tool == "youtube_video" and action in ("youtube_pause", "youtube_resume"):
        suggestions.append(("browser_control", {"action": "press", "key": "Space"}))

    return suggestions
