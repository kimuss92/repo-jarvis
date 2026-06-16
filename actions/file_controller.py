# actions/file_controller.py
import os
import shutil
import platform
import subprocess
import difflib
from core.utils import (
    get_desktop,
    get_downloads,
    get_documents,
    get_pictures,
    get_music,
    get_videos,
    is_safe_path,
    format_size,
    log,
)
from pathlib import Path
from datetime import datetime

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _resolve_path(raw: str) -> Path:
    shortcuts: dict[str, Path] = {
        "desktop": get_desktop(),
        "downloads": get_downloads(),
        "documents": get_documents(),
        "pictures": get_pictures(),
        "music": get_music(),
        "videos": get_videos(),
        "home": Path.home(),
    }
    lower = raw.strip().lower()
    if lower in shortcuts:
        return shortcuts[lower]
    return Path(raw).expanduser()

def _safe_trash(target: Path) -> str:
    if not _SEND2TRASH:
        return (
            "send2trash is not installed. "
            "Run: pip install send2trash — "
            "Permanent deletion is disabled for safety."
        )
    send2trash.send2trash(str(target))
    return f"Moved to Trash: {target.name}"


def list_files(path: str = "desktop", show_hidden: bool = False) -> str:
    try:
        target = _resolve_path(path)
        if not is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Path not found: {target}"
        if not target.is_dir():
            return f"Not a directory: {target}"

        items = []
        for item in sorted(target.iterdir()):
            if not show_hidden and item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                size = format_size(item.stat().st_size)
                items.append(f"📄 {item.name} ({size})")

        if not items:
            return f"Directory is empty: {target.name}/"

        return f"Contents of {target.name}/ ({len(items)} items):\n" + "\n".join(items)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Error listing files: {e}"


