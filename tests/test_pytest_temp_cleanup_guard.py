"""Regression tests for the atexit temp-cleanup guard (tests/conftest.py).

2026-11-12: every tmp-using pytest run on the machine ended with
"Exception ignored in atexit callback" + ``PermissionError: [WinError 5]``
on a wedged ``pytest-current`` symlink in ``%TEMP%\\pytest-of-<user>``:
``cleanup_numbered_dir`` (atexit) -> ``cleanup_dead_symlinks`` ->
``Path.resolve().exists()`` re-raises WinError 5 for an entry that is
enumerable but not stat-able (delete-pending).  The guard wraps that sweep
so an unstatable entry is skipped instead of raising at interpreter exit.
"""

from __future__ import annotations

from pathlib import Path

import _pytest.pathlib as pytest_pathlib

import conftest as repo_conftest


def test_guard_swallows_winerror5_from_dead_symlink_sweep(monkeypatch):
    """The atexit sweep must never raise - that is the whole point."""
    calls = []

    def boom(root):
        calls.append(root)
        raise PermissionError(13, "Access is denied", str(root))

    monkeypatch.setattr(pytest_pathlib, "cleanup_dead_symlinks", boom)
    repo_conftest._harden_pytest_temp_cleanup()
    pytest_pathlib.cleanup_dead_symlinks(Path("pytest-current"))
    assert calls, "the wrapped sweep must still run for healthy roots"


def test_guard_delegates_healthy_sweep(monkeypatch):
    """A healthy sweep runs unchanged (only OSError is swallowed)."""
    seen = []

    def ok(root):
        seen.append(root)

    monkeypatch.setattr(pytest_pathlib, "cleanup_dead_symlinks", ok)
    repo_conftest._harden_pytest_temp_cleanup()
    pytest_pathlib.cleanup_dead_symlinks("root-1")
    assert seen == ["root-1"]


def test_guard_is_idempotent(monkeypatch):
    """Arming twice must not double-wrap (the flag is checked first)."""
    def ok(root):
        pass

    monkeypatch.setattr(pytest_pathlib, "cleanup_dead_symlinks", ok)
    repo_conftest._harden_pytest_temp_cleanup()
    first = pytest_pathlib.cleanup_dead_symlinks
    repo_conftest._harden_pytest_temp_cleanup()
    assert pytest_pathlib.cleanup_dead_symlinks is first


def test_guard_is_armed_by_conftest_import():
    """Conftest import arms the guard, so every run is protected."""
    assert getattr(
        pytest_pathlib.cleanup_dead_symlinks, "_temp_cleanup_hardened", False
    )
