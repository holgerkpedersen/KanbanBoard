"""Active-board pointer for the Kanban app.

The Kanban app is single-user. The "catalog" of available boards lives
in a folder Agent1 maintains (default ``data/boards/``). The user picks
one catalog board, the app copies the seed to a per-board working file
(default ``data/board-<catalog_id>.json``) and loads that into memory.

What this module persists is just **which catalog id is currently
active** so the app can re-open the right working copy after a
restart. There is no user-managed list of boards anymore — the
catalog is the single source of truth and Agent1 owns it.

Atomic-write semantics: write to a ``.tmp`` file, then ``os.replace``
into place, so a crash mid-write cannot leave a half-written pointer
on disk.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Optional


def _atomic_write_json(path: str, data: object) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class BoardRegistry:
    """Trivial active-id pointer (atomic-write JSON)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
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
            # Corrupt pointer: start with no active board rather than
            # crash the app.
            return
        if not isinstance(data, dict):
            return
        active = data.get("active")
        if isinstance(active, str) and active:
            self._active = active

    def _save(self) -> None:
        _atomic_write_json(self._path, {"active": self._active})

    # ---- public API --------------------------------------------------------
    def get_active(self) -> Optional[str]:
        """Return the active catalog id, or ``None``."""
        with self._lock:
            return self._active

    def set_active(self, catalog_id: str) -> None:
        with self._lock:
            if not isinstance(catalog_id, str) or not catalog_id:
                raise ValueError("catalog_id must be a non-empty string")
            self._active = catalog_id
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._active = None
            self._save()
