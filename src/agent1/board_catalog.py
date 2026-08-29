"""Read-only view of the Agent1-managed board catalog.

The catalog is a folder (default ``data/boards/``) containing one
``<catalog_id>.json`` file per available board. Agent1 owns this folder:
it writes seeds here as it makes progress on a plan. The Kanban app
reads it but never writes to it — the user's edits land in a separate
working copy so the catalog stays pristine.

A catalog entry's ``display_name`` is read from inside the JSON
(``{"name": "...", "frames": [...], "cards": [...]}``) when present, and
falls back to the catalog id (the filename without ``.json``) if the
file is empty or has no ``name`` field.

This module is deliberately read-only and stateless beyond an mtime
cache, so it can be called on every request without disk I/O dominating.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional


# Catalog id is the filename without ``.json``. Keep it conservative
# so it round-trips through the URL and the on-disk file path safely.
_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BoardCatalog:
    """A folder of seed ``.json`` files Agent1 maintains."""

    def __init__(self, folder: str) -> None:
        self._folder = folder
        # Tiny in-memory cache: folder mtime -> entries. Avoids re-reading
        # every .json on every UI refresh. The folder is Agent1's, so a
        # change to its mtime is a good enough invalidation signal.
        self._cache_mtime: Optional[float] = None
        self._cache: List[Dict[str, str]] = []

    @property
    def folder(self) -> str:
        return self._folder

    # ---- public API --------------------------------------------------------
    def list(self) -> List[Dict[str, str]]:
        """Return ``[{id, name, source_path}, ...]``, sorted by id.

        Silently skips files whose name is not a valid catalog id or
        whose JSON cannot be parsed. Agent1 is the only writer, so a
        parse failure here means the seed is mid-write — the next
        refresh will pick it up.
        """
        try:
            mtime = os.path.getmtime(self._folder)
        except OSError:
            return []
        if self._cache_mtime == mtime and self._cache:
            return list(self._cache)
        entries: List[Dict[str, str]] = []
        try:
            names = sorted(os.listdir(self._folder))
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".json"):
                continue
            cid = name[: -len(".json")]
            if not _VALID_ID.match(cid):
                continue
            path = os.path.join(self._folder, name)
            if not os.path.isfile(path):
                continue
            display = self._read_display_name(path) or cid
            entries.append(
                {"id": cid, "name": display, "source_path": path}
            )
        self._cache = entries
        self._cache_mtime = mtime
        return list(entries)

    def get(self, catalog_id: str) -> Optional[Dict[str, str]]:
        """Return the entry for ``catalog_id`` or ``None``."""
        for entry in self.list():
            if entry["id"] == catalog_id:
                return entry
        return None

    def path_for(self, catalog_id: str) -> str:
        """Return the seed file path for ``catalog_id`` (no existence check)."""
        if not _VALID_ID.match(catalog_id):
            raise ValueError(f"invalid catalog id: {catalog_id!r}")
        return os.path.join(self._folder, f"{catalog_id}.json")

    # ---- internals ---------------------------------------------------------
    @staticmethod
    def _read_display_name(path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                # Cap the read so a huge seed doesn't dominate the request.
                raw = fh.read(64 * 1024)
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        if isinstance(name, str):
            stripped = name.strip()
            if stripped:
                return stripped[:100]
        return None
