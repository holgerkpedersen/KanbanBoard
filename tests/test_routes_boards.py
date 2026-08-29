"""Tests for the /api/boards blueprint.

These exercise the HTTP layer, including CSRF enforcement, the
path-validation rules, the live-store swap, and the per-board isolation
of mutations.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1 import create_app
from src.agent1.store import BoardStore

CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _client(tmp_path, data_path=None):
    app = create_app(
        data_path=str(data_path) if data_path else None,
        registry_path=str(tmp_path / "boards.json"),
    )
    app.config["TESTING"] = True
    app.config["UPLOADS_DIR"] = str(tmp_path / "uploads")
    return app.test_client(), app


def _write_board(tmp_path, name="board.json", *, frames=2, cards=1):
    p = tmp_path / name
    data = {
        "frames": [
            {"id": f"f{i}", "title": f"Frame {i}", "card_ids": [f"c{i}"]}
            for i in range(frames)
        ],
        "cards": [
            {
                "id": f"c{i}",
                "title": f"Card {i}",
                "text": "",
                "frame_id": f"f{i}",
                "tags": [],
                "system": "",
            }
            for i in range(cards)
        ],
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ---- list / active --------------------------------------------------------


def test_list_boards_initially_empty(tmp_path):
    c, _ = _client(tmp_path)
    r = c.get("/api/boards")
    assert r.status_code == 200
    body = r.get_json()
    assert body["boards"] == []
    assert body["active"] is None


def test_active_endpoint_empty_when_no_board(tmp_path):
    c, _ = _client(tmp_path)
    r = c.get("/api/boards/active")
    assert r.status_code == 200
    assert r.get_json() == {}


# ---- create ---------------------------------------------------------------


def test_create_board_writes_file_and_registers(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/create",
        json={
            "name": "My Plan",
            "folder": str(tmp_path),
            "filename": "plan.json",
        },
        headers=CSRF,
    )
    assert r.status_code == 201, r.data
    entry = r.get_json()
    assert entry["name"] == "My Plan"
    target = Path(entry["path"])
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {"frames": [], "cards": []}
    # The new board is now active.
    active = c.get("/api/boards/active").get_json()
    assert active["id"] == entry["id"]


def test_create_board_rejects_existing_file(tmp_path):
    c, _ = _client(tmp_path)
    target = tmp_path / "exists.json"
    target.write_text("{}", encoding="utf-8")
    r = c.post(
        "/api/boards/create",
        json={"name": "X", "folder": str(tmp_path), "filename": "exists.json"},
        headers=CSRF,
    )
    assert r.status_code == 409


def test_create_board_validates_name_and_filename(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/create",
        json={"name": "", "folder": str(tmp_path), "filename": "ok.json"},
        headers=CSRF,
    )
    assert r.status_code == 400
    r = c.post(
        "/api/boards/create",
        json={"name": "OK", "folder": str(tmp_path), "filename": "no-extension"},
        headers=CSRF,
    )
    assert r.status_code == 400


def test_create_board_requires_csrf(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/create",
        json={"name": "X", "folder": str(tmp_path), "filename": "x.json"},
    )
    assert r.status_code == 403


# ---- open-file (path) ----------------------------------------------------


def test_open_file_by_path_registers_existing_board(tmp_path):
    c, _ = _client(tmp_path)
    src = _write_board(tmp_path, "src.json")
    r = c.post("/api/boards/open-file", json={"path": src}, headers=CSRF)
    assert r.status_code == 201, r.data
    entry = r.get_json()
    assert entry["path"] == src
    # Active now reflects the new file.
    active = c.get("/api/boards/active").get_json()
    assert active["id"] == entry["id"]
    # /api/frames/board now returns the new file's frames.
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 2


def test_open_file_rejects_relative_path(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/open-file", json={"path": "relative.json"}, headers=CSRF
    )
    assert r.status_code == 400


def test_open_file_rejects_non_json_extension(tmp_path):
    c, _ = _client(tmp_path)
    target = tmp_path / "notjson.txt"
    target.write_text("{}", encoding="utf-8")
    r = c.post(
        "/api/boards/open-file", json={"path": str(target)}, headers=CSRF
    )
    assert r.status_code == 400


def test_open_file_rejects_illegal_characters(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/open-file",
        json={"path": str(tmp_path) + "\\ba|d.json"},
        headers=CSRF,
    )
    assert r.status_code == 400


def test_open_file_rejects_invalid_board_json(tmp_path):
    c, _ = _client(tmp_path)
    target = tmp_path / "wrong.json"
    target.write_text("{}", encoding="utf-8")
    r = c.post(
        "/api/boards/open-file", json={"path": str(target)}, headers=CSRF
    )
    assert r.status_code == 400


def test_open_file_rejects_missing_file(tmp_path):
    c, _ = _client(tmp_path)
    target = tmp_path / "ghost.json"
    r = c.post(
        "/api/boards/open-file", json={"path": str(target)}, headers=CSRF
    )
    assert r.status_code == 400


# ---- open-file (content upload, the browser FS-Access-API branch) --------


def test_open_file_with_inline_content_saves_managed_copy(tmp_path):
    c, app = _client(tmp_path)
    content = json.dumps({"frames": [], "cards": []})
    r = c.post(
        "/api/boards/open-file",
        json={"name": "Imported", "content": content},
        headers=CSRF,
    )
    assert r.status_code == 201, r.data
    entry = r.get_json()
    target = Path(entry["path"])
    assert target.exists()
    assert target.parent == Path(app.config["UPLOADS_DIR"])


def test_open_file_with_invalid_content_returns_400(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/open-file",
        json={"name": "Bad", "content": "{ not json"},
        headers=CSRF,
    )
    assert r.status_code == 400


def test_open_file_with_non_board_content_returns_400(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post(
        "/api/boards/open-file",
        json={"name": "X", "content": json.dumps({"foo": 1})},
        headers=CSRF,
    )
    assert r.status_code == 400


# ---- select --------------------------------------------------------------


def test_select_swaps_live_store(tmp_path):
    c, _ = _client(tmp_path)
    # Set up two distinct boards.
    a = c.post(
        "/api/boards/create",
        json={"name": "A", "folder": str(tmp_path), "filename": "a.json"},
        headers=CSRF,
    ).get_json()
    # Add a frame to A.
    c.post("/api/frames", json={"title": "A-frame"}, headers=CSRF)
    # Create B (now active, so /api/frames sees an empty board).
    b = c.post(
        "/api/boards/create",
        json={"name": "B", "folder": str(tmp_path), "filename": "b.json"},
        headers=CSRF,
    ).get_json()
    # B is active -> no frames.
    assert c.get("/api/frames").get_json() == []
    # Switch back to A.
    r = c.post("/api/boards/select", json={"id": a["id"]}, headers=CSRF)
    assert r.status_code == 200
    # A's frame is visible again.
    frames = c.get("/api/frames").get_json()
    assert len(frames) == 1
    assert frames[0]["title"] == "A-frame"


def test_select_unknown_id_returns_404(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/api/boards/select", json={"id": "nope"}, headers=CSRF)
    assert r.status_code == 404


def test_select_requires_csrf(tmp_path):
    c, _ = _client(tmp_path)
    r = c.post("/api/boards/select", json={"id": "x"})
    assert r.status_code == 403


# ---- rename --------------------------------------------------------------


def test_rename_board(tmp_path):
    c, _ = _client(tmp_path)
    a = c.post(
        "/api/boards/create",
        json={"name": "Old", "folder": str(tmp_path), "filename": "x.json"},
        headers=CSRF,
    ).get_json()
    r = c.put(f"/api/boards/{a['id']}", json={"name": "New"}, headers=CSRF)
    assert r.status_code == 200
    assert r.get_json()["name"] == "New"
    # Visible in the listing.
    listed = c.get("/api/boards").get_json()["boards"]
    assert listed[0]["name"] == "New"


# ---- duplicate -----------------------------------------------------------


def test_duplicate_copies_file_and_registers(tmp_path):
    c, _ = _client(tmp_path)
    src = _write_board(tmp_path, "src.json", frames=1, cards=1)
    a = c.post(
        "/api/boards/open-file", json={"path": src}, headers=CSRF
    ).get_json()
    r = c.post(
        f"/api/boards/{a['id']}/duplicate",
        json={"new_name": "Copy", "dest_path": str(tmp_path / "copy.json")},
        headers=CSRF,
    )
    assert r.status_code == 201, r.data
    new = r.get_json()
    assert Path(new["path"]).exists()
    assert json.loads(Path(new["path"]).read_text(encoding="utf-8")) == json.loads(
        Path(src).read_text(encoding="utf-8")
    )
    assert new["name"] == "Copy"


def test_duplicate_rejects_existing_destination(tmp_path):
    c, _ = _client(tmp_path)
    src = _write_board(tmp_path, "src.json")
    a = c.post(
        "/api/boards/open-file", json={"path": src}, headers=CSRF
    ).get_json()
    target = tmp_path / "exists.json"
    target.write_text("{}", encoding="utf-8")
    r = c.post(
        f"/api/boards/{a['id']}/duplicate",
        json={"new_name": "X", "dest_path": str(target)},
        headers=CSRF,
    )
    assert r.status_code == 409


# ---- delete --------------------------------------------------------------


def test_delete_unregisters_keeps_file_by_default(tmp_path):
    c, _ = _client(tmp_path)
    a = c.post(
        "/api/boards/create",
        json={"name": "X", "folder": str(tmp_path), "filename": "x.json"},
        headers=CSRF,
    ).get_json()
    target = Path(a["path"])
    assert target.exists()
    r = c.delete(f"/api/boards/{a['id']}", headers=CSRF)
    assert r.status_code == 204
    assert target.exists()  # file not deleted
    assert c.get("/api/boards").get_json()["boards"] == []


def test_delete_with_delete_file_true_removes_file(tmp_path):
    c, _ = _client(tmp_path)
    a = c.post(
        "/api/boards/create",
        json={"name": "X", "folder": str(tmp_path), "filename": "x.json"},
        headers=CSRF,
    ).get_json()
    target = Path(a["path"])
    assert target.exists()
    r = c.delete(
        f"/api/boards/{a['id']}?delete_file=true", headers=CSRF
    )
    assert r.status_code == 204
    assert not target.exists()


def test_delete_active_board_falls_back_to_next_or_empty(tmp_path):
    c, _ = _client(tmp_path)
    a = c.post(
        "/api/boards/create",
        json={"name": "A", "folder": str(tmp_path), "filename": "a.json"},
        headers=CSRF,
    ).get_json()
    b = c.post(
        "/api/boards/create",
        json={"name": "B", "folder": str(tmp_path), "filename": "b.json"},
        headers=CSRF,
    ).get_json()
    # B is the current active.
    r = c.delete(f"/api/boards/{b['id']}", headers=CSRF)
    assert r.status_code == 204
    # A becomes active.
    active = c.get("/api/boards/active").get_json()
    assert active["id"] == a["id"]


# ---- CSRF parity for all write endpoints --------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/boards/create"),
        ("POST", "/api/boards/open-file"),
        ("POST", "/api/boards/select"),
    ],
)
def test_writes_require_csrf_header(tmp_path, method, path):
    c, _ = _client(tmp_path)
    r = c.open(path, method=method, json={})
    assert r.status_code == 403


# ---- security headers parity ---------------------------------------------


def test_boards_endpoints_carry_security_headers(tmp_path):
    c, _ = _client(tmp_path)
    r = c.get("/api/boards")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in r.headers.get("Content-Security-Policy", "")
