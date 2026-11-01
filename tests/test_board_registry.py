"""Tests for the active-id pointer (BoardRegistry).

The registry is now a small atomic-write JSON file that records just
which catalog board is currently active. It used to be a list of
known boards; Agent1 now owns that list (the catalog folder) and the
app is read-only against it.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import src.agent1.board_registry as reg_mod
from src.agent1.board_registry import BoardRegistry


def test_registry_initially_no_active(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "active.json"))
    assert reg.get_active() is None


def test_set_active_persists_across_reload(tmp_path):
    path = str(tmp_path / "active.json")
    reg = BoardRegistry(path=path)
    reg.set_active("plan-1")
    assert reg.get_active() == "plan-1"
    reg2 = BoardRegistry(path=path)
    assert reg2.get_active() == "plan-1"


def test_clear_resets_active(tmp_path):
    path = str(tmp_path / "active.json")
    reg = BoardRegistry(path=path)
    reg.set_active("plan-1")
    reg.clear()
    assert reg.get_active() is None
    reg2 = BoardRegistry(path=path)
    assert reg2.get_active() is None


def test_set_active_rejects_empty_string(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "active.json"))
    with pytest.raises(ValueError):
        reg.set_active("")
    with pytest.raises(ValueError):
        reg.set_active(None)  # type: ignore[arg-type]


def test_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "active.json"
    path.write_text("{ not json", encoding="utf-8")
    reg = BoardRegistry(path=str(path))
    assert reg.get_active() is None
    # Subsequent set still works.
    reg.set_active("plan-x")
    assert reg.get_active() == "plan-x"


def test_tolerates_missing_file(tmp_path):
    # No file on disk yet: registry starts empty without error.
    reg = BoardRegistry(path=str(tmp_path / "does-not-exist.json"))
    assert reg.get_active() is None


def test_tolerates_non_dict_payload(tmp_path):
    path = tmp_path / "active.json"
    path.write_text("[]", encoding="utf-8")  # valid JSON, wrong shape
    reg = BoardRegistry(path=str(path))
    assert reg.get_active() is None
    reg.set_active("plan-1")
    assert reg.get_active() == "plan-1"


def test_set_active_overwrites_previous(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "active.json"))
    reg.set_active("a")
    reg.set_active("b")
    assert reg.get_active() == "b"


def test_atomic_write_does_not_leave_tmp_on_success(tmp_path):
    reg = BoardRegistry(path=str(tmp_path / "active.json"))
    reg.set_active("a")
    reg.set_active("b")
    leftover = list(tmp_path.glob("active.json.tmp"))
    assert leftover == []


def test_registry_atomic_write_retries_transient_windows_sharing_violation(
    tmp_path, monkeypatch
):
    """Regression: the active-board pointer write must retry a transient
    ``PermissionError: [WinError 32]`` instead of failing the write.
    """
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(
                32,
                "The process cannot access the file because of other opened handles",
                str(dst),
            )
        return real_replace(src, dst)

    monkeypatch.setattr(reg_mod.os, "replace", flaky_replace)
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    reg = BoardRegistry(path=str(tmp_path / "active.json"))
    reg.set_active("a")

    assert calls["n"] == 2, "the transient violation must be retried exactly once"
    assert reg.get_active() == "a"
