"""Shared test support for the Kanban board service tests.

The app enforces a double-submit CSRF token on every ``/api/*`` write
(POST / PUT / DELETE).  In production the token is generated per request
and handed to the browser through the ``<meta name="csrf-token">`` tag;
under ``TESTING`` the app uses a fixed token (see ``create_app``) so a
test client can send a matching ``X-CSRF-Token`` header.

The real guard still runs: requests that omit the header are rejected
with 403, so security assertions keep their meaning.

Usage in tests::

    CSRF = {"X-Requested-With": "XMLHttpRequest", **TEST_CSRF_HEADER}
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session", autouse=True)
def _sync_switch_isolation():
    """Keep the real repo ``.env`` out of every test's sync-gate decisions.

    ``kanban_sync.sync_enabled()`` resolves the master switch as: process env
    first, then the repo-root ``.env``. The repo now ships a working
    ``.env`` with ``KANBAN_SYNC_ENABLED=1`` (that file is what opens gate 1 of
    the Work button on a real machine), so a test that only clears
    ``os.environ`` no longer means "sync off" — it inherits the developer's
    settings and silently starts queue threads.

    Exporting an explicit value makes the resolver ignore the file entirely,
    which restores the pre-``.env`` baseline: tests get "off by default" and
    any test that wants sync on sets the variable itself (see
    ``test_app_sync_env_file.py``, which also redirects ``_ENV_FILE``).
    """
    saved = os.environ.get("KANBAN_SYNC_ENABLED")
    os.environ["KANBAN_SYNC_ENABLED"] = "0"
    yield
    if saved is None:
        os.environ.pop("KANBAN_SYNC_ENABLED", None)
    else:
        os.environ["KANBAN_SYNC_ENABLED"] = saved


TEST_CSRF_TOKEN = "test-csrf-token"

# Header dict to merge into every mutating test request.
TEST_CSRF_HEADER = {"X-CSRF-Token": TEST_CSRF_TOKEN}


def _harden_pytest_temp_cleanup() -> None:
    """Neutralize a wedge in pytest's atexit temp-dir cleanup on Windows.

    ``_pytest.pathlib.make_numbered_dir_with_cleanup`` registers
    ``cleanup_numbered_dir`` via ``atexit``; it ends with an unguarded
    ``cleanup_dead_symlinks(root)`` sweep whose
    ``left_dir.resolve().exists()`` RE-RAISES ``[WinError 5]`` for an entry
    that is enumerable but not stat-able (observed 2026-11-12: a wedged
    ``pytest-current`` symlink in the machine-global
    ``%TEMP%\\pytest-of-<user>`` root, delete-pending after a crashed or
    privileged run).  Python 3.12 then prints "Exception ignored in atexit
    callback" on EVERY tmp-using run — stderr noise that run wrappers read
    as a failure.  Wrapping the sweep so an unstatable entry is skipped (a
    delete-pending name clears when the OS releases it) removes the noise
    deterministically.  Idempotent and defensive: a no-op if pytest ever
    renames the function.
    """
    import _pytest.pathlib as _pl

    orig = getattr(_pl, "cleanup_dead_symlinks", None)
    if orig is None or getattr(orig, "_temp_cleanup_hardened", False):
        return

    def _safe_cleanup_dead_symlinks(root: object) -> None:
        try:
            orig(root)
        except OSError:
            pass  # wedged entry: leave it for the OS, never raise at exit

    _safe_cleanup_dead_symlinks._temp_cleanup_hardened = True  # type: ignore[attr-defined]
    _pl.cleanup_dead_symlinks = _safe_cleanup_dead_symlinks  # type: ignore[assignment]


_harden_pytest_temp_cleanup()
