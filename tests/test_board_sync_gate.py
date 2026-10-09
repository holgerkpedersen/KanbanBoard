"""Tests for the per-board agent1 sync gate (Kanban side of the link).

Two independent gates must both pass before either direction of the sync
link does anything:

* outbound (Kanban -> Agent1): ``routes_cards._should_sync()`` requires the
  process-level master switch ``KANBAN_SYNC_ENABLED=1`` AND the active board's
  on-disk ``sync`` map to allow agent1;
* inbound (Agent1 -> Kanban): ``QueueProcessor.run_once()`` holds messages in
  the queue (no offset advance) while the active board has not opted in.

The per-board opt-in is persisted by ``BoardStore.set_sync_settings`` and
managed through GET/POST /api/boards/sync, so a toggle in the UI takes effect
on both sides of the link without a restart.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: F401  (tmp_path/monkeypatch fixtures)

from src.agent1 import create_app
from src.agent1 import kanban_sync as ks
from src.agent1.store import BoardStore

CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _client(tmp_path, *, select=True):
    """Build a test client with a seeded catalog board 'demo'.

    ``select=False`` leaves no active board (for the 404/empty-state tests).
    The seed frame is titled "Ideas" so an issue_create without an explicit
    status lands there via ISSUE_TO_FRAME's default.
    """
    wd = str(tmp_path / "data")
    cd = str(tmp_path / "data" / "boards")
    Path(cd).mkdir(parents=True, exist_ok=True)
    (Path(cd) / "demo.json").write_text(
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
        working_dir=wd,
        catalog_dir=cd,
        registry_path=str(tmp_path / "active.json"),
    )
    app.config["TESTING"] = True
    client = app.test_client()
    if select:
        r = client.post("/api/boards/select", json={"catalog_id": "demo"}, headers=CSRF)
        assert r.status_code == 200, r.get_json()
    return client, app


def _live_store(app):
    holder = app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        return holder["store"]


# ---- BoardStore sync settings -------------------------------------------------


class TestBoardStoreSyncSettings:
    def test_defaults_to_empty_map(self, tmp_path):
        store = BoardStore(str(tmp_path / "board.json"))
        assert store.get_sync_settings() == {}
        assert store.sync_allowed("agent1") is False

    def test_set_persists_and_round_trips(self, tmp_path):
        path = str(tmp_path / "board.json")
        BoardStore(path).set_sync_settings({"agent1": True})
        reloaded = BoardStore(path)
        assert reloaded.get_sync_settings() == {"agent1": True}
        assert reloaded.sync_allowed("agent1") is True

    def test_non_bool_values_are_dropped(self, tmp_path):
        store = BoardStore(str(tmp_path / "board.json"))
        store.set_sync_settings({"agent1": "yes", "other": 1})
        assert store.get_sync_settings() == {}


# ---- GET/POST /api/boards/sync -------------------------------------------------


class TestBoardSyncApi:
    def test_get_without_active_board(self, tmp_path):
        client, _ = _client(tmp_path, select=False)
        body = client.get("/api/boards/sync").get_json()
        assert body == {"active": False, "board_id": None, "sync": {}}

    def test_post_requires_csrf(self, tmp_path):
        client, _ = _client(tmp_path)
        r = client.post("/api/boards/sync", json={"sync": {"agent1": True}})
        assert r.status_code == 403

    def test_post_without_active_board_404(self, tmp_path):
        client, _ = _client(tmp_path, select=False)
        r = client.post(
            "/api/boards/sync", json={"sync": {"agent1": True}}, headers=CSRF
        )
        assert r.status_code == 404

    def test_post_wrapped_form_updates_and_persists(self, tmp_path):
        client, _ = _client(tmp_path)
        body = client.post(
            "/api/boards/sync", json={"sync": {"agent1": True}}, headers=CSRF
        ).get_json()
        assert body["active"] is True and body["sync"] == {"agent1": True}

        # Reflected by the active-board summary too...
        active = client.get("/api/boards/active").get_json()
        assert active["sync"] == {"agent1": True}

        # ...and persisted to the on-disk working copy (what Agent1 reads).
        on_disk = json.loads(
            (Path(tmp_path / "data" / "board-demo.json")).read_text(encoding="utf-8")
        )
        assert on_disk.get("sync") == {"agent1": True}

    def test_post_bare_map_form(self, tmp_path):
        client, app = _client(tmp_path)
        body = client.post(
            "/api/boards/sync", json={"agent1": False}, headers=CSRF
        ).get_json()
        assert body["sync"] == {"agent1": False}
        assert _live_store(app).sync_allowed("agent1") is False

    def test_post_rejects_non_bool_values(self, tmp_path):
        client, app = _client(tmp_path)
        r = client.post(
            "/api/boards/sync", json={"sync": {"agent1": "yes"}}, headers=CSRF
        )
        assert r.status_code == 400
        assert "agent1" in r.get_json()["error"]
        assert _live_store(app).get_sync_settings() == {}


# ---- outbound gate (Kanban -> Agent1) ------------------------------------------


class TestOutboundGate:
    def test_card_create_held_while_board_opted_out(self, tmp_path, monkeypatch):
        client, _ = _client(tmp_path)
        qdir = Path(tmp_path / "q-kb-to-a1")
        qdir.mkdir()
        monkeypatch.setenv("KANBAN_SYNC_ENABLED", "1")
        monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", qdir)

        r = client.post("/api/cards", json={"frame_id": "f1", "title": "T"}, headers=CSRF)
        assert r.status_code == 201
        msgs, _ = ks.read_queue(qdir)
        assert msgs == []  # board has not opted in -> nothing leaves

    def test_card_create_enqueued_after_opt_in(self, tmp_path, monkeypatch):
        client, app = _client(tmp_path)
        qdir = Path(tmp_path / "q-kb-to-a1")
        qdir.mkdir()
        monkeypatch.setenv("KANBAN_SYNC_ENABLED", "1")
        monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", qdir)

        _live_store(app).set_sync_settings({"agent1": True})
        r = client.post("/api/cards", json={"frame_id": "f1", "title": "T"}, headers=CSRF)
        assert r.status_code == 201
        msgs, _ = ks.read_queue(qdir)
        assert [m["op"] for m in msgs] == ["card_create"]

    def test_master_switch_still_dominates(self, tmp_path, monkeypatch):
        """Env switch off -> no outbound even when the board opted in."""
        client, app = _client(tmp_path)
        qdir = Path(tmp_path / "q-kb-to-a1")
        qdir.mkdir()
        monkeypatch.delenv("KANBAN_SYNC_ENABLED", raising=False)
        monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", qdir)
        _live_store(app).set_sync_settings({"agent1": True})

        r = client.post("/api/cards", json={"frame_id": "f1", "title": "T"}, headers=CSRF)
        assert r.status_code == 201
        msgs, _ = ks.read_queue(qdir)
        assert msgs == []


# ---- inbound gate (Agent1 -> Kanban) --------------------------------------------


class TestInboundProcessorHold:
    def test_run_once_holds_then_applies_after_opt_in(self, tmp_path):
        client, app = _client(tmp_path)
        qdir = Path(tmp_path / "q-a1-to-kb")
        qdir.mkdir()
        ks.enqueue(
            {"op": "issue_create", "source_id": "i-1", "payload": {"title": "T"}},
            queue_dir=qdir,
        )
        processor = ks.QueueProcessor(app, queue_dir=qdir)

        # Board opted out: message held (not applied, not consumed).
        assert processor.run_once() == 0
        assert _live_store(app).get_all_cards() == []

        # Still pending after the hold...
        msgs, _ = ks.read_queue(qdir)
        assert [m["op"] for m in msgs] == ["issue_create"]

        # ...and applied once the board opts in.
        _live_store(app).set_sync_settings({"agent1": True})
        assert processor.run_once() == 1
        cards = _live_store(app).get_all_cards()
        assert [c.title for c in cards] == ["T"]

    def test_holds_for_unsynced_board_even_with_env_on(self, tmp_path, monkeypatch):
        """The inbound gate is per-board; the env switch does not bypass it."""
        client, app = _client(tmp_path)
        qdir = Path(tmp_path / "q-a1-to-kb")
        qdir.mkdir()
        monkeypatch.setenv("KANBAN_SYNC_ENABLED", "1")
        ks.enqueue(
            {"op": "issue_create", "source_id": "i-2", "payload": {"title": "U"}},
            queue_dir=qdir,
        )

        assert ks.QueueProcessor(app, queue_dir=qdir).run_once() == 0
        assert _live_store(app).get_all_cards() == []
