from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Card:
    id: str
    title: str
    text: str
    frame_id: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "frame_id": self.frame_id,
            "tags": self.tags,
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
