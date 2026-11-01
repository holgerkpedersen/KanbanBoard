import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import src.agent1.store as store_mod
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


# ---------------------------------------------------------------------------
# Atomic write: transient Windows sharing violations must be retried
# ---------------------------------------------------------------------------


def _transient_sharing_violation(path) -> OSError:
    """A transient Windows sharing violation (WinError 32)."""
    return OSError(
        32,
        "The process cannot access the file because of other opened handles",
        str(path),
    )


def test_store_atomic_write_retries_transient_windows_sharing_violation(
    tmp_path, monkeypatch
):
    """Regression: a transient ``PermissionError: [WinError 32]`` on the atomic
    rename must be retried instead of failing the board mutation - which is what
    used to dead-letter every mutating Kanban sync message.
    """
    path = tmp_path / "board.json"
    s = BoardStore(path=str(path))
    s.add_frame(Frame(id="f1", title="X", card_ids=[]))

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _transient_sharing_violation(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(store_mod.os, "replace", flaky_replace)
    monkeypatch.setattr(store_mod.time, "sleep", lambda *_: None)

    s.add_frame(Frame(id="f2", title="Y", card_ids=[]))

    assert calls["n"] == 2, "the transient violation must be retried exactly once"
    assert path.exists()
    assert s.get_frame("f2") is not None


def test_store_atomic_write_gives_each_write_a_unique_tmp_sidecar(
    tmp_path, monkeypatch
):
    """The temp sidecar must be unique per write so concurrent writers cannot
    clobber each other's in-flight file.
    """
    path = tmp_path / "board.json"
    s = BoardStore(path=str(path))
    s.add_frame(Frame(id="f1", title="X", card_ids=[]))

    real_replace = os.replace
    seen_tmp_names: list = []

    def recording_replace(src, dst):
        seen_tmp_names.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(store_mod.os, "replace", recording_replace)

    s.add_frame(Frame(id="f2", title="Y", card_ids=[]))
    s.add_frame(Frame(id="f3", title="Z", card_ids=[]))

    assert len(seen_tmp_names) == 2, "one tmp sidecar per write, no shared fixed name"
    assert len({os.path.basename(n) for n in seen_tmp_names}) == 2, (
        "each write must use its own tmp sidecar"
    )
    assert list(tmp_path.glob("*.tmp")) == [], "no tmp sidecar may be left behind"