def create_file(path: str, name: str = "", content: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"File created: {target.name}"
    except Exception as e:
        return f"Could not create file: {e}"


def create_folder(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        target.mkdir(parents=True, exist_ok=True)
        return f"Folder created: {target.name}"
    except Exception as e:
        return f"Could not create folder: {e}"


def delete_file(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        protected = {
            get_desktop(), get_downloads(), get_documents(),
            get_pictures(), get_music(), get_videos(), Path.home()
        }
        if target.resolve() in {p.resolve() for p in protected}:
            return f"Protected directory, cannot delete: {target.name}"

        return _safe_trash(target)

    except PermissionError:
        return f"Permission denied: {path}"
    except Exception as e:
        return f"Could not delete: {e}"


def move_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base   = _resolve_path(path)
        src    = (base / name) if name else base
        dst    = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if not is_safe_path(src):
            return f"Access denied (source): {src}"
        if not is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir():
            dst = dst / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not move: {e}"


def copy_file(path: str, name: str = "", destination: str = "") -> str:
    try:
        base = _resolve_path(path)
        src  = (base / name) if name else base
        dst  = _resolve_path(destination) if destination else None

        if not src.exists():
            return f"Source not found: {src.name}"
        if dst is None:
            return "No destination specified."
        if not is_safe_path(src):
            return f"Access denied (source): {src}"
        if not is_safe_path(dst):
            return f"Access denied (destination): {dst}"

        if dst.is_dir():
            dst = dst / src.name

        dst.parent.mkdir(parents=True, exist_ok=True)

        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        return f"Copied: {src.name} → {dst.parent.name}/"

    except Exception as e:
        return f"Could not copy: {e}"


def rename_file(path: str, name: str = "", new_name: str = "") -> str:
    try:
        base     = _resolve_path(path)
        target   = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"
        if not new_name:
            return "No new name provided."

        new_path = target.parent / new_name
        if new_path.exists():
            return f"A file named '{new_name}' already exists here."

        target.rename(new_path)
        return f"Renamed: {target.name} → {new_name}"

    except Exception as e:
        return f"Could not rename: {e}"


def read_file(path: str, name: str = "", max_chars: int = 4000) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"File not found: {target.name}"
        if not target.is_file():
            return f"Not a file: {target.name}"

        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[Truncated — {len(content)} total chars]"
        return content

    except Exception as e:
        return f"Could not read file: {e}"


def write_file(path: str, name: str = "", content: str = "",
               append: bool = False) -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content)
        action = "Appended to" if append else "Written to"
        return f"{action}: {target.name}"
    except Exception as e:
        return f"Could not write file: {e}"


def find_files(name: str = "", extension: str = "",
               path: str = "home", max_results: int = 20) -> str:
    def _match(file_path: Path) -> bool:
        if extension and file_path.suffix.lower() != extension.lower():
            return False
        if name and name.lower() not in file_path.name.lower():
            return False
        return True

    def _search_roots(roots: list[Path], hard_limit: int) -> list[str]:
        results: list[str] = []
        seen_files: set[Path] = set()

        for root in roots:
            if len(results) >= hard_limit:
                break
            if not root.exists() or not root.is_dir():
                continue

            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    try:
                        dirpath_path = Path(dirpath)
                    except Exception:
                        dirpath_path = None

                    if dirpath_path is not None and not is_safe_path(dirpath_path):
                        continue

                    for fname in filenames:
                        if len(results) >= hard_limit:
                            break
                        try:
                            p = Path(dirpath) / fname
                            if p in seen_files:
                                continue
                            if not p.is_file():
                                continue
                            if not _match(p):
                                continue
                            size = format_size(p.stat().st_size)
                            results.append(f"📄 {p.name} ({size}) — {p.parent}")
                            seen_files.add(p)
                        except PermissionError:
                            continue
                        except Exception:
                            continue

            except PermissionError:
                continue
            except Exception:
                continue

        return results

    try:
        search_path = _resolve_path(path)
        if not is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Search path not found: {path}"

        hard_limit = min(int(max_results), 50)
        initial_results = _search_roots([search_path], hard_limit=hard_limit)

        if initial_results:
            return f"Found {len(initial_results)} file(s):\n" + "\n".join(initial_results)

        common_roots: list[Path] = [
            get_desktop(),
            get_documents(),
            get_downloads(),
        ]
        common_roots = [r for r in common_roots if r.exists() and r.is_dir() and is_safe_path(r)]

        fallback_results = _search_roots(common_roots, hard_limit=hard_limit)

        if fallback_results:
            return f"Found {len(fallback_results)} file(s) in common folders:\n" + "\n".join(fallback_results)

        query = name or extension or "files"
        return f"No {query} found in {search_path.name}/"

    except Exception as e:
        return f"Search error: {e}"


def get_largest_files(path: str = "downloads", count: int = 10) -> str:
    count = min(count, 50)  
    try:
        search_path = _resolve_path(path)
        if not is_safe_path(search_path):
            return f"Access denied: {search_path}"
        if not search_path.exists():
            return f"Path not found: {path}"

        files = []
        for item in search_path.rglob("*"):
            if item.is_file():
                try:
                    files.append((item.stat().st_size, item))
                except Exception:
                    continue

        files.sort(reverse=True)
        top = files[:count]

        if not top:
            return "No files found."

        lines = [f"Top {len(top)} largest files in {search_path.name}/:"]
        for size, f in top:
            lines.append(f"  {format_size(size):>10}  {f.name}  ({f.parent})")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {e}"


def get_disk_usage(path: str = "home") -> str:
    try:
        target = _resolve_path(path)
        usage  = shutil.disk_usage(target)
        pct    = usage.used / usage.total * 100
        return (
            f"Disk usage ({target}):\n"
            f"  Total : {format_size(usage.total)}\n"
            f"  Used  : {format_size(usage.used)} ({pct:.1f}%)\n"
            f"  Free  : {format_size(usage.free)}"
        )
    except Exception as e:
        return f"Could not get disk usage: {e}"


def organize_desktop() -> str:
    type_map = {
        "Images":    {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
        "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx",
                      ".ppt", ".pptx", ".csv", ".odt", ".ods", ".odp"},
        "Videos":    {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
        "Music":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
        "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
        "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                      ".cpp", ".java", ".cs", ".go", ".rs", ".sh"},
    }

    desktop = get_desktop()
    moved, skipped = [], []

    try:
        for item in desktop.iterdir():
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.name in {k for k in type_map}:
                continue

            ext        = item.suffix.lower()
            target_dir = desktop / "Others"
            for folder, exts in type_map.items():
                if ext in exts:
                    target_dir = desktop / folder
                    break

            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name

            if new_path.exists():
                skipped.append(item.name)
                continue

            shutil.move(str(item), str(new_path))
            moved.append(f"{item.name} → {target_dir.name}/")

        result = f"Desktop organized: {len(moved)} files moved."
        if moved:
            preview = moved[:8]
            result += "\n" + "\n".join(preview)
            if len(moved) > 8:
                result += f"\n... and {len(moved) - 8} more."
        if skipped:
            result += f"\n{len(skipped)} file(s) skipped (name conflict)."
        return result

    except Exception as e:
        return f"Could not organize desktop: {e}"


def get_file_info(path: str, name: str = "") -> str:
    try:
        base   = _resolve_path(path)
        target = (base / name) if name else base
        if not is_safe_path(target):
            return f"Access denied: {target}"
        if not target.exists():
            return f"Not found: {target.name}"

        stat = target.stat()
        info = {
            "Name":      target.name,
            "Type":      "Folder" if target.is_dir() else "File",
            "Size":      format_size(stat.st_size),
            "Location":  str(target.parent),
            "Created":   datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            "Modified":  datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "Extension": target.suffix or "—",
        }
        return "\n".join(f"  {k}: {v}" for k, v in info.items())

    except Exception as e:
        return f"Could not get file info: {e}"


def open_file_or_folder(path: str, name: str = "") -> str:
    try:
        base = _resolve_path(path)
        target = (base / name) if name else base

        if not target.exists() and name:
            print(f"[file_controller] Target not found natively. Launching global indexer fuzzy matching for: '{name}'")
            indexed_paths = []
            
            if _OS == "Windows":
                try:
                    import win32com.client
                    import pythoncom
                    # ✅ CORRECT WIN32 API CALL: Initializes the COM thread apartment properly
                    pythoncom.CoInitialize()
                    
                    conn = win32com.client.Dispatch("ADODB.Connection")
                    rs = win32com.client.Dispatch("ADODB.Recordset")
                    conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
                    
                    search_token = name.replace(" ", "")
                    query = f"SELECT System.ItemPathDisplay FROM SystemIndex WHERE System.ItemName LIKE '%{search_token}%'"
                    
                    if len(name.split()) > 1:
                        first_tok = name.split()[0]
                        if len(first_tok) > 2:
                            query += f" OR System.ItemName LIKE '%{first_tok}%'"
                            
                    rs.Open(query, conn)
                    while not rs.EOF:
                        p_str = rs.Fields.Item("System.ItemPathDisplay").Value
                        if p_str:
                            p_obj = Path(p_str)
                            if p_obj.exists() and is_safe_path(p_obj):
                                indexed_paths.append(p_obj)
                        rs.MoveNext()
                    rs.Close()
                    conn.Close()
                except Exception as e:
                    print(f"[file_controller] Indexer system call skipped: {e}")

            if not indexed_paths:
                quick_roots = [get_desktop(), get_downloads(), get_documents(), Path.home()]
                for root in quick_roots:
                    try:
                        for item in root.iterdir():
                            indexed_paths.append(item)
                    except Exception:
                        continue

            if indexed_paths:
                scored_results = []
                target_clean = name.replace(" ", "").lower()
                
                for p in indexed_paths:
                    p_clean = p.name.replace(" ", "").lower()
                    ratio = difflib.SequenceMatcher(None, target_clean, p_clean).ratio()
                    scored_results.append((ratio, p))
                
                scored_results.sort(key=lambda x: x[0], reverse=True)
                if scored_results and scored_results[0][0] > 0.4:
                    target = scored_results[0][1]

        if not target.exists():
            return f"Sir, I could not locate any file or folder matching '{name}' anywhere inside your system layout indexes."

        if not is_safe_path(target):
            return f"Access denied for safety reasons: {target}"

        if _OS == "Windows":
            if target.is_file():
                subprocess.run(['explorer', '/select,', str(target)])
                return f"Opened folder and highlighted file: {target.name}"
            else:
                os.startfile(str(target))
                return f"Opened folder: {target.name}"
        elif _OS == "Darwin": 
            if target.is_file():
                subprocess.run(["open", "-R", str(target)])
                return f"Opened folder and highlighted file: {target.name}"
            else:
                subprocess.run(["open", str(target)])
                return f"Opened folder: {target.name}"
        else: 
            target_dir = str(target.parent if target.is_file() else target)
            subprocess.run(["xdg-open", target_dir])
            return f"Opened directory containing: {target.name}"

    except Exception as e:
        return f"Failed to open path: {e}"


# ── ✅ NEW SYSTEM ACTION: OPEN LAST COMPLETED DOWNLOAD ────────────────────────
def open_last_download() -> str:
    """Queries your system Downloads folder and reveals the absolute latest file."""
    try:
        dl_dir = get_downloads()
        if not dl_dir.exists() or not dl_dir.is_dir():
            return "Sir, your Downloads directory could not be resolved safely."
        
        # Pull valid files while ignoring dynamic/unfinished web browser chunks
        files = [f for f in dl_dir.iterdir() if f.is_file() and not f.name.endswith(('.tmp', '.crdownload', '.part'))]
        
        if not files:
            # Fallback to absolute file mapping if completely empty or custom engine named
            files = [f for f in dl_dir.iterdir() if f.is_file()]
            
        if not files:
            return "Sir, your Downloads folder is currently empty."
            
        # Sort files by real-time modification timestamp descending
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        latest_file = files[0]
        
        return open_file_or_folder(str(dl_dir), name=latest_file.name)
    except Exception as e:
        return f"Failed to track down last download: {e}"


def file_controller(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    path   = params.get("path", "desktop")
    name   = params.get("name", "")

    if player:
        player.write_log(f"[file] {action} {name or path}")

    try:
        if action == "list":
            return list_files(path)
        elif action == "create_file":
            return create_file(path, name=name, content=params.get("content", ""))
        elif action == "create_folder":
            return create_folder(path, name=name)
        elif action == "delete":
            return delete_file(path, name=name)
        elif action == "move":
            return move_file(path, name=name, destination=params.get("destination", ""))
        elif action == "copy":
            return copy_file(path, name=name, destination=params.get("destination", ""))
        elif action == "rename":
            return rename_file(path, name=name, new_name=params.get("new_name", ""))
        elif action == "read":
            return read_file(path, name=name)
        elif action == "write":
            return write_file(path, name=name, content=params.get("content", ""), append=params.get("append", False))
        elif action == "find":
            return find_files(name=name or params.get("name", ""), extension=params.get("extension", ""), path=path, max_results=min(int(params.get("max_results", 20)), 50))
        elif action == "open":
            return open_file_or_folder(path, name=name)
        # ✅ REGISTER NEW ROUTE ACTION
        elif action in ("last_download", "open_last_download"):
            return open_last_download()
        elif action == "largest":
            return get_largest_files(path=path, count=int(params.get("count", 10)))
        elif action == "disk_usage":
            return get_disk_usage(path)
        elif action == "organize_desktop":
            return organize_desktop()
        elif action == "info":
            return get_file_info(path, name=name)
        else:
            return f"Unknown action: '{action}'"
    except Exception as e:
        return f"File controller error ({action}): {e}"