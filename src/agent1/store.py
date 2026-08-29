import json
import os
import threading
from typing import Dict, List, Optional

from .models import Card, Frame


class BoardStore:
    """In-memory board store with optional JSON-file persistence.

    When ``path`` is provided the store loads its state from that file on
    startup and writes it back (atomically) after every mutation, so frames
    and cards survive process restarts. When ``path`` is ``None`` the store
    is purely in-memory (used by the test suite).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._frames: Dict[str, Frame] = {}
        self._cards: Dict[str, Card] = {}
        self._frame_order: List[str] = []
        self._path = path
        if path:
            self._load()

    # ---- persistence -------------------------------------------------------
    def _snapshot(self) -> Dict[str, object]:
        return {
            "frames": [
                self._frames[fid].to_dict()
                for fid in self._frame_order
                if fid in self._frames
            ],
            "cards": [c.to_dict() for c in self._cards.values()],
        }

    def _save(self) -> None:
        if not self._path:
            return
        data = self._snapshot()
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def _load(self) -> None:
        assert self._path is not None
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            # Corrupt or unreadable file: start from a clean board rather
            # than crashing the whole app.
            return

        self._frames = {}
        self._cards = {}
        self._frame_order = []
        for f in data.get("frames", []):
            frame = Frame(
                id=f["id"],
                title=f["title"],
                card_ids=list(f.get("card_ids", [])),
            )
            self._frames[frame.id] = frame
            self._frame_order.append(frame.id)
        for c in data.get("cards", []):
            card = Card(
                id=c["id"],
                title=c["title"],
                text=c.get("text", ""),
                frame_id=c["frame_id"],
                tags=list(c.get("tags", [])),
                system=c.get("system", ""),
            )
            self._cards[card.id] = card

        # Reconcile frame membership with the actual cards so the two views
        # can never disagree (e.g. after a partially-written file).
        for frame in self._frames.values():
            frame.card_ids = [cid for cid in frame.card_ids if cid in self._cards]
        for card in self._cards.values():
            frame = self._frames.get(card.frame_id)
            if frame and card.id not in frame.card_ids:
                frame.card_ids.append(card.id)

    # ---- frames ------------------------------------------------------------
    def add_frame(self, frame: Frame) -> None:
        with self._lock:
            if frame.id not in self._frames:
                self._frame_order.append(frame.id)
            self._frames[frame.id] = frame
            self._save()

    def reorder_frames(self, order: List[str]) -> None:
        with self._lock:
            if all(fid in self._frames for fid in order):
                self._frame_order = list(order)
            self._save()

    def get_frame(self, frame_id: str) -> Optional[Frame]:
        with self._lock:
            return self._frames.get(frame_id)

    def get_all_frames(self) -> List[Frame]:
        with self._lock:
            return [self._frames[fid] for fid in self._frame_order if fid in self._frames]

    def update_frame(self, frame: Frame) -> None:
        with self._lock:
            if frame.id in self._frames:
                self._frames[frame.id] = frame
                self._save()

    def delete_frame(self, frame_id: str) -> None:
        with self._lock:
            frame = self._frames.pop(frame_id, None)
            if frame:
                for card_id in frame.card_ids:
                    self._cards.pop(card_id, None)
                if frame_id in self._frame_order:
                    self._frame_order.remove(frame_id)
            self._save()

    # ---- cards -------------------------------------------------------------
    def add_card(self, card: Card, frame_id: str) -> None:
        with self._lock:
            self._cards[card.id] = card
            frame = self._frames.get(frame_id)
            if frame and card.id not in frame.card_ids:
                frame.card_ids.append(card.id)
            self._save()

    def get_card(self, card_id: str) -> Optional[Card]:
        with self._lock:
            return self._cards.get(card_id)

    def get_all_cards(self) -> List[Card]:
        with self._lock:
            return list(self._cards.values())

    def move_card(
        self, card_id: str, to_frame_id: str, index: Optional[int] = None
    ) -> bool:
        with self._lock:
            card = self._cards.get(card_id)
            if card is None:
                return False
            target = self._frames.get(to_frame_id)
            if target is None:
                return False
            source = self._frames.get(card.frame_id)
            if source is not None and card.id in source.card_ids:
                source.card_ids.remove(card.id)
            if index is None or index < 0 or index > len(target.card_ids):
                target.card_ids.append(card.id)
            else:
                target.card_ids.insert(index, card.id)
            card.frame_id = to_frame_id
            self._save()
            return True

    def get_cards_in_frame(self, frame_id: str) -> List[Card]:
        with self._lock:
            frame = self._frames.get(frame_id)
            if not frame:
                return []
            return [self._cards[cid] for cid in frame.card_ids if cid in self._cards]

    def update_card(self, card: Card) -> None:
        with self._lock:
            if card.id in self._cards:
                self._cards[card.id] = card
                self._save()

    def delete_card(self, card_id: str) -> None:
        with self._lock:
            self._cards.pop(card_id, None)
            for frame in self._frames.values():
                if card_id in frame.card_ids:
                    frame.card_ids.remove(card_id)
            self._save()
