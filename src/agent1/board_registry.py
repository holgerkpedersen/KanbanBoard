"""Persistent registry of board files + which one is currently active.

The registry is itself a small JSON file (default ``data/boards.json``)
that tracks a list of known board files (by absolute path) and an
``active`` id pointing at the one currently loaded in memory. It is the
single source of truth for "which board is open" and survives process
restarts.

Atomic-write semantics: same as :class:`BoardStore` — write to a ``.tmp``
file, then ``os.replace`` into place, so a crash mid-write cannot leave a
half-written registry on disk.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional


def _atomic_write_json(path: str, data: object) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return secrets.token_hex(4)  # 8 hex chars


class BoardRegistry:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._boards: Dict[str, Dict[str, str]] = {}
        self._active: Optional[str] = None
        self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            # Corrupt registry: start clean rather than crash the app.
            return
        if not isinstance(data, dict):
            return
        for entry in data.get("boards", []) or []:
            if not isinstance(entry, dict):
                continue
            bid = entry.get("id")
            path = entry.get("path")
            name = entry.get("name") or ""
            last_opened = entry.get("last_opened") or ""
            if not isinstance(bid, str) or not isinstance(path, str):
                continue
            self._boards[bid] = {
                "id": bid,
                "name": name,
                "path": path,
                "last_opened": last_opened,
            }
        active = data.get("active")
        if isinstance(active, str) and active in self._boards:
            self._active = active

    def _save(self) -> None:
        _atomic_write_json(
            self._path,
            {
                "boards": list(self._boards.values()),
                "active": self._active,
            },
        )

    # ---- public API --------------------------------------------------------
    def list(self) -> List[Dict[str, str]]:
        with self._lock:
            return list(self._boards.values())

    def get(self, board_id: str) -> Optional[Dict[str, str]]:
        with self._lock:
            entry = self._boards.get(board_id)
            return dict(entry) if entry is not None else None

    def get_active(self) -> Optional[Dict[str, str]]:
        with self._lock:
            if self._active is None:
                return None
            entry = self._boards.get(self._active)
            return dict(entry) if entry is not None else None

    def add(self, name: str, path: str) -> Dict[str, str]:
        with self._lock:
            bid = _new_id()
            while bid in self._boards:
                bid = _new_id()
            entry = {
                "id": bid,
                "name": name,
                "path": path,
                "last_opened": _now_iso(),
            }
            self._boards[bid] = entry
            self._save()
            return dict(entry)

    def remove(self, board_id: str) -> None:
        with self._lock:
            self._boards.pop(board_id, None)
            if self._active == board_id:
                self._active = None
            self._save()

    def rename(self, board_id: str, name: str) -> Dict[str, str]:
        with self._lock:
            entry = self._boards[board_id]
            entry["name"] = name
            self._save()
            return dict(entry)

    def set_active(self, board_id: str) -> Dict[str, str]:
        with self._lock:
            if board_id not in self._boards:
                raise KeyError(board_id)
            self._active = board_id
            self._boards[board_id]["last_opened"] = _now_iso()
            self._save()
            return dict(self._boards[board_id])

    def ensure_default(self, name: str, path: str) -> Dict[str, str]:
        """First-run helper: if the registry is empty, register ``path``
        and mark it active. Returns the active entry."""
        with self._lock:
            if self._active is not None and self._active in self._boards:
                return dict(self._boards[self._active])
            # Also dedupe by path: if a board with this path already
            # exists (e.g. the user re-pointed the same data file), reuse
            # it instead of creating a duplicate.
            for existing in self._boards.values():
                if existing["path"] == path:
                    self._active = existing["id"]
                    existing["last_opened"] = _now_iso()
                    self._save()
                    return dict(existing)
            entry = self.add(name, path)
            self._active = entry["id"]
            self._save()
            return dict(entry)
