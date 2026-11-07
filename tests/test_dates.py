"""Created/updated timestamps on cards.

Covers the API semantics (create stamps both dates with the same value; an
update and a move refresh ``updated_at`` only) and persistence (dates
round-trip through the board file; legacy files written before timestamps
existed load with empty strings, meaning "unknown").

Timestamps have second precision, so tests that need to observe a refresh
sleep just over one second.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app
from src.agent1.models import Card, Frame
from src.agent1.store import BoardStore

from conftest import TEST_CSRF_HEADER, TEST_CSRF_TOKEN

CSRF = {"X-Requested-With": "XMLHttpRequest", **TEST_CSRF_HEADER}


def _client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["TEST_CSRF_TOKEN"] = TEST_CSRF_TOKEN
    return app.test_client()


def _parse(value):
    """Parse an ISO 8601 timestamp string."""
    return datetime.fromisoformat(value)


def _mk_frame(client, title="To Do"):
    resp = client.post("/api/frames", json={"title": title}, headers=CSRF)
    assert resp.status_code == 201
    return resp.get_json()["id"]


# ---- API semantics ----------------------------------------------------------

def test_created_and_updated_are_stamped_on_create():
    c = _client()
    fid = _mk_frame(c)
    resp = c.post(
        "/api/cards", json={"frame_id": fid, "title": "Dated"}, headers=CSRF
    )
    assert resp.status_code == 201
    card = resp.get_json()
    created = _parse(card["created_at"])
    updated = _parse(card["updated_at"])
    # Both set at creation and identical (second precision).
    assert created == updated
    # Timestamps are UTC.
    assert created.utcoffset().total_seconds() == 0


def test_update_refreshes_updated_at_and_keeps_created_at():
    c = _client()
    fid = _mk_frame(c)
    card = c.post(
        "/api/cards", json={"frame_id": fid, "title": "A"}, headers=CSRF
    ).get_json()

    time.sleep(1.05)  # second precision: force a new timestamp tick

    resp = c.put(
        f"/api/cards/{card['id']}", json={"title": "B"}, headers=CSRF
    )
    assert resp.status_code == 200
    after = resp.get_json()
    assert _parse(after["updated_at"]) > _parse(card["updated_at"])
    assert after["created_at"] == card["created_at"]


def test_move_refreshes_updated_at():
    c = _client()
    fid1 = _mk_frame(c, "One")
    fid2 = _mk_frame(c, "Two")
    card = c.post(
        "/api/cards", json={"frame_id": fid1, "title": "A"}, headers=CSRF
    ).get_json()

    time.sleep(1.05)  # second precision: force a new timestamp tick

    resp = c.post(
        f"/api/cards/{card['id']}/move",
        json={"frame_id": fid2, "index": 0},
        headers=CSRF,
    )
    assert resp.status_code in (200, 204)
    got = c.get(f"/api/cards/{card['id']}").get_json()
    assert _parse(got["updated_at"]) > _parse(card["updated_at"])
    assert got["created_at"] == card["created_at"]


# ---- Persistence ------------------------------------------------------------

def test_dates_round_trip_through_disk(tmp_path):
    path = tmp_path / "board.json"
    s1 = BoardStore(path=str(path))
    s1.add_frame(Frame(id="f1", title="T", card_ids=[]))
    s1.add_card(
        Card(
            id="c1",
            title="A",
            text="",
            frame_id="f1",
            tags=[],
            created_at="2026-01-02T03:04:05+00:00",
            updated_at="2026-02-03T04:05:06+00:00",
        ),
        "f1",
    )

    s2 = BoardStore(path=str(path))
    card = s2.get_card("c1")
    assert card.created_at == "2026-01-02T03:04:05+00:00"
    assert card.updated_at == "2026-02-03T04:05:06+00:00"


def test_legacy_board_without_dates_loads_as_unknown(tmp_path):
    path = tmp_path / "board.json"
    payload = {
        "frames": [{"id": "f1", "title": "T", "card_ids": ["c1"]}],
        "cards": [
            {
                "id": "c1",
                "title": "Old",
                "text": "",
                "frame_id": "f1",
                "tags": [],
                "system": "",
            }
        ],
    }
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")

    s = BoardStore(path=str(path))
    card = s.get_card("c1")
    assert card.created_at == ""
    assert card.updated_at == ""


def test_to_dict_includes_empty_date_fields_by_default():
    card = Card(id="c1", title="A", text="", frame_id="f1", tags=[])
    d = card.to_dict()
    assert d["created_at"] == ""
    assert d["updated_at"] == ""
