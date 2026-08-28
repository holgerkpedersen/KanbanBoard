import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1.models import Card, Frame
from src.agent1.store import BoardStore


def test_store_persists_frames_and_cards_across_reopen(tmp_path):
    path = tmp_path / "board.json"

    # First instance: create frames + cards, exercising reorder/move.
    s1 = BoardStore(path=str(path))
    f1 = Frame(id="f1", title="To Do", card_ids=[])
    f2 = Frame(id="f2", title="Done", card_ids=[])
    s1.add_frame(f1)
    s1.add_frame(f2)
    s1.reorder_frames(["f2", "f1"])  # reorder: Done before To Do

    c1 = Card(id="c1", title="Write code", text="", frame_id="f1", tags=["dev"])
    c2 = Card(id="c2", title="Ship", text="", frame_id="f1", tags=[])
    s1.add_card(c1, "f1")
    s1.add_card(c2, "f1")
    s1.move_card("c1", "f1", index=1)  # reorder within frame: c2, c1

    # File must now exist and contain the data.
    assert path.exists()

    # Second instance: reopen from the same file, no writes yet.
    s2 = BoardStore(path=str(path))
    frames = s2.get_all_frames()
    assert [f.id for f in frames] == ["f2", "f1"]
    assert frames[1].title == "To Do"

    card_ids_in_f1 = [c.id for c in s2.get_cards_in_frame("f1")]
    assert card_ids_in_f1 == ["c2", "c1"]
    assert s2.get_card("c1").tags == ["dev"]


def test_store_survives_restart_after_delete(tmp_path):
    path = tmp_path / "board.json"

    s1 = BoardStore(path=str(path))
    f = Frame(id="f1", title="X", card_ids=[])
    s1.add_frame(f)
    c = Card(id="c1", title="A", text="", frame_id="f1", tags=[])
    s1.add_card(c, "f1")
    s1.delete_card("c1")
    s1.delete_frame("f1")

    s2 = BoardStore(path=str(path))
    assert s2.get_frame("f1") is None
    assert s2.get_card("c1") is None
    assert s2.get_all_frames() == []


def test_store_handles_missing_file_gracefully(tmp_path):
    path = tmp_path / "does-not-exist-yet.json"
    s = BoardStore(path=str(path))
    assert s.get_all_frames() == []
    # A write creates the file lazily.
    s.add_frame(Frame(id="f1", title="Fresh", card_ids=[]))
    assert path.exists()


def test_store_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "board.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    # Should not raise; starts from a clean board.
    s = BoardStore(path=str(path))
    assert s.get_all_frames() == []


def test_frame_title_update_is_persisted(tmp_path):
    path = tmp_path / "board.json"

    s1 = BoardStore(path=str(path))
    f = Frame(id="f1", title="Old", card_ids=[])
    s1.add_frame(f)

    # Mutate the in-memory frame and persist the change.
    f.title = "New"
    s1.update_frame(f)

    s2 = BoardStore(path=str(path))
    assert s2.get_frame("f1").title == "New"

