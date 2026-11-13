"""Regression: the Work button's sync gate must resolve the master switch the
same way the rest of the app does.

The live failure: the board's "Agent1 sync" checkbox was ticked (the per-board
``sync`` map on disk held ``{"agent1": true}``), yet
``POST /api/cards/<id>/work`` answered 409 "agent1 sync is not enabled for this
board".

Cause: ``routes_cards._should_sync()`` read the process-level master switch from
``os.environ`` ONLY, while every other consumer (``app.create_app`` and the
inbound ``QueueProcessor``) resolves it through ``kanban_sync.sync_enabled()``,
which also reads the repo ``.env`` file. An operator who flips the switch in
``.env`` therefore got a running sync pipeline whose Work button refused to
queue, and the 409 blamed the per-board setting that was actually enabled.

Fix: ``_should_sync()`` delegates to ``kanban_sync.sync_enabled()``.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: F401  (tmp_path/monkeypatch fixtures)

from src.agent1 import create_app
from src.agent1 import kanban_sync as ks

from conftest import TEST_CSRF_HEADER, TEST_CSRF_TOKEN

CSRF = {"X-Requested-With": "XMLHttpRequest", **TEST_CSRF_HEADER}


def _board_app(tmp_path, monkeypatch, *, env_value=None, env_file_text=None):
    """Build the real app with the board opted in to agent1 sync.

    ``env_value`` is what the *process* environment says about the master
    switch; ``env_file_text`` is the repo ``.env`` content. The queue and the
    ``.env`` path are redirected into ``tmp_path`` so nothing touches the
    developer's real data directory.
    """
    if env_value is None:
        monkeypatch.delenv("KANBAN_SYNC_ENABLED", raising=False)
    else:
        monkeypatch.setenv("KANBAN_SYNC_ENABLED", env_value)

    if env_file_text is not None:
        env_path = tmp_path / ".env"
        env_path.write_text(env_file_text, encoding="utf-8")
        monkeypatch.setattr(ks, "_ENV_FILE", env_path)
    else:
        # A .env that says nothing about the switch (exists, silent).
        monkeypatch.setattr(ks, "_ENV_FILE", tmp_path / ".env")

    queue = tmp_path / "queue-kanban-to-agent1"
    monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", queue)

    working = tmp_path / "data"
    working.mkdir()
    catalog = working / "boards"
    catalog.mkdir()
    (catalog / "demo.json").write_text(
        json.dumps(
            {
                "name": "Demo",
                "frames": [{"id": "f1", "title": "Ideas", "card_ids": []}],
                "cards": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        working_dir=str(working),
        catalog_dir=str(catalog),
        registry_path=str(tmp_path / "active.json"),
    )
    app.config["TESTING"] = True
    app.config["TEST_CSRF_TOKEN"] = TEST_CSRF_TOKEN

    client = app.test_client()
    r = client.post("/api/boards/select", json={"catalog_id": "demo"}, headers=CSRF)
    assert r.status_code == 200, r.get_json()

    # The user's checkbox: the per-board opt-in is ON.
    holder = app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        store = holder["store"]
    assert store is not None
    store.set_sync_settings({"agent1": True})
    assert store.sync_allowed("agent1") is True

    card = client.post(
        "/api/cards", json={"frame_id": "f1", "title": "Work me"}, headers=CSRF
    ).get_json()
    return client, app, queue, card["id"]


def test_work_button_queues_when_switch_is_in_env_file_only(tmp_path, monkeypatch):
    """The reported failure: ``.env`` switch on, board opted in -> must queue."""
    client, app, queue, card_id = _board_app(
        tmp_path, monkeypatch, env_value=None, env_file_text="KANBAN_SYNC_ENABLED=1\n"
    )

    r = client.post(f"/api/cards/{card_id}/work", headers=CSRF)

    assert r.status_code == 202, r.get_json()
    assert r.get_json()["queued"] is True
    msgs, _ = ks.read_queue(queue)
    # The card was created while sync was already on, so it queued a
    # card_create too; what matters is that the Work request left.
    assert [m["op"] for m in msgs] == ["card_create", "card_work"]
    work = [m for m in msgs if m["op"] == "card_work"]
    assert len(work) == 1
    assert work[0]["source_id"] == card_id


def test_work_button_still_reports_board_opt_out(tmp_path, monkeypatch):
    """The 409 must only fire for the reason it names: board not opted in."""
    client, app, _queue, card_id = _board_app(
        tmp_path, monkeypatch, env_value="1", env_file_text=None
    )
    holder = app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        store = holder["store"]
    store.set_sync_settings({"agent1": False})

    r = client.post(f"/api/cards/{card_id}/work", headers=CSRF)

    assert r.status_code == 409
    assert "not enabled" in r.get_json()["error"]


def test_work_button_refuses_when_master_switch_off_everywhere(tmp_path, monkeypatch):
    """Master switch off in both layers -> fail closed (no queue write)."""
    client, app, queue, card_id = _board_app(
        tmp_path, monkeypatch, env_value=None, env_file_text="KANBAN_SYNC_ENABLED=\n"
    )

    r = client.post(f"/api/cards/{card_id}/work", headers=CSRF)

    assert r.status_code == 409
    assert not queue.exists() or ks.read_queue(queue)[0] == []


def test_process_env_switch_off_overrides_env_file(tmp_path, monkeypatch):
    """Process env wins: an explicit off must keep the Work button closed."""
    client, app, queue, card_id = _board_app(
        tmp_path, monkeypatch, env_value="0", env_file_text="KANBAN_SYNC_ENABLED=1\n"
    )

    r = client.post(f"/api/cards/{card_id}/work", headers=CSRF)

    assert r.status_code == 409
    assert not queue.exists() or ks.read_queue(queue)[0] == []


def test_shipped_config_enables_both_sync_gates():
    """Deployment contract for the reported failure.

    The code fix alone did not stop the 409: the operator's switch lives in
    ``C:\\Dev\\Agent1\\.env``, a DIFFERENT repo, and the Kanban app resolves
    gate 1 from its own repo root.  With no ``Kanban\\.env`` and no exported
    variable, ``sync_enabled()`` returned False while the checkbox read
    ``{"agent1": true}`` on disk — so Work answered "not enabled for this
    board" forever.

    This test pins the machine-local settings that actually open gate 1, and
    keeps ``KANBAN_WORKING_DIR`` pointed at the same queue root Agent1's bridge
    uses.  It fails if ``.env`` is deleted, emptied, or the switch flipped off.
    """
    repo = Path(__file__).resolve().parents[1]
    env_file = repo / ".env"

    assert env_file.is_file(), (
        "Kanban repo root needs a .env with KANBAN_SYNC_ENABLED=1; the Agent1 "
        "repo's .env does not apply to this app"
    )

    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("\"'")

    assert values.get("KANBAN_SYNC_ENABLED", "").lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    ), "gate 1 (master switch) must be on in Kanban's own .env"

    # Gate 1 must resolve on from the file alone, with nothing exported.
    monkeypatch_env = os.environ.pop("KANBAN_SYNC_ENABLED", None)
    try:
        assert ks.sync_enabled() is True
    finally:
        if monkeypatch_env is not None:
            os.environ["KANBAN_SYNC_ENABLED"] = monkeypatch_env

    # Both sides of the bridge must resolve one shared queue root.
    working = Path(values.get("KANBAN_WORKING_DIR") or "data")
    if not working.is_absolute():
        working = repo / working
    assert (working / "queue-kanban-to-agent1" / "messages.jsonl").is_file(), (
        f"KANBAN_WORKING_DIR must be the queue root Agent1 also writes to: {working}"
    )

    # The template that documents the setting must exist for a fresh checkout.
    assert (repo / ".env.example").is_file()


def test_should_sync_uses_the_shared_switch_resolver(tmp_path, monkeypatch):
    """``_should_sync`` tracks ``kanban_sync.sync_enabled()``, not os.environ."""
    from src.agent1 import routes_cards

    env_path = tmp_path / ".env"
    env_path.write_text("KANBAN_SYNC_ENABLED=1\n", encoding="utf-8")
    monkeypatch.setattr(ks, "_ENV_FILE", env_path)
    monkeypatch.delenv("KANBAN_SYNC_ENABLED", raising=False)

    client, app, _queue, _card_id = _board_app(tmp_path, monkeypatch, env_value=None)
    with app.test_request_context("/api/cards"):
        from flask import g

        holder = app.config["LIVE_STORE_HOLDER"]
        with holder["lock"]:
            g.board_store = holder["store"]
        assert routes_cards._should_sync() is True
