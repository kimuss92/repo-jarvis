import os
# Force Qt to use standard scaling pass-throughs before the window hooks launch
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"

import asyncio
import re
import threading
import json
import sys
import traceback
active_jarvis_instance = None  
from pathlib import Path

import sounddevice as sd
from core.utils import get_api_key, get_os, log, BASE_DIR
from core.self_healing_router import route_tool_call, record_tool_result
from core.process_awareness import format_running_apps
from core.window_awareness import format_context as format_window_context
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.window_control import window_control
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.open_app import open_app
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.netflix_control import netflix_control
from actions.media_coordinator import media_coordinator
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater

# ----- NEW IMPORTS FOR STANDBY / WAKE WORD -----
import vosk
import queue
# -----------------------------------------------





API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ---- VOSK CONFIGURATION ----
VOSK_MODEL_PATH = BASE_DIR / "models" / "vosk-model-small-en-us-0.15"
WAKE_WORD = "jarvis"

def init_vosk() -> vosk.Model | None:
    """Load Vosk model if available, otherwise print error and return None."""
    if not VOSK_MODEL_PATH.exists():
        print(f"[WAKE] Model not found: {VOSK_MODEL_PATH}")
        print("Please download vosk-model-small-en-us-0.15 into the 'models' folder.")
        return None
    try:
        model = vosk.Model(str(VOSK_MODEL_PATH))
        print("[WAKE] Vosk model loaded.")
        return model
    except Exception as e:
        print(f"[WAKE] Failed to load Vosk model: {e}")
        return None


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
"description": (
    "Opens an application on the computer system. If the application is already running "
    "or matching open windows on the desktop environment, this tool will safely pivot "
    "to restore window focus automatically. Do not try to launch a process manually if it is running."
),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, pausing/resuming playback, "
            "summarizing a video's content, getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING", 
                    "description": "The exact YouTube action token to execute: youtube_play | youtube_pause | youtube_resume | summarize | get_info | trending"
                },
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },  
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page, "
            "AND Spotify playback execution. Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING", 
                    "description": (
                        "The exact system action token to execute. "
                        "Options include: volume_up, volume_down, mute, toggle_mute, dark_mode, "
                        "screenshot, lock_screen, close_app, minimize, maximize, refresh_page, "
                        "spotify_play, spotify_pause, spotify_next, spotify_previous, spotify_like, "
                        "spotify_dislike, spotify_search, spotify_liked_songs, spotify_new_releases, "
                        "spotify_made_for_you, spotify_volume_up, spotify_volume_down"
                    )
                },
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "The song title or artist name to search for (Required when action is spotify_search)"}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls web browser features, active tabs, and monitor layouts. "
            "Supports listing all open tabs, cycling navigation, closing tabs, "
            "switching tabs by index or title keyword, interacting with web elements, "
            "and casting active video playback streams to secondary display monitors."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "The exact action token to execute. Options include: "
                        "list_tabs, active_tab, switch_tab, focus_tab, next_tab, prev_tab, "
                        "new_tab, close_tab, go_to, search, reload, back, forward, click, "
                        "smart_click, type, smart_type, fill_form, scroll, press, get_text, "
                        "get_url, screenshot, cast_screen, close"
                    )
                },
                "browser": {
                    "type": "STRING",
                    "description": "Target browser engine: brave | chrome | edge | firefox | opera | operagx | vivaldi | safari. Omit to default to the active session."
                },
                "url": {
                    "type": "STRING",
                    "description": "The destination URL string for go_to/new_tab, or title keyword fragment for focus_tab."
                },
                "index": {
                    "type": "INTEGER",
                    "description": "The exact numeric tab placement index starting from 0 (Required when action is switch_tab)."
                },
                "query": {
                    "type": "STRING",
                    "description": "The web search text string query argument (Required when action is search)."
                },
                "engine": {
                    "type": "STRING",
                    "description": "Target search index engine: google | bing | duckduckgo | yandex (default: google)."
                },
                "selector": {
                    "type": "STRING",
                    "description": "Target page interaction CSS element query selector path (Used for standard click/type)."
                },
                "text": {
                    "type": "STRING",
                    "description": "Visible button or text label element name to locate on the target web page canvas frame."
                },
                "description": {
                    "type": "STRING",
                    "description": "Accessibility aria-label role description or placeholder text string used for smart_click/smart_type algorithms."
                },
                "direction": {
                    "type": "STRING",
                    "description": "The physical wheel scroll movement vector path: up | down | left | right."
                },
                "amount": {
                    "type": "INTEGER",
                    "description": "The pixel navigation offset scale length applied during scroll cycles (default: 500)."
                },
                "key": {
                    "type": "STRING",
                    "description": "The target keyboard key token string identifier to press (e.g., 'Enter', 'Escape', 'Space')."
                },
                "path": {
                    "type": "STRING",
                    "description": "Target persistent storage directory path used when executing local page screenshots."
                },
                "clear_first": {
                    "type": "BOOLEAN",
                    "description": "Completely wipe any stale text out of a page text box input field prior to typing new arguments (default: true)."
                },
                "fields": {
                    "type": "OBJECT",
                    "description": "A structured mapping dictionary containing selector-to-value parameters utilized during fill_form processing."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "netflix_control",
        "description": "Controls active Netflix streaming playback. Use for skipping intros, pausing, fast forwarding, rewinding, or fullscreen toggles.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "The target playback macro to fire: play | skip_intro | forward | rewind | fullscreen | mute"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data | running_apps | active_window | system_snapshot"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    # ----- NEW TOOL: ENTER STANDBY MODE -----
    {
        "name": "enter_standby",
        "description": (
            "Mutes the microphone and puts JARVIS into standby mode. "
            "Call this when the user says 'take a break', 'go to sleep', 'standby', "
            "'mute yourself' or any similar command. "
            "While in standby, JARVIS listens only for the wake word 'Jarvis'."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    # ----------------------------------------
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "media_coordinator",
        "description": "Orchestrates media playing between Spotify, YouTube, and Netflix. Pauses conflicting sources when a new stream is requested to play.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "The media target: spotify | youtube | netflix"},
                "action": {"type": "STRING", "description": "play | pause"}
            },
            "required": ["target", "action"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
},
    {
        "name": "window_control",
        "description": (
            "Advanced window management. Use to list all open windows, "
            "focus a specific window by title, move/resize, minimize/maximize/restore, "
            "close a window, or tile windows in grid/horizontal/vertical layout. "
            "Always provide a window title fragment (partial match) for focus, move, resize, close, minimize, maximize, restore."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "list | focus | move | resize | close | minimize | maximize | restore | tile | active"
                },
                "title": {
                    "type": "STRING",
                    "description": "Window title fragment (partial match) – required for focus, move, resize, close, minimize, maximize, restore."
                },
                "x": {"type": "INTEGER", "description": "X coordinate for move action"},
                "y": {"type": "INTEGER", "description": "Y coordinate for move action"},
                "width": {"type": "INTEGER", "description": "New width for resize action"},
                "height": {"type": "INTEGER", "description": "New height for resize action"},
                "style": {"type": "STRING", "description": "grid | horizontal | vertical (for tile action, default: grid)"}
            },
            "required": ["action"]
        }
    },    
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None

        # ----- STANDBY / WAKE WORD ATTRIBUTES -----
        self._standby_active = False          # True when muted and waiting for wake word
        self._vosk_model = init_vosk()
        self._wake_recognizer: vosk.KaldiRecognizer | None = None
        self._wake_queue: asyncio.Queue[bytes] | None = None
        self._wake_task: asyncio.Task | None = None
        # ------------------------------------------

    def _on_text_command(self, text: str):
        # === INTERCEPTION STANDBY : Permet au réseau (iPhone/Watch) de réveiller JARVIS ===
        if self._standby_active:
            clean_cmd = text.lower().strip()
            if any(word in clean_cmd for word in ["wake up", "réveille-toi", "jarvis", "active"]):
                print("[WAKE] Remote wake phrase detected via network shortcut!")
                self._standby_active = False
                self.ui.set_mute(False)
                self.ui.set_state("LISTENING")
                self.ui.write_log("SYS: Woken up via Remote Text Command. Resuming.")
                
                self._stop_wake_word_listener()
                
                # === FIX: Safe cross-thread scheduling for wake-up notification ===
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        send_jarvis_notification_async("JARVIS", "Bonjour Monsieur. Je suis réveillé et à votre écoute."),
                        self._loop
                    )
                
                try:
                    print("[WAKE] Speaking remote exit phrase...")
                    self.speak("Online and ready, sir.")
                    
                    # 👈 DELAY FOR REMOTE ACTIVATION SPEECH
                    import time
                    time.sleep(1.5)
                except Exception as e:
                    print(f"[WAKE] Speak error: {e}")
                return
            else:
                self.ui.write_log(f"SYS: Ignored remote command '{text}' while in standby.")
                return
                
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )
                
    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    # ----- STANDBY / WAKE WORD METHODS -----

    def _start_wake_word_listener(self):
        """Start listening for wake word in the background."""
        if not self._vosk_model:
            return
        self._wake_recognizer = vosk.KaldiRecognizer(self._vosk_model, 16000)
        self._wake_queue = asyncio.Queue()

        async def wake_listener():
            try:
                while self._standby_active:
                    try:
                        data = await self._wake_queue.get()
                        if self._wake_recognizer.AcceptWaveform(data):
                            result = json.loads(self._wake_recognizer.Result())
                            text = result.get("text", "").lower()
                            if WAKE_WORD in text:
                                print("[WAKE] Wake word detected!")
                                self._standby_active = False
                                self.ui.set_mute(False)
                                self.ui.set_state("LISTENING")
                                self.ui.write_log("SYS: Wake word 'Jarvis' detected. Resuming.")
                                
                                while not self._wake_queue.empty():
                                    self._wake_queue.get_nowait()
                                
                                # --- QUICK PHRASE NOTIFICATION ON EXIT ---
                                try:
                                    print("[WAKE] Speaking exit phrase...")
                                    self.speak("At your service, sir.")
                                    
                                    # 👈 ASYNC DELAY: Holds the loop open to guarantee speech completion
                                    await asyncio.sleep(1.8) 
                                except Exception as e:
                                    print(f"[WAKE] Speak error: {e}")
                                
                                self._stop_wake_word_listener()
                                break
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        print(f"[WAKE] Listener error: {e}")
                        await asyncio.sleep(0.1)
            finally:
                # Cleanup when loop exits
                self._wake_queue = None
                self._wake_recognizer = None

        self._wake_task = asyncio.create_task(wake_listener())

    def _stop_wake_word_listener(self):
        """Stop the wake word listener task."""
        if self._wake_task and not self._wake_task.done():
            self._wake_task.cancel()
            # Give it a moment to clean up
            # In async context, you'd await, but here we just let it go
        self._wake_task = None

    def enter_standby(self):
        """Puts JARVIS into standby mode and plays the notification phrase flawlessly."""
        if self._standby_active:
            return
        self._stop_wake_word_listener()   # Clean previous listener
        self._standby_active = True
        
        # 1. Update UI state instantly
        self.ui.set_state("STANDBY")
        self.ui.write_log("SYS: Entering standby mode.")
        
        # 2. Clear out any lingering background noise or trailing speech chunks
        if self.audio_in_queue:
            while not self.audio_in_queue.empty():
                try:
                    self.audio_in_queue.get_nowait()
                except asyncio.queues.QueueEmpty:
                    break

        # 3. Handle speech and muting asynchronously so the audio chunks can stream out
        if self._loop and self._loop.is_running():
            async def play_and_mute():
                try:
                    print("[STANDBY] Safe-streaming entry phrase...")
                    self.speak("Jarvis is offline.")
                    # Give the async playback loop exactly 1.5 seconds to stream the phrase to your speakers
                    await asyncio.sleep(1.5)
                except Exception as e:
                    print(f"[STANDBY] Audio stream error: {e}")
                finally:
                    # Mute the mic ONLY after the phrase has finished playing
                    self.ui.set_mute(True)
                    if self._vosk_model:
                        self._start_wake_word_listener()
                    else:
                        self.ui.write_log("WARN: Vosk model missing – cannot wake from standby.")

            asyncio.run_coroutine_threadsafe(play_and_mute(), self._loop)
        else:
            # Fallback if loop isn't ready
            self.ui.set_mute(True)
            if self._vosk_model:
                self._start_wake_word_listener()

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime
        import pywinctl as pwc

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        
        # ── UPGRADED REAL-TIME OS VISION LAYER (ANTI-POLLUTION FILTER) ───────
        foreground_window = "Desktop"
        running_applications = []
        spotify_active = False
        youtube_active = False

        try:
            import win32gui
            import ctypes

            def is_window_really_visible(hwnd):
                if not win32gui.IsWindowVisible(hwnd):
                    return False
                rect = win32gui.GetWindowRect(hwnd)
                if (rect[2] - rect[0]) <= 0 or (rect[3] - rect[1]) <= 0:
                    return False
                # Filter out cloaked/suspended background instances (UWP/stale tabs)
                cloaked = ctypes.c_int(0)
                try:
                    ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                    )
                    if cloaked.value != 0:
                        return False
                except Exception:
                    pass
                return True

            def _enumerate_windows(hwnd, extra_list):
                if is_window_really_visible(hwnd):
                    title = win32gui.GetWindowText(hwnd).strip()
                    class_name = win32gui.GetClassName(hwnd)
                    
                    if title:
                        title_lower = title.lower()
                        # Ignore self-referential system engines
                        if any(kw in title_lower for kw in ["mark-xxxix", "j.a.r.v.i.s", "main.py", "prompt.txt"]):
                            return True
                        extra_list.append((title, class_name))
                return True

            visible_windows = []
            win32gui.EnumWindows(_enumerate_windows, visible_windows)
            
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd:
                foreground_window = win32gui.GetWindowText(fg_hwnd).strip()

            for title, class_name in visible_windows:
                title_lower = title.lower()
                
                # Crucial Fix: Exclude active text streams inside your IDE or code files
                is_code_workspace = any(ide in title_lower for ide in [
                    "visual studio", "vscode", "code -", ".py", ".txt", ".ahk", "github", "terminal", "cmd.exe"
                ])
                
                if class_name == "SpotifyMainWindow":
                    spotify_active = True
                    running_applications.append(f"- {title} [Target: Spotify Desktop App]")
                elif "youtube" in title_lower and not is_code_workspace:
                    youtube_active = True
                    running_applications.append(f"- {title} [Target: Browser Media Tab]")
                else:
                    # Clean catalog map for other windows
                    if not is_code_workspace:
                        running_applications.append(f"- {title}")

        except Exception:
            # Isolated fallback path
            pass
        # ─────────────────────────────────────────────────────────────────────

        os_vision_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n\n"
            f"[REAL-TIME SYSTEM EYE (OS AWARENESS)]\n"
            f"User Foreground Focus: \"{foreground_window}\"\n"
            f"All Open Applications & Active Web Tabs:\n"
            f"{chr(10).join(running_applications) if running_applications else '- None'}\n\n"
            f"[LIVE MEDIA TELEMETRY STATES]\n"
            f"- Spotify Application Open/Running: {spotify_active}\n"
            f"- YouTube Browser Content Present: {youtube_active}\n\n"
            f"CRITICAL CONTEXTUAL ROUTING RULES:\n"
            f"1. Spotify is your absolute default media application. When the user issues general playback or music controls ('play', 'pause', 'poz', 'resume', 'stop', 'music') WITHOUT naming a specific platform, you MUST always choose Spotify (computer_settings) and trigger 'spotify_play' or 'spotify_pause'.\n"
            f"2. NEVER auto-route generic commands to YouTube and NEVER ask 'Spotify or YouTube, sir?' regardless of the window telemetry states. Spotify is the strict baseline fallback.\n"
            f"3. ONLY route commands to youtube_video if the user explicitly specifies 'youtube', 'the video', 'clip', or 'watch'.\n"
            f"4. For track navigation commands ('next', 'skip', 'previous', 'back', 'next song', 'next track'):\n"
            f"   - You MUST call computer_settings with action='spotify_next' or action='spotify_previous'.\n"
            f"5. If the user says 'close this app' or 'minimize this', extract \"{foreground_window}\" as the target parameter for window_control.\n\n"
        )

        try:
            awareness_ctx = (
                "\n[PHASE-1 AWARENESS LAYER]\n"
                + format_window_context()
                + "\n\nRunning process snapshot:\n"
                + format_running_apps(limit=35)
                + "\n"
            )
            os_vision_ctx += awareness_ctx
        except Exception as _awareness_error:
            os_vision_ctx += f"\n[PHASE-1 AWARENESS LAYER]\nUnavailable: {_awareness_error}\n"

        parts = [os_vision_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
                    response_modalities=["AUDIO"], 
                    output_audio_transcription={},
                    input_audio_transcription={},
                    system_instruction="\n".join(parts),
                    tools=[{"function_declarations": TOOL_DECLARATIONS}],
                    session_resumption=types.SessionResumptionConfig(),
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name="Charon"
                            )
                        )
                    ),
                )
            
    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        original_name, original_args = name, dict(args)
        try:
            name, args = route_tool_call(name, args)
            if (name, args) != (original_name, original_args):
                print(f"[JARVIS] 🧭 routed {original_name} {original_args} → {name} {args}")
        except Exception as route_error:
            print(f"[JARVIS] ⚠️ routing layer skipped: {route_error}")

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # ----- HANDLE STANDBY TOOL -----
        if name == "enter_standby":
            self.enter_standby()
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "Entering standby mode. The assistant will now ignore all further commands until the wake word 'Jarvis' is heard. Do not call any other tools."}
            )                                                       
        # --------------------------------

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "netflix_control":
                r = await loop.run_in_executor(None, lambda: netflix_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "media_coordinator":
                r = await loop.run_in_executor(None, lambda: media_coordinator(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "window_control":
                r = await loop.run_in_executor(None, lambda: window_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        try:
            record_tool_result(name, args, result=result)
        except Exception as memory_error:
            print(f"[JARVIS] ⚠️ intent memory record failed: {memory_error}")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
                    with self._speaking_lock:
                        jarvis_speaking = self._is_speaking

                    # ----- MODIFIED: handle standby mode -----
                    if self._standby_active:
                        if self._wake_queue is not None:
                            data = indata.tobytes()
                            loop.call_soon_threadsafe(self._wake_queue.put_nowait, data)
                        return
                    # -----------------------------------------

                    # Normal operation: send audio to Gemini only if not muted
                    if not jarvis_speaking and not self.ui.muted:
                        data = indata.tobytes()
                        try:
                            # Thread-safe try-push without exploding if the loop lags
                            loop.call_soon_threadsafe(
                                lambda: self.out_queue.put_nowait({"data": data, "mime_type": "audio/pcm"}) 
                                if not self.out_queue.full() else None
                            )
                        except Exception:
                            pass
        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
            print("[JARVIS] 👂 Recv started")
            out_buf, in_buf = [], []

            try:
                while True:
                    async for response in self.session.receive():

                        if response.data:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            self.audio_in_queue.put_nowait(response.data)

                        if response.server_content:
                            sc = response.server_content

                            if sc.output_transcription and sc.output_transcription.text:
                                txt = _clean_transcript(sc.output_transcription.text)
                                if txt:
                                    out_buf.append(txt)

                            if sc.input_transcription and sc.input_transcription.text:
                                txt = _clean_transcript(sc.input_transcription.text)
                                if txt:
                                    in_buf.append(txt)

                            if sc.turn_complete:
                                if self._turn_done_event:
                                    self._turn_done_event.set()

                                full_in = " ".join(in_buf).strip()
                                if full_in:
                                    self.ui.write_log(f"You: {full_in}")
                                in_buf = []

                                full_out = " ".join(out_buf).strip()
                                if full_out:
                                    self.ui.write_log(f"Jarvis: {full_out}")
                                out_buf = []

                        # --- HANDLE TOOL CALLS ---
                        if response.tool_call and response.tool_call.function_calls:
                            fn_responses = []
                            standby_called = False

                            for fc in response.tool_call.function_calls:
                                # 1. Guard against any accidental trailing tool calls in the queue
                                if standby_called:
                                    print(f"[JARVIS] ⏭️ Skipping {fc.name} because enter_standby was already called.")
                                    continue

                                # 2. Check BEFORE execution if this is the standby command
                                if fc.name == "enter_standby":
                                    standby_called = True
                                    print(f"[JARVIS] 📞 {fc.name} (Standby intercepted, blocking remaining package execution)")
                                    
                                    fr = await self._execute_tool(fc)
                                    fn_responses.append(fr)
                                    break  # 👈 Immediately break out of the loop completely bypassing subsequent calls

                                print(f"[JARVIS] 📞 {fc.name}")
                                fr = await self._execute_tool(fc)
                                fn_responses.append(fr)

                            # Send tool responses if we gathered any
                            if fn_responses:
                                await self.session.send_tool_response(function_responses=fn_responses)

                            # === FIX: Instantly drop turn state if entering standby mode ===
                            if standby_called:
                                if self._turn_done_event:
                                    self._turn_done_event.set()
                                self.set_speaking(False)

            except Exception as e:
                print(f"[JARVIS] ❌ Recv: {e}")
                traceback.print_exc()
                raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=50)
                    self._turn_done_event = asyncio.Event()

                    print("[JARVIS] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")
                    self.speak("Jarvis online.")
                    

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()
            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)
from flask import Flask, request
import logging

# Initialize a micro-webserver for your local network
watch_bridge = Flask(__name__)

# Suppress annoying terminal logs for every ping
log_control = logging.getLogger('werkzeug')
log_control.setLevel(logging.ERROR)

# Global variable to reference your active JARVIS instance
active_jarvis_instance = None

@watch_bridge.route('/remote_voice', methods=['POST'])
def remote_voice_input():
    global active_jarvis_instance
    payload = request.get_json() or {}
    text_command = payload.get("command", "").strip()
    
    # === TEMPORARY DEBUG PRINTS ===
    print("--- BRIDGE TRIGGERED ---")
    print(f"Payload received: {payload}")
    print(f"Extracted Command: '{text_command}'")
    print(f"Is JARVIS engine online? {active_jarvis_instance is not None}")
    print("------------------------")
    
    if text_command and active_jarvis_instance:
        print(f"[REMOTE BRIDGE] 📱 Phone command received: '{text_command}'")
        
        # === FIX: Safe notification for standard incoming remote tasks ===
        if active_jarvis_instance._loop and active_jarvis_instance._loop.is_running():
            # Check if it's not a wake-up command to prevent duplicate notifications
            if not active_jarvis_instance._standby_active:
                asyncio.run_coroutine_threadsafe(
                    send_jarvis_notification_async("Remote Task Executed", f"Command: {text_command}"),
                    active_jarvis_instance._loop
                )
        
        active_jarvis_instance._on_text_command(text_command)
        return {"status": "success", "executed": text_command}, 200
        
    return {"status": "ignored", "reason": "Missing text or engine offline"}, 400

def launch_network_bridge():
    # Binds to port 5005 on your local Wi-Fi network
    watch_bridge.run(host='0.0.0.0', port=5005, debug=False, use_reloader=False)

import html
import re
import aiohttp
import asyncio

async def send_jarvis_notification_async(title: str, message: str):
    """
    Envoie une notification push gratuite via Telegram de manière asynchrone.
    """
    telegram_token = "8981625642:AAFdXbedkl6cfQ90QS3IKwNK79FcjLafeJo"
    chat_id = "1704700117"

    # Helper format definition to escape reserved markdown characters safely
    def escape_markdown(text: str) -> str:
        escape_chars = r'_*[]()~`>#+-=|{}.!'
        return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)

    # Clean the message text strings
    safe_title = escape_markdown(title)
    safe_message = escape_markdown(message)
    texte_final = f"*{safe_title}*\n\n{safe_message}"

    # Correct endpoint format containing /bot followed by the token
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": texte_final,
        "parse_mode": "MarkdownV2",
    }

    try:
        # Create an async client session with a timeout wrapper
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                
                if response.status == 200:
                    print("[JARVIS PUSH] Notification envoyée avec succès via Telegram.")
                else:
                    response_text = await response.text()
                    print(f"[JARVIS PUSH] Erreur Telegram Code : {response.status}")
                    print(f"[JARVIS PUSH] Détails : {response_text}")

    except asyncio.TimeoutError:
        print("[JARVIS PUSH] Erreur : Le délai d'attente (timeout) a expiré.")
    except Exception as e:
        print(f"[JARVIS PUSH] Erreur réseau lors de l'envoi : {e}")

# # Example of how to execute this inside your async event loop
# if __name__ == "__main__":
#     async def main():
#         await send_jarvis_notification_async(
#             "JARVIS Async Test: Success!", 
#             "Status: Online. Non-blocking network call complete."
#         )
#     asyncio.run(main())


def main():
    ui = JarvisUI("face.png")

    def runner():
        global active_jarvis_instance
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        active_jarvis_instance = jarvis  # Expose l'instance pour Flask
        
        # Démarre le serveur Flask sur un thread séparé
        import threading
        threading.Thread(target=launch_network_bridge, daemon=True).start()
        
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()

    ui.root.mainloop()

if __name__ == "__main__":
    main()