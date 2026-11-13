"""Regression: the sync master switch must be readable from the repo ``.env``.

The queue processor thread in ``create_app()`` is gated on
``KANBAN_SYNC_ENABLED``, read from ``os.environ`` only. The operator sets the
switch in ``C:\\Dev\\Kanban\\.env`` (the documented place), and nothing bridges
that file into the process environment — so the app started with sync silently
disabled, the outbound queue (Agent1 -> Kanban) stopped draining, and resolved
issues never moved their cards to Finished.

Contract pinned here:

* ``sync_enabled()`` resolves the switch process-env first, then the repo
  ``.env`` (the same two-layer resolution Agent1's bridge uses);
* ``create_app()`` gates the processor on that resolver, not on a bare
  ``os.environ`` read;
* an empty/blank value in ``.env`` means "off" — it must not count as enabled.
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import kanban_sync as ks
app_module = importlib.import_module("src.agent1.app")


class _StubProcessor:
    """Stand-in for ``QueueProcessor`` that never touches real queues."""

    def __init__(self, app, poll_interval=5.0):
        self.app = app

    def run_forever(self):
        pass

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def _reset_sync_handles(monkeypatch):
    """Give each test the "no processor yet" state and stub the poller."""
    monkeypatch.setattr(ks, "QueueProcessor", _StubProcessor)
    saved = list(getattr(app_module, "_SYNC_HANDLES", []))
    app_module._SYNC_HANDLES = []
    yield
    app_module._SYNC_HANDLES = saved


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """Point the resolver at a throwaway .env and clear the process env."""

    def _write(text):
        p = tmp_path / ".env"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setattr(ks, "_ENV_FILE", p)
        return p

    monkeypatch.delenv("KANBAN_SYNC_ENABLED", raising=False)
    return _write


def test_sync_enabled_off_with_no_env_and_no_file(env_file):
    env_file("")  # file exists but says nothing about the switch
    assert ks.sync_enabled() is False


def test_sync_enabled_from_repo_env_file(env_file):
    env_file("KANBAN_SYNC_ENABLED=1\n")
    assert ks.sync_enabled() is True


def test_sync_enabled_blank_value_in_env_file_is_off(env_file):
    env_file("KANBAN_SYNC_ENABLED=\n")
    assert ks.sync_enabled() is False


def test_process_env_wins_over_env_file(env_file, monkeypatch):
    env_file("KANBAN_SYNC_ENABLED=1\n")
    monkeypatch.setenv("KANBAN_SYNC_ENABLED", "0")
    assert ks.sync_enabled() is False


def test_create_app_starts_processor_from_env_file_only(env_file):
    """The live failure: .env has the switch, process env does not.

    Before the fix ``create_app()`` read only ``os.environ`` and skipped the
    processor entirely; with the resolver in place the thread must start.
    """
    env_file("KANBAN_SYNC_ENABLED=1\n")
    app = app_module.create_app()
    assert app is not None
    assert app_module._SYNC_HANDLES, "processor was not started from .env switch"


def test_create_app_skips_processor_when_everything_off(env_file):
    env_file("KANBAN_SYNC_ENABLED=\n")
    app = app_module.create_app()
    assert app is not None
    assert not app_module._SYNC_HANDLES
