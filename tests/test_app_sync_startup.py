"""Regression tests: ``create_app()`` must survive enabling Kanban sync.

``create_app()`` starts the Kanban sync background thread when
``KANBAN_SYNC_ENABLED=1``, wrapping the startup in a try/except so a sync
failure can never take down the web app. That guard logged through a
module-level ``logger`` that was never defined (and ``logging`` was never
imported), so the ``except`` handler raised ``NameError`` instead of
swallowing the error.

Because the production entry point calls ``create_app()`` at import time, the
``NameError`` propagated out of the module: ``import src.agent1`` failed
outright whenever sync was enabled — the app could not even start.

These tests pin the contract: enabling sync must not raise, and a failing sync
startup must still yield a usable app.
"""

import importlib
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1.app import create_app

# ``src.agent1.__init__`` re-exports the Flask instance as ``src.agent1.app``,
# which shadows the submodule of the same name. Import by name to get the
# module itself, so we can assert on its ``logger``.
app_module = importlib.import_module("src.agent1.app")


class _StubProcessor:
    """Stand-in for ``kanban_sync.QueueProcessor`` that never blocks."""

    started = False

    def __init__(self, app, poll_interval=5.0):
        self.app = app

    def run_forever(self):
        type(self).started = True


class _BoomProcessor:
    """A processor whose construction fails, exercising the except branch."""

    def __init__(self, app, poll_interval=5.0):
        raise RuntimeError("sync boom")


@pytest.fixture()
def sync_enabled(monkeypatch):
    """Enable sync without touching the real queue directory."""
    monkeypatch.setenv("KANBAN_SYNC_ENABLED", "1")
    return monkeypatch


def test_module_defines_logger():
    """The startup guard logs through a real logger, not an undefined name."""
    assert isinstance(app_module.logger, logging.Logger)


def test_create_app_with_sync_enabled_does_not_raise(sync_enabled):
    """Regression: enabling sync must not crash create_app()."""
    from src.agent1 import kanban_sync as ks

    sync_enabled.setattr(ks, "QueueProcessor", _StubProcessor)

    app = create_app()
    assert app is not None


def test_create_app_with_sync_enabled_starts_thread(sync_enabled):
    """The happy path really does start the processor."""
    from src.agent1 import kanban_sync as ks

    _StubProcessor.started = False
    sync_enabled.setattr(ks, "QueueProcessor", _StubProcessor)

    create_app()
    assert _StubProcessor.started is True


def test_failing_sync_startup_is_swallowed(sync_enabled):
    """A sync failure is logged, never fatal to the web app.

    Regression: this branch raised ``NameError`` on the undefined ``logger``
    and so escaped the guard it was written to enforce.
    """
    from src.agent1 import kanban_sync as ks

    sync_enabled.setattr(ks, "QueueProcessor", _BoomProcessor)

    app = create_app()
    assert app is not None


def test_sync_disabled_does_not_start_processor(monkeypatch):
    """With sync off, the processor is never constructed."""
    from src.agent1 import kanban_sync as ks

    monkeypatch.delenv("KANBAN_SYNC_ENABLED", raising=False)
    _StubProcessor.started = False
    monkeypatch.setattr(ks, "QueueProcessor", _StubProcessor)

    create_app()
    assert _StubProcessor.started is False
