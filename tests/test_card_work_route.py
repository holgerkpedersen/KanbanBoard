"""Regression: ``POST /api/cards/<id>/work`` (the board's Work button).

The route queues a ``card_work`` message for Agent1 and fails closed:
no sync opt-in means nothing is queued. These tests drive the real Flask
route and read the real queue file back through ``kanban_sync.read_queue``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1 import create_app, kanban_sync as ks

from conftest import TEST_CSRF_HEADER, TEST_CSRF_TOKEN

CSRF = {"X-Requested-With": "XMLHttpRequest", **TEST_CSRF_HEADER}


class _NullProcessor:
    def __init__(self, app, poll_interval=5.0):
        self.app = app

    def run_forever(self):
        return None

    def stop(self):
        return None


def _make_app(tmp_path, monkeypatch, *, sync_on: bool):
    monkeypatch.setenv("KANBAN_SYNC_ENABLED", "1" if sync_on else "0")
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
    app.config["TEST_CSRF_TOKEN"] = TEST_CSRF_TOKEN
    from src.agent1.store import BoardStore

    board_path = working / "board-work-route-test.json"
    holder = app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        if holder["store"] is None:
            holder["store"] = BoardStore(path=str(board_path))
            holder["path"] = str(board_path)
        holder["store"].set_sync_settings({"agent1": sync_on})
    return app.test_client(), queue


@pytest.fixture()
def synced(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch, sync_on=True)


@pytest.fixture()
def unsynced(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch, sync_on=False)


def _work_messages(queue):
    messages, _offset = ks.read_queue(queue)
    return [m for m in messages if m.get("op") == "card_work"]


def test_work_enqueues_card_work_with_title_and_text(synced):
    client, queue = synced
    frame = client.post(
        "/api/frames", json={"title": "New"}, headers=CSRF
    ).get_json()["id"]
    card = client.post(
        "/api/cards",
        json={"title": "Work button", "text": "Send me", "frame_id": frame},
        headers=CSRF,
    ).get_json()

    resp = client.post(f"/api/cards/{card['id']}/work", json={}, headers=CSRF)

    assert resp.status_code == 202
    assert resp.get_json() == {"queued": True, "card_id": card["id"]}
    msgs = _work_messages(queue)
    assert len(msgs) == 1
    assert msgs[0]["source_id"] == card["id"]
    assert msgs[0]["payload"]["card_id"] == card["id"]
    assert msgs[0]["payload"]["title"] == "Work button"
    assert msgs[0]["payload"]["text"] == "Send me"
    assert msgs[0]["payload"]["frame_title"] == "New"


def test_work_unknown_card_is_404_and_queues_nothing(synced):
    client, queue = synced
    resp = client.post("/api/cards/does-not-exist/work", json={}, headers=CSRF)
    assert resp.status_code == 404
    assert _work_messages(queue) == []


def test_work_fails_closed_when_sync_disabled(unsynced):
    client, queue = unsynced
    frame = client.post(
        "/api/frames", json={"title": "New"}, headers=CSRF
    ).get_json()["id"]
    card = client.post(
        "/api/cards", json={"title": "X", "frame_id": frame}, headers=CSRF
    ).get_json()

    resp = client.post(f"/api/cards/{card['id']}/work", json={}, headers=CSRF)

    assert resp.status_code == 409
    assert _work_messages(queue) == []
