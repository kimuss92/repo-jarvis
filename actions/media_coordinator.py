from actions.youtube_video import youtube_video
from actions.netflix_control import netflix_control
from actions.computer_settings import computer_settings
from actions.browser_control import get_browser_session
from actions.window_control import focus_window
import time

def check_if_playing(target: str) -> bool:
    """Helper logic to detect if something is playing or active, this could just safely send pause signals."""
    return True

def media_coordinator(parameters: dict = None, player=None) -> str:
    params = parameters or {}
    target = str(params.get("target", "spotify")).lower().strip()
    action = str(params.get("action", "play")).lower().strip()

    # If the user asks to pause, we can just safely pause all known targets or just the intended one.
    if action in ("pause", "stop"):
        if target == "youtube":
            return youtube_video({"action": "youtube_pause"}, player=player)
        elif target == "netflix":
            return netflix_control({"action": "pause"}, player=player)
        else:
            return computer_settings({"action": "spotify_pause"}, player=player)

    # For play/resume, we must pause the others.
    if action in ("play", "resume"):
        # Pause background
        if target == "youtube":
            computer_settings({"action": "spotify_pause"}, player=player)
            netflix_control({"action": "pause"}, player=player)
            return youtube_video({"action": "youtube_resume"}, player=player)

        elif target == "netflix":
            computer_settings({"action": "spotify_pause"}, player=player)
            youtube_video({"action": "youtube_pause"}, player=player)

            # Switch to the netflix tab in browser
            from actions.browser_control import browser_control
            browser_control({"action": "focus_tab", "url": "netflix.com"}, player=player)
            time.sleep(0.5)

            # Now the window title should contain Netflix, so we bring it to foreground
            focus_window("netflix")

            # Wait for focus to settle
            time.sleep(0.5)

            res = netflix_control({"action": "play"}, player=player)

            # The prompt says: bring netflix tab to foreground and play it in full screen via f11
            computer_settings({"action": "press_key", "value": "f11"}, player=player)

            return res

        else:
            # Default to spotify play, pause the others
            youtube_video({"action": "youtube_pause"}, player=player)
            netflix_control({"action": "pause"}, player=player)
            return computer_settings({"action": "spotify_play"}, player=player)

    return f"Unknown media_coordinator target/action: {target} / {action}"
