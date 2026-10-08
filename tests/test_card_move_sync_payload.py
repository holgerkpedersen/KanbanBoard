"""Regression: a card move must tell Agent1 *which column* the card landed in.

``POST /api/cards/<id>/move`` enqueues a ``card_move`` message so Agent1 can
map the column to an issue status. Frames are stored by opaque id (a uuid
hex), while Agent1's ``FRAME_TO_STATUS`` table is keyed by column *title*
("Finished" -> resolved). Sending only the id meant the lookup could never
hit and every move fell back to "open" — silently rewriting a resolved
issue back to open.

The fix adds ``frame_title`` alongside ``frame_id``. Agent1's
``_resolve_frame_status`` reads ``frame_title`` first and keeps accepting
``frame_id`` so messages enqueued before the fix still apply.

These tests drive the real Flask route and read the real queue file back
through ``kanban_sync.read_queue`` — no stubbed enqueue.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1 import create_app, kanban_sync as ks

CSRF = {"X-Requested-With": "XMLHttpRequest"}


class _NullProcessor:
    """No-op stand-in so enabling sync never spawns a real poller thread."""

    def __init__(self, app, poll_interval=5.0):
        self.app = app

    def run_forever(self):
        return None

    def stop(self):
        return None


@pytest.fixture()
def synced(tmp_path, monkeypatch):
    """Sync enabled, writing into a throwaway queue inside ``tmp_path``."""
    monkeypatch.setenv("KANBAN_SYNC_ENABLED", "1")
    queue = tmp_path / "queue-kanban-to-agent1"
    monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", queue)
    monkeypatch.setattr(ks, "QueueProcessor", _NullProcessor)

    working = tmp_path / "data"
    working.mkdir()
    catalog = working / "boards"
    catalog.mkdir()
    app = create_app(
        working_dir=str(working),
        catalog_dir=str(catalog),
        registry_path=str(tmp_path / "active.json"),
    )
    app.config["TESTING"] = True
    return app.test_client(), queue


def _queued(queue):
    """Every message the app has written to the Kanban -> Agent1 queue."""
    messages, _offset = ks.read_queue(queue)
    return messages


def _move_messages(queue):
    return [m for m in _queued(queue) if m.get("op") == "card_move"]


def test_move_enqueues_frame_title_not_just_id(synced):
    """The regression: the title travels with the opaque id."""
    client, queue = synced
    f1 = client.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    f2 = client.post(
        "/api/frames", json={"title": "Finished"}, headers=CSRF
    ).get_json()["id"]
    card = client.post(
        "/api/cards", json={"title": "X", "frame_id": f1}, headers=CSRF
    ).get_json()

    resp = client.post(
        f"/api/cards/{card['id']}/move", json={"frame_id": f2}, headers=CSRF
    )
    assert resp.status_code == 200

    moves = _move_messages(queue)
    assert len(moves) == 1, f"expected one card_move, got {moves}"
    payload = moves[0]["payload"]
    assert payload["frame_id"] == f2
    # The whole point: a human-readable title, not the uuid.
    assert payload["frame_title"] == "Finished"
    assert payload["frame_title"] != payload["frame_id"]


def test_move_frame_title_tracks_the_destination_column(synced):
    """A second move reports its own column, not the first one's."""
    client, queue = synced
    a = client.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    b = client.post("/api/frames", json={"title": "B"}, headers=CSRF).get_json()["id"]
    card = client.post(
        "/api/cards", json={"title": "X", "frame_id": a}, headers=CSRF
    ).get_json()

    assert client.post(
        f"/api/cards/{card['id']}/move", json={"frame_id": b}, headers=CSRF
    ).status_code == 200
    assert client.post(
        f"/api/cards/{card['id']}/move", json={"frame_id": a}, headers=CSRF
    ).status_code == 200

    titles = [m["payload"]["frame_title"] for m in _move_messages(queue)]
    assert titles == ["B", "A"]


def test_frame_title_is_a_real_column_name(synced):
    """The title sent is the human-readable column name, not the uuid.

    Agent1 resolves the status by looking the *title* up in its
    ``FRAME_TO_STATUS`` table; a message carrying only the opaque id can
    never hit that table. (The mapping itself is pinned on the Agent1 side,
    in ``tests/test_kanban_bridge.py``, which owns that module.)
    """
    client, queue = synced
    f1 = client.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    f2 = client.post(
        "/api/frames", json={"title": "Finished"}, headers=CSRF
    ).get_json()["id"]
    card = client.post(
        "/api/cards", json={"title": "X", "frame_id": f1}, headers=CSRF
    ).get_json()
    client.post(
        f"/api/cards/{card['id']}/move", json={"frame_id": f2}, headers=CSRF
    )

    title = _move_messages(queue)[0]["payload"]["frame_title"]
    assert title == "Finished"
    assert title.strip() == title and title, "a blank/odd title could never resolve"


def test_no_message_when_sync_disabled(tmp_path, monkeypatch):
    """With sync off the queue stays untouched."""
    monkeypatch.delenv("KANBAN_SYNC_ENABLED", raising=False)
    queue = tmp_path / "queue-kanban-to-agent1"
    monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", queue)

    working = tmp_path / "data"
    working.mkdir()
    catalog = working / "boards"
    catalog.mkdir()
    app = create_app(
        working_dir=str(working),
        catalog_dir=str(catalog),
        registry_path=str(tmp_path / "active.json"),
    )
    app.config["TESTING"] = True
    client = app.test_client()

    f1 = client.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    f2 = client.post("/api/frames", json={"title": "B"}, headers=CSRF).get_json()["id"]
    card = client.post(
        "/api/cards", json={"title": "X", "frame_id": f1}, headers=CSRF
    ).get_json()
    client.post(
        f"/api/cards/{card['id']}/move", json={"frame_id": f2}, headers=CSRF
    )

    assert _move_messages(queue) == []
