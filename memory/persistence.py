from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryPersistence:
    """Append-only JSON storage for JARVIS long-term memory."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        home = Path(os.environ.get("APPDATA") or Path.cwd())
        self.base_dir = Path(base_dir) if base_dir else (home / "JARVIS_MEMORY")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.mem_path = self.base_dir / "jarvis_memory.json"
        self._lock = asyncio.Lock()


    async def _ensure_file(self) -> None:
        if not self.mem_path.exists():
            initial = {"profile_version": 1, "updated_at": 0.0, "events": []}
            self.mem_path.write_text(json.dumps(initial, indent=2), encoding="utf-8")


    async def append_event(self, event: Dict[str, Any]) -> None:
        async with self._lock:
            await self._ensure_file()
            data = json.loads(self.mem_path.read_text(encoding="utf-8"))
            events: List[Dict[str, Any]] = data.get("events", [])
            record = {
                "id": str(uuid.uuid4()),
                "ts": time.time(),
                **event,
            }
            events.append(record)
            # Bound size for performance
            if len(events) > 5000:
                events = events[-5000:]
            data["events"] = events
            data["updated_at"] = time.time()
            self.mem_path.write_text(json.dumps(data, indent=2), encoding="utf-8")



    async def get_profile(self) -> Dict[str, Any]:
        async with self._lock:
            await self._ensure_file()
            return json.loads(self.mem_path.read_text(encoding="utf-8"))



    async def background_compactor(self, interval_s: float = 900.0) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.get_profile()
            except Exception:
                pass
