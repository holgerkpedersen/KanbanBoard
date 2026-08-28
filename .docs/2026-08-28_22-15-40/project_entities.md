```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, TypedDict

class CardDict(TypedDict):
    id: str
    title: str
    description: str
    tags: List[str]

class FrameDict(TypedDict):
    id: str
    title: str
    card_ids: List[str]

class BoardStateDict(TypedDict):
    frames: List[FrameDict]
    cards: Dict[str, CardDict]

@dataclass
class Card:
    id: str
    title: str
    description: str
    tags: List[str]

    def to_dict(self) -> CardDict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
        }

@dataclass
class Frame:
    id: str
    title: str
    card_ids: List[str]

    def to_dict(self) -> FrameDict:
        return {
            "id": self.id,
            "title": self.title,
            "card_ids": self.card_ids,
        }
```
