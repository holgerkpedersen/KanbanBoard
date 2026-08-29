"""Tests for the read-only BoardCatalog view."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1.board_catalog import BoardCatalog


def _write(p: Path, payload) -> None:
    p.write_text(json.dumps(payload), encoding="utf-8")


def test_list_returns_empty_when_folder_missing(tmp_path):
    cat = BoardCatalog(folder=str(tmp_path / "nope"))
    assert cat.list() == []


def test_list_reads_display_name_from_seed(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    _write(folder / "alpha.json", {"name": "Plan Alpha", "frames": [], "cards": []})
    cat = BoardCatalog(folder=str(folder))
    entries = cat.list()
    assert len(entries) == 1
    assert entries[0]["id"] == "alpha"
    assert entries[0]["name"] == "Plan Alpha"
    assert entries[0]["source_path"].endswith("alpha.json")


def test_list_falls_back_to_id_when_no_name(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    _write(folder / "no-name.json", {"frames": [], "cards": []})
    cat = BoardCatalog(folder=str(folder))
    assert cat.list()[0]["name"] == "no-name"


def test_list_falls_back_to_id_when_seed_is_unparseable(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    cat = BoardCatalog(folder=str(folder))
    # We don't know the display name, so fall back to the file stem.
    entries = cat.list()
    assert len(entries) == 1
    assert entries[0]["id"] == "broken"
    assert entries[0]["name"] == "broken"


def test_list_skips_non_json_files_and_invalid_ids(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    (folder / "good.json").write_text("{}", encoding="utf-8")
    (folder / "README.md").write_text("hi", encoding="utf-8")
    (folder / "with space.json").write_text("{}", encoding="utf-8")
    (folder / "../escape.json").write_text("{}", encoding="utf-8")
    cat = BoardCatalog(folder=str(folder))
    ids = [e["id"] for e in cat.list()]
    assert ids == ["good"]


def test_list_returns_sorted_by_id(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    for cid in ("zeta", "alpha", "mu"):
        (folder / f"{cid}.json").write_text("{}", encoding="utf-8")
    cat = BoardCatalog(folder=str(folder))
    ids = [e["id"] for e in cat.list()]
    assert ids == ["alpha", "mu", "zeta"]


def test_get_returns_entry_by_id(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    (folder / "alpha.json").write_text(
        json.dumps({"name": "Alpha"}), encoding="utf-8"
    )
    cat = BoardCatalog(folder=str(folder))
    entry = cat.get("alpha")
    assert entry and entry["name"] == "Alpha"
    assert cat.get("missing") is None


def test_path_for_rejects_invalid_ids(tmp_path):
    cat = BoardCatalog(folder=str(tmp_path))
    with pytest.raises(ValueError):
        cat.path_for("../escape")
    with pytest.raises(ValueError):
        cat.path_for("with space")
    with pytest.raises(ValueError):
        cat.path_for("")


def test_list_caches_until_folder_mtime_changes(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps({"name": "A"}), encoding="utf-8")
    cat = BoardCatalog(folder=str(folder))
    first = cat.list()
    # Mutate the file but keep the folder mtime equal: catalog uses
    # folder mtime as the cache invalidation key.
    time.sleep(0.05)  # ensure mtime granularity is past the previous
    (folder / "a.json").write_text(json.dumps({"name": "Renamed"}), encoding="utf-8")
    # Touch the folder to bump its mtime.
    os.utime(str(folder), None)
    second = cat.list()
    assert second[0]["name"] == "Renamed"
    assert first is not second  # a fresh list was returned


def test_display_name_is_truncated_to_100_chars(tmp_path):
    folder = tmp_path / "boards"
    folder.mkdir()
    long_name = "x" * 250
    (folder / "a.json").write_text(json.dumps({"name": long_name}), encoding="utf-8")
    cat = BoardCatalog(folder=str(folder))
    assert len(cat.list()[0]["name"]) == 100
