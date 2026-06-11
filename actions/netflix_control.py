# actions/netflix_control.py
from actions.browser_control import get_browser_session
from core.utils import log
from core.intent_memory import remember_action

def netflix_control(parameters: dict = None, player=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()

    print(f"[Netflix] Executing background DOM token: {action!r}")
    if player:
        player.write_log(f"[Netflix] {action}")
    try:
        remember_action(tool="netflix_control", action=action, app="browser", media_app="netflix", browser="brave")
    except Exception:
        pass

    # Map target macro tokens directly to automated JavaScript expressions
    js_scripts = {
        "play": (
            "const v = document.querySelector('video'); "
            "if(v) { if(v.paused) { v.play(); return 'Video resumed'; } else { v.pause(); return 'Video paused'; } } "
            "return 'No active video element found';"
        ),
        "pause": (
            "const v = document.querySelector('video'); "
            "if(v) { v.pause(); return 'Video paused'; } "
            "return 'No active video element found';"
        ),
        "skip_intro": (
            "const btn = document.querySelector('.watch-video--skip-content-button, .skip-credits, button[data-uia=\"player-skip-intro\"], button[data-uia=\"player-skip-recap\"]'); "
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
            "const btn = document.querySelector('button[data-uia=\"control-fullscreen\"]'); "
            "if(btn) { btn.click(); return 'Fullscreen mode toggled'; } "
            "return 'Fullscreen node missing';"
        ),
        "mute": (
            "const v = document.querySelector('video'); "
            "if(v) { v.muted = !v.muted; return 'Audio mute status toggled'; } "
            "return 'No video context';"
        )
    }

    # Normalize alternate trigger phrases cleanly
    if action in ("toggle_playback", "play_pause"):
        action = "play"
    elif action in ("skip", "skip_recap"):
        action = "skip_intro"
    elif action in ("fast_forward"):
        action = "forward"
    elif action in ("back_10s"):
        action = "rewind"

    script = js_scripts.get(action)
    if not script:
        return f"Unknown background macro token: '{action}'."

    try:
        sess = get_browser_session("brave")
        # Route execution to the background Playwright thread safely
        result = sess.run(sess.evaluate_background_media("netflix.com", script))
        print(f"[Netflix] Native CDP Pipeline Response: {result}")
        return result
    except Exception as e:
        return f"Netflix background communication loop exception: {e}"