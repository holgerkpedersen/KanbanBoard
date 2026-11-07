"""Regression tests for GET /api/cards/linked (the integrated-card view).

The endpoint is a read-only projection of the shared ``.card_id_map.json``:
a card id is any *key* whose value starts with ``iss-`` (Agent1's issue-id
prefix).  The map stores every pair twice (issue→card and card→issue), so
the reverse keys must NOT leak into the response, and unrelated pairs in the
same file must be ignored.

The endpoint must never 500: a missing, unreadable or malformed map degrades
to ``{"card_ids": []}`` — "nothing is integrated" — because a stale map on
a dev machine must not take the whole board UI down.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app
from src.agent1 import kanban_sync as ks


def _client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _isolate_id_map(monkeypatch, tmp_path):
    """Point the sync module's map path at a private tmp file."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ks, "DATA_DIR", data)
    map_path = data / ".card_id_map.json"
    monkeypatch.setattr(ks, "CARD_ID_MAP_PATH", map_path)
    return map_path


def _write_map(map_path, pairs):
    map_path.write_text(json.dumps(pairs), encoding="utf-8")


def test_linked_returns_only_issue_linked_card_ids(monkeypatch, tmp_path):
    """Exactly the card ids mapped to Agent1 issues — forward keys only."""
    map_path = _isolate_id_map(monkeypatch, tmp_path)
    _write_map(map_path, {
        # Forward pair: issue -> card.
        "iss-general-kanban_aaa": "card-aaa",
        # Reverse pair of the same link: card -> issue.  This key IS a linked
        # card (its value starts with iss-), so it belongs in the answer.
        "card-aaa": "iss-general-kanban_aaa",
        # Second full link.
        "iss-general-kanban_bbb": "card-bbb",
        "card-bbb": "iss-general-kanban_bbb",
        # Unrelated pair (no iss- prefix on either side) — must not appear.
        "some-key": "some-value",
    })
    resp = _client().get("/api/cards/linked")
    assert resp.status_code == 200
    assert resp.get_json() == {"card_ids": ["card-aaa", "card-bbb"]}


def test_linked_empty_when_map_missing(monkeypatch, tmp_path):
    """No map file at all -> nothing is linked (never an error)."""
    _isolate_id_map(monkeypatch, tmp_path)
    resp = _client().get("/api/cards/linked")
    assert resp.status_code == 200
    assert resp.get_json() == {"card_ids": []}


def test_linked_empty_when_map_malformed(monkeypatch, tmp_path):
    """A corrupt map must degrade to an empty list, not a 500."""
    map_path = _isolate_id_map(monkeypatch, tmp_path)
    map_path.write_text("{ this is not json", encoding="utf-8")
    resp = _client().get("/api/cards/linked")
    assert resp.status_code == 200
    assert resp.get_json() == {"card_ids": []}


def test_linked_empty_when_map_is_a_list(monkeypatch, tmp_path):
    """Valid JSON of the wrong shape (list) is also a safe empty answer."""
    map_path = _isolate_id_map(monkeypatch, tmp_path)
    map_path.write_text(json.dumps(["iss-x", "card-x"]), encoding="utf-8")
    resp = _client().get("/api/cards/linked")
    assert resp.status_code == 200
    assert resp.get_json() == {"card_ids": []}


def test_linked_is_read_only(monkeypatch, tmp_path):
    """GET must not create, rewrite or delete the map file."""
    map_path = _isolate_id_map(monkeypatch, tmp_path)
    _write_map(map_path, {"iss-general-kanban_ccc": "card-ccc",
                          "card-ccc": "iss-general-kanban_ccc"})
    before = map_path.read_text(encoding="utf-8")
    resp = _client().get("/api/cards/linked")
    assert resp.get_json() == {"card_ids": ["card-ccc"]}
    assert map_path.exists()
    assert map_path.read_text(encoding="utf-8") == before
