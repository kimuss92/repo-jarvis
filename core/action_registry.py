# core/action_registry.py
"""Unified action registry for JARVIS routing.

The registry is intentionally lazy: handlers are imported only when called.
This avoids circular imports and keeps startup fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass(frozen=True)
class ActionSpec:
    tool: str
    action: str | None = None
    target: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


ACTION_SPECS: dict[str, ActionSpec] = {
    # system volume
    "volume_set": ActionSpec("computer_settings", "volume_set", aliases=("volume", "set_volume", "absolute_volume")),
    "volume_up": ActionSpec("computer_settings", "volume_up", aliases=("louder", "sound_up")),
    "volume_down": ActionSpec("computer_settings", "volume_down", aliases=("quieter", "sound_down")),
    "mute": ActionSpec("computer_settings", "mute", aliases=("toggle_mute", "volume_mute")),

    # spotify
    "spotify_play": ActionSpec("computer_settings", "spotify_play", "spotify", aliases=("play_music", "resume_music")),
    "spotify_pause": ActionSpec("computer_settings", "spotify_pause", "spotify", aliases=("pause_music", "stop_music")),
    "spotify_next": ActionSpec("computer_settings", "spotify_next", "spotify", aliases=("next_track", "next_song", "skip_track")),
    "spotify_previous": ActionSpec("computer_settings", "spotify_previous", "spotify", aliases=("previous_track", "previous_song", "prev_track")),
    "spotify_search": ActionSpec("computer_settings", "spotify_search", "spotify"),
    "spotify_like": ActionSpec("computer_settings", "spotify_like", "spotify"),

    # youtube
    "youtube_play": ActionSpec("youtube_video", "youtube_play", "youtube", aliases=("play_youtube",)),
    "youtube_pause": ActionSpec("youtube_video", "youtube_pause", "youtube", aliases=("pause_youtube",)),
    "youtube_resume": ActionSpec("youtube_video", "youtube_resume", "youtube", aliases=("resume_youtube",)),

    # netflix
    "netflix_play": ActionSpec("netflix_control", "play", "netflix", aliases=("netflix_resume", "netflix_pause_toggle")),
    "netflix_pause": ActionSpec("netflix_control", "pause", "netflix", aliases=("pause_netflix",)),
    "netflix_forward": ActionSpec("netflix_control", "forward", "netflix"),
    "netflix_rewind": ActionSpec("netflix_control", "rewind", "netflix"),
    "netflix_skip_intro": ActionSpec("netflix_control", "skip_intro", "netflix"),

    # windows/browser
    "window_active": ActionSpec("window_control", "active"),
    "window_list": ActionSpec("window_control", "list"),
    "close_window": ActionSpec("window_control", "close"),
    "focus_window": ActionSpec("window_control", "focus"),
    "browser_new_tab": ActionSpec("browser_control", "new_tab"),
    "browser_close_tab": ActionSpec("browser_control", "close_tab"),
    "browser_next_tab": ActionSpec("browser_control", "next_tab"),
    "browser_prev_tab": ActionSpec("browser_control", "prev_tab"),

    # awareness actions
    "running_apps": ActionSpec("computer_control", "running_apps", aliases=("processes", "what_is_running")),
    "system_snapshot": ActionSpec("computer_control", "system_snapshot", aliases=("pc_status", "computer_status")),
    "active_window": ActionSpec("computer_control", "active_window"),
}

_ALIAS_TO_KEY: dict[str, str] = {}
for key, spec in ACTION_SPECS.items():
    _ALIAS_TO_KEY[key] = key
    if spec.action:
        _ALIAS_TO_KEY[spec.action] = key
    for alias in spec.aliases:
        _ALIAS_TO_KEY[alias] = key


def normalize_action(action: str | None) -> str:
    raw = (action or "").lower().strip().replace("-", "_").replace(" ", "_")
    return _ALIAS_TO_KEY.get(raw, raw)


def get_spec(action: str | None) -> ActionSpec | None:
    key = normalize_action(action)
    return ACTION_SPECS.get(key)


def canonicalize_tool_call(tool: str, parameters: dict | None) -> tuple[str, dict]:
    params = dict(parameters or {})
    action = params.get("action")

    # Fix legacy exact-volume naming drift.
    if tool == "computer_settings" and str(action).lower().strip() == "volume":
        if params.get("value") is not None:
            params["action"] = "volume_set"
        else:
            params["action"] = "volume_up"

    spec = get_spec(params.get("action"))
    if spec:
        tool = spec.tool
        if spec.action:
            params["action"] = spec.action
    return tool, params


def tool_names() -> list[str]:
    return sorted({spec.tool for spec in ACTION_SPECS.values()})


def format_registry_for_prompt() -> str:
    rows = []
    for key, spec in sorted(ACTION_SPECS.items()):
        rows.append(f"- {key}: tool={spec.tool}, action={spec.action or '-'}, target={spec.target or '-'}")
    return "\n".join(rows)
