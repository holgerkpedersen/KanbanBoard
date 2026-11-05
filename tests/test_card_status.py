"""Regression tests for the Card ``status`` field.

The status lifecycle:
- Cards default to ``"open"`` when no status is sent.
- Only values in ``VALID_STATUSES`` are accepted (``open``, ``planned``,
  ``in-progress``, ``reviewing``, ``done``).
- Invalid or missing statuses return HTTP 400 on create and update.
- Status round-trips through the API, disk persistence and sync payloads.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1.models import VALID_STATUSES
from src.agent1.store import BoardStore
from src.agent1 import create_app


CSRF = {"X-Requested-With": "XMLHttpRequest"}


# ---- model-level tests ----------------------------------------------------

def test_valid_statuses_are_exactly_the_expected_set():
    """VALID_STATUSES must list every recognised workflow status."""
    assert VALID_STATUSES == frozenset(
        {"open", "planned", "in-progress", "reviewing", "done"}
    )


def test_card_default_status_is_open():
    from src.agent1.models import Card

    card = Card(id="x", title="T", text="", frame_id="f")
    assert card.status == "open"


def test_card_to_dict_includes_status():
    from src.agent1.models import Card

    card = Card(id="x", title="T", text="", frame_id="f", status="done")
    d = card.to_dict()
    assert d["status"] == "done"


# ---- API-level tests ------------------------------------------------------

def _client_with_frame():
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    # Create a frame for the test to use.
    r = c.post("/api/frames", json={"title": "To Do"}, headers=CSRF)
    fid = r.get_json()["id"]
    return c, fid


def test_create_card_defaults_status_to_open():
    c, fid = _client_with_frame()
    r = c.post(
        "/api/cards",
        json={"title": "Test card", "frame_id": fid},
        headers=CSRF,
    )
    assert r.status_code == 201
    assert r.get_json()["status"] == "open"


def test_create_card_accepts_valid_status():
    c, fid = _client_with_frame()
    for status in VALID_STATUSES:
        r = c.post(
            "/api/cards",
            json={"title": f"Card-{status}", "frame_id": fid, "status": status},
            headers=CSRF,
        )
        assert r.status_code == 201, f"Failed for status={status!r}"
        assert r.get_json()["status"] == status


def test_create_card_rejects_invalid_status():
    c, fid = _client_with_frame()
    r = c.post(
        "/api/cards",
        json={"title": "Bad", "frame_id": fid, "status": "bogus"},
        headers=CSRF,
    )
    assert r.status_code == 400
    assert "invalid status" in r.get_json()["error"].lower()


def test_update_card_changes_status():
    c, fid = _client_with_frame()
    card = c.post(
        "/api/cards",
        json={"title": "T", "frame_id": fid},
        headers=CSRF,
    ).get_json()
    cid = card["id"]

    url = f"/api/cards/{cid}"
    r = c.put(url, json={"status": "in-progress"}, headers=CSRF)
    assert r.status_code == 200
    assert r.get_json()["status"] == "in-progress"


def test_update_card_rejects_invalid_status():
    c, fid = _client_with_frame()
    card = c.post(
        "/api/cards",
        json={"title": "T", "frame_id": fid},
        headers=CSRF,
    ).get_json()
    cid = card["id"]

    r = c.put(f"/api/cards/{cid}", json={"status": "nope"}, headers=CSRF)
    assert r.status_code == 400


def test_status_round_trips_through_disk():
    """Status survives a store restart (load → save → load)."""
    import tempfile

    from src.agent1.models import Frame

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name

    try:
        store = BoardStore(path)
        frame = Frame(id="f1", title="Column")
        store.add_frame(frame)
        card = _make_card("x", frame.id, status="done")
        store.add_card(card, frame.id)

        # Reload from disk.
        store2 = BoardStore(path)
        loaded = store2.get_card("x")
        assert loaded is not None
        assert loaded.status == "done"
    finally:
        import os
        try:
            os.unlink(path)
        except OSError:
            pass


def test_status_in_sync_payload_on_move():
    """A card move enqueues a payload that includes ``status``."""
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    f1 = c.post("/api/frames", json={"title": "To Do"}, headers=CSRF).get_json()
    f2 = c.post("/api/frames", json={"title": "Done"}, headers=CSRF).get_json()
    card = c.post(
        "/api/cards",
        json={"title": "T", "frame_id": f1["id"], "status": "planned"},
        headers=CSRF,
    ).get_json()

    r = c.post(
        f"/api/cards/{card['id']}/move",
        json={"frame_id": f2["id"]},
        headers=CSRF,
    )
    assert r.status_code == 200
    resp_data = r.get_json()
    assert resp_data["status"] == "planned"


# ---- helpers ------------------------------------------------------------

def _make_card(card_id: str, frame_id: str, **kw) -> object:
    """Create a minimal Card-like dict that can be stored."""
    from src.agent1.models import Card, now_iso

    defaults = {
        "id": card_id,
        "title": "T",
        "text": "",
        "frame_id": frame_id,
        "tags": [],
        "system": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    defaults.update(kw)
    return Card(**defaults)
