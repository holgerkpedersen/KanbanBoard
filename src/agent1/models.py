from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string (second precision).

    Stored verbatim on cards and board files; the frontend converts to
    browser-local calendar days for date-range filtering.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Card:
    id: str
    title: str
    text: str
    frame_id: str
    tags: List[str] = field(default_factory=list)
    system: str = ""
    # ISO 8601 UTC timestamps. Empty string means "unknown" (cards created
    # before timestamps existed). A move counts as an update.
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "frame_id": self.frame_id,
            "tags": self.tags,
            "system": self.system,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Frame:
    id: str
    title: str
    card_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "card_ids": self.card_ids,
        }
