import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app

CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_app_boots_and_lists_routes():
    app = create_app()
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/frames" in rules
    assert "/api/cards" in rules
    assert "/api/frames/reorder" in rules


def test_index_serves_spa():
    c = _client()
    resp = c.get("/")
    assert resp.status_code == 200
    assert b"Kanban" in resp.data
    assert b"main.js" in resp.data
    assert "text/html" in resp.headers.get("Content-Type", "")


def test_security_headers_applied():
    c = _client()
    resp = c.get("/api/frames")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_csrf_guard_blocks_post_without_header():
    c = _client()
    resp = c.post("/api/frames", json={"title": "To Do"})
    assert resp.status_code == 403


def test_frame_crud_and_card_crud():
    c = _client()

    # create frame
    r = c.post("/api/frames", json={"title": "To Do"}, headers=CSRF)
    assert r.status_code == 201
    frame = r.get_json()
    fid = frame["id"]
    assert frame["title"] == "To Do"

    # list frames
    r = c.get("/api/frames")
    assert r.status_code == 200
    assert any(f["id"] == fid for f in r.get_json())

    # update frame
    r = c.put(f"/api/frames/{fid}", json={"title": "Doing"}, headers=CSRF)
    assert r.status_code == 200
    assert r.get_json()["title"] == "Doing"

    # create card in frame
    r = c.post(
        "/api/cards",
        json={"title": "Write tests", "text": "add boot test", "tags": ["qa"], "frame_id": fid},
        headers=CSRF,
    )
    assert r.status_code == 201
    card = r.get_json()
    cid = card["id"]
    assert card["tags"] == ["qa"]
    assert card["frame_id"] == fid

    # card appears inside frame
    r = c.get(f"/api/frames/{fid}")
    assert cid in r.get_json()["card_ids"]

    # update card
    r = c.put(
        f"/api/cards/{cid}",
        json={"title": "Write more tests", "tags": ["qa", "ci"]},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.get_json()["tags"] == ["qa", "ci"]

    # move card to a non-existent frame -> 404
    r = c.post(f"/api/cards/{cid}/move", json={"frame_id": "nope"}, headers=CSRF)
    assert r.status_code == 400

    # delete card
    r = c.delete(f"/api/cards/{cid}", headers=CSRF)
    assert r.status_code == 204
    r = c.get(f"/api/cards/{cid}")
    assert r.status_code == 404

    # delete frame (cascades)
    r = c.delete(f"/api/frames/{fid}", headers=CSRF)
    assert r.status_code == 204
    r = c.get(f"/api/frames/{fid}")
    assert r.status_code == 404


def test_board_endpoint_returns_frames_with_cards():
    c = _client()
    f = c.post("/api/frames", json={"title": "Done"}, headers=CSRF).get_json()
    cid = c.post(
        "/api/cards",
        json={"title": "Ship", "frame_id": f["id"]},
        headers=CSRF,
    ).get_json()["id"]
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 1
    assert board[0]["title"] == "Done"
    assert board[0]["cards"][0]["id"] == cid


def test_move_card_updates_frame_membership():
    c = _client()
    f1 = c.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    f2 = c.post("/api/frames", json={"title": "B"}, headers=CSRF).get_json()["id"]
    card = c.post(
        "/api/cards", json={"title": "X", "frame_id": f1}, headers=CSRF
    ).get_json()

    r = c.post(
        f"/api/cards/{card['id']}/move",
        json={"frame_id": f2},
        headers=CSRF,
    )
    assert r.status_code == 200

    board = c.get("/api/frames/board").get_json()
    by_id = {fr["id"]: fr for fr in board}
    assert card["id"] not in [c["id"] for c in by_id[f1]["cards"]]
    assert card["id"] in [c["id"] for c in by_id[f2]["cards"]]


def test_bulk_cards_list():
    c = _client()
    f = c.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    c.post("/api/cards", json={"title": "X", "frame_id": f}, headers=CSRF)
    all_cards = c.get("/api/cards").get_json()
    assert len(all_cards) == 1
    assert all_cards[0]["title"] == "X"


def test_move_card_to_explicit_index_reorders_within_frame():
    c = _client()
    f = c.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    c1 = c.post("/api/cards", json={"title": "1", "frame_id": f}, headers=CSRF).get_json()
    c2 = c.post("/api/cards", json={"title": "2", "frame_id": f}, headers=CSRF).get_json()
    c3 = c.post("/api/cards", json={"title": "3", "frame_id": f}, headers=CSRF).get_json()

    # Drag card "1" to the end (index 2) — the same operation DnD performs.
    r = c.post(
        f"/api/cards/{c1['id']}/move",
        json={"frame_id": f, "index": 2},
        headers=CSRF,
    )
    assert r.status_code == 200
    order = [cd["id"] for cd in c.get("/api/frames/board").get_json()[0]["cards"]]
    assert order == [c2["id"], c3["id"], c1["id"]]


def test_frame_reorder():
    c = _client()
    f1 = c.post("/api/frames", json={"title": "A"}, headers=CSRF).get_json()["id"]
    f2 = c.post("/api/frames", json={"title": "B"}, headers=CSRF).get_json()["id"]

    r = c.post("/api/frames/reorder", json={"order": [f2, f1]}, headers=CSRF)
    assert r.status_code == 200

    order = [f["id"] for f in c.get("/api/frames").get_json()]
    assert order == [f2, f1]


def test_input_validation_rejects_overlong_title():
    c = _client()
    long_title = "x" * 1000
    r = c.post("/api/frames", json={"title": long_title}, headers=CSRF)
    assert r.status_code == 400


def test_card_system_property_round_trips():
    c = _client()
    f = c.post("/api/frames", json={"title": "To Do"}, headers=CSRF).get_json()

    # Create a card with a system assignment.
    r = c.post(
        "/api/cards",
        json={"title": "Fix login", "system": "Auth", "frame_id": f["id"]},
        headers=CSRF,
    )
    assert r.status_code == 201
    card = r.get_json()
    cid = card["id"]
    assert card["system"] == "Auth"

    # Cards without a system default to an empty string.
    r = c.post(
        "/api/cards",
        json={"title": "Tidy docs", "frame_id": f["id"]},
        headers=CSRF,
    )
    assert r.status_code == 201
    assert r.get_json()["system"] == ""

    # Update the system on an existing card.
    r = c.put(
        f"/api/cards/{cid}",
        json={"system": "Billing"},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.get_json()["system"] == "Billing"

    # The system is exposed via the board endpoint too.
    board = c.get("/api/frames/board").get_json()
    by_id = {cd["id"]: cd for fr in board for cd in fr["cards"]}
    assert by_id[cid]["system"] == "Billing"


def test_card_system_rejects_overlong_value():
    c = _client()
    f = c.post("/api/frames", json={"title": "To Do"}, headers=CSRF).get_json()
    r = c.post(
        "/api/cards",
        json={"title": "X", "system": "s" * 1000, "frame_id": f["id"]},
        headers=CSRF,
    )
    assert r.status_code == 400

