"""Tests for the persistent board-registry object."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1.board_registry import BoardRegistry


def test_registry_add_and_list(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "boards.json"))
    a = reg.add("Project A", str(tmp_path / "a.json"))
    b = reg.add("Project B", str(tmp_path / "b.json"))
    listed = reg.list()
    assert {e["id"] for e in listed} == {a["id"], b["id"]}
    assert {e["name"] for e in listed} == {"Project A", "Project B"}


def test_registry_duplicate_names_allowed_with_unique_ids(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "boards.json"))
    a = reg.add("Same", str(tmp_path / "a.json"))
    b = reg.add("Same", str(tmp_path / "b.json"))
    assert a["id"] != b["id"]


def test_registry_set_active_updates_last_opened_and_is_persisted(tmp_path):
    path = tmp_path / "boards.json"
    reg = BoardRegistry(path=str(path))
    a = reg.add("A", str(tmp_path / "a.json"))
    reg.set_active(a["id"])
    first_ts = reg.get_active()["last_opened"]
    assert first_ts  # non-empty ISO timestamp

    # Reopen: state survives.
    reg2 = BoardRegistry(path=str(path))
    assert reg2.get_active()["id"] == a["id"]


def test_registry_remove_clears_active_and_persists(tmp_path):
    path = tmp_path / "boards.json"
    reg = BoardRegistry(path=str(path))
    a = reg.add("A", str(tmp_path / "a.json"))
    reg.set_active(a["id"])
    reg.remove(a["id"])
    assert reg.get_active() is None
    assert reg.list() == []
    reg2 = BoardRegistry(path=str(path))
    assert reg2.get_active() is None
    assert reg2.list() == []


def test_registry_rename(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "boards.json"))
    a = reg.add("Old", str(tmp_path / "a.json"))
    reg.rename(a["id"], "New")
    assert reg.get(a["id"])["name"] == "New"


def test_registry_set_active_unknown_id_raises(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "boards.json"))
    with pytest.raises(KeyError):
        reg.set_active("nope")


def test_registry_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "boards.json"
    path.write_text("{ not json", encoding="utf-8")
    reg = BoardRegistry(path=str(path))
    assert reg.list() == []
    assert reg.get_active() is None
    # A subsequent add still works.
    a = reg.add("X", str(tmp_path / "x.json"))
    assert a["name"] == "X"


def test_registry_ensure_default_registers_and_activates_on_first_run(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "boards.json"))
    entry = reg.ensure_default("My Board", str(tmp_path / "board.json"))
    assert reg.get_active()["id"] == entry["id"]
    assert reg.get_active()["name"] == "My Board"


def test_registry_ensure_default_reuses_existing_path(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "boards.json"))
    p = str(tmp_path / "board.json")
    a = reg.add("Original", p)
    reg.set_active(a["id"])
    # Second registry, same path -> ensure_default should not duplicate.
    reg2 = BoardRegistry(path=str(tmp_path / "boards.json"))
    entry = reg2.ensure_default("Different Name", p)
    assert entry["id"] == a["id"]
    # Name does NOT get clobbered by ensure_default (the original is kept).
    assert reg2.get(entry["id"])["name"] == "Original"
